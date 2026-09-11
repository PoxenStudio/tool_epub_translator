"""
EPUB 翻译 —— MyBooks Toolbox 外部工具后端

用 `epub_translator`（base conda 环境提供，见 README"依赖"一节）把书库里选中的 EPUB
翻译成目标语言，结果支持下载或直接生成一本新书。

设计依据：`doc/EPUB_TRANSLATOR_IMPLEMENTATION_REVIEW.md`（已完成源码交叉核对）。

这个文件只做两件事：`EpubTranslator`（CoreAPI 看到的业务对象）和 5 个 HTTP handler
（manifest.json 的 api_routes 声明）。具体怎么校验配置、怎么测连接、怎么真正跑一次
翻译，分别在 config.py / connection.py / translation.py 里，job.json 的 schema 和
原子读写在 job_store.py 里——想改哪一块就去对应的文件，不用在一个大文件里找。

CoreAPI 命名空间速查（详见 mybooks/mybooks `webserver/toolbox/core_api.py`）：
    self.api.calibre   书库读写：search_books / get_data_as_dict / format_abspath /
                        import_book / cover ...
    self.api.db        应用数据库：create_item（把新书登记为某用户名下的收藏）
    self.api.tasks     后台任务：create_task / update_progress / complete_task
                        （只用于在宿主任务面板里展示进度，不作为"只能一个任务"的判断依据——
                        那份状态只在内存里，重启/刷新即丢，见 job_store.py 顶部说明）
    self.api.messages  站内消息：任务结束时发一条通知
    self.api.storage   工具专属持久配置：get_config / set_config（只管 config.json 一个
                        文件；job.json 和 jobs/ 目录由本工具在 job_store.py 里自己管理）
"""
import json
import logging
import os
from typing import List, Optional

from webserver.i18n import _
from webserver.services import AsyncService
from webserver.handlers.base import BaseHandler, js, auth
from webserver.toolbox.base_tool import BaseTool

from . import job_store, translation
from .config import ConfigError, DEFAULT_CONFIG, clamp, present_config, validate_config
from .connection import test_connection as _test_connection
from .job_store import JobStore


class EpubTranslator(BaseTool):
    service_item_name = "EPUB 翻译"

    @staticmethod
    def info() -> dict:
        return {
            "tool_id": "epub_translator",
            "name": "EPUB 翻译",
            "description": "使用 LLM 翻译 EPUB 电子书，支持书库选书或本地上传，输出下载或生成新书",
            "revision": "0.1.0",
            "author": "Horky",
            "publish_date": "2026-09-08",
            "repo_url": "https://github.com/horky/tool_translation",
        }

    # -- 数据目录 / 任务存储 --

    def _data_dir(self) -> str:
        d = os.path.join(self.TOOL_DATA_ROOT, self.tool_id())
        os.makedirs(d, exist_ok=True)
        return d

    def _store(self) -> JobStore:
        return JobStore(self._data_dir())

    # -- 配置 --

    @AsyncService.register_function
    def get_config(self) -> dict:
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(self.api.storage.get_config() or {})
        return present_config(cfg)

    @AsyncService.register_function
    def save_config(self, patch: dict) -> dict:
        existing = self.api.storage.get_config() or {}
        merged = validate_config(patch, {**DEFAULT_CONFIG, **existing})
        api_key = patch.get("api_key")
        if api_key:
            merged["api_key"] = str(api_key).strip()
        elif existing.get("api_key"):
            merged["api_key"] = existing["api_key"]
        self.api.storage.set_config(merged)
        return present_config(merged)

    # -- 测试连接 --

    def test_connection(self, api_url: str, api_key: str, model: str) -> dict:
        """api_key 为空时回退到已保存的配置，其余校验/请求逻辑见 connection.py。"""
        if not api_key:
            api_key = (self.api.storage.get_config() or {}).get("api_key", "")
        return _test_connection(api_url, api_key, model)

    # -- 书库搜索 --

    @AsyncService.register_function
    def search_books(self, query: str, limit: int = 20) -> List[dict]:
        limit = clamp(limit, 1, 50, 20)
        query = (query or "").strip()
        calibre_query = query if query else "formats:EPUB"
        try:
            books = self.api.calibre.search_books(calibre_query, max_results=max(limit * 4, 40))
        except Exception as err:
            logging.warning("[epub_translator] search_books failed: %s", err)
            return []
        results = []
        for book in books:
            fmts = [f['format'].upper() for f in (book.get("files") or [])]
            if "EPUB" not in fmts:
                continue
            results.append({
                "book_id": book.get("id"),
                "title": book.get("title") or "",
                "authors": book.get("authors") or [],
                "thumb": book.get("thumb", ""),
            })
            if len(results) >= limit:
                break
        return results

    # -- 任务状态 --

    @AsyncService.register_function
    def get_status(self) -> dict:
        store = self._store()
        store.clear_if_expired_result()
        return job_store.public_job(store.read())

    @AsyncService.register_function
    def cancel(self, job_id: str) -> dict:
        """请求取消运行中的任务。epub_translator 没有取消 API，这是靠
        `translation.run()` 里 `on_progress` 协作检查实现的，生效有一次回调的延迟，
        见 job_store.py 顶部说明。没有匹配的运行中任务时报错，交给前端提示。"""
        store = self._store()
        if not store.request_cancel(job_id):
            raise ConfigError(_("没有可取消的运行中任务"))
        return job_store.public_job(store.read())

    # -- 启动任务 --

    @AsyncService.register_function
    def start_from_library(self, user_id: int, book_id: int, output_mode: str,
                            title: Optional[str] = None) -> dict:
        cfg = self.api.storage.get_config() or {}
        if not cfg.get("api_key") or not cfg.get("api_url") or not cfg.get("model"):
            raise ConfigError(_("请先在配置区填写并保存 LLM 配置"))

        books = self.api.calibre.get_data_as_dict([book_id])
        if not books:
            raise ConfigError(_("书籍不存在：ID=%d") % book_id)
        book = books[0]
        fmts = [f.upper() for f in (book.get("available_formats") or [])]
        if "EPUB" not in fmts:
            raise ConfigError(_("该书籍没有 EPUB 格式，无法翻译"))
        source_path = self.api.calibre.format_abspath(book_id, "EPUB")
        if not source_path or not os.path.exists(source_path):
            raise ConfigError(_("找不到 EPUB 文件，可能已被移除"))

        store = self._store()
        job_id = job_store.new_job_id()
        job_dir = store.job_dir(job_id)
        # 书库源文件属于 Calibre：不复制大文件，直接把绝对路径记下来只读使用
        job = {
            "job_id": job_id,
            "status": job_store.STATUS_RUNNING,
            "stage": "queued",
            "progress": 0,
            "source": job_store.SOURCE_LIBRARY,
            "book_id": book_id,
            "book_title": book.get("title") or "",
            "output_mode": output_mode,
            "target_language": cfg.get("target_language", DEFAULT_CONFIG["target_language"]),
            "tokens": job_store.empty_tokens(),
            "error": None,
            "new_book": None,
            "result_ready": False,
            "result_expired": False,
            "result_downloaded": False,
        }
        started, current = store.try_start(job)
        if not started:
            store.remove_job_dir(job_id)
            return job_store.public_job(current)

        task_id = self.api.tasks.create_task(progress_data={"status": "starting", "book_id": book_id})
        self._run_translation(
            job_id=job_id, job_dir=job_dir, source_path=source_path,
            output_mode=output_mode, user_id=user_id, task_id=task_id,
            book_title=book.get("title") or "", authors=book.get("authors") or [],
            override_title=title, book_id=book_id,
        )
        return job_store.public_job(store.read())

    @AsyncService.register_service
    def _run_translation(self, job_id, job_dir, source_path, output_mode,
                          user_id, task_id, book_title, authors, book_id,
                          override_title=None):
        # `register_service` 用"每个 (类, 方法) 一条持久后台线程 + 队列"机制把这一整个
        # 调用挪到 Tornado IOLoop 之外执行；是否允许"入队"由 start_from_library() 里的
        # try_start() 原子把关，所以队列里任何时候最多只有一个待执行项。真正的翻译逻辑
        # 在 translation.run() 里，这里只负责准备 store/cfg 再转发过去。
        translation.run(
            self.api, self._store(), job_id, job_dir, source_path, output_mode,
            user_id, task_id, book_title, authors, book_id,
            self.api.storage.get_config() or {}, override_title,
        )

    # -- 结果交付 --

    @AsyncService.register_function
    def get_result_info(self) -> dict:
        job = self._store().read()
        if not job:
            raise ConfigError(_("没有任务记录"))
        if job.get("status") != job_store.STATUS_COMPLETED:
            raise ConfigError(_("任务尚未完成"))
        if job.get("output_mode") == job_store.OUTPUT_NEW_BOOK:
            return {"output_mode": job_store.OUTPUT_NEW_BOOK, "new_book": job.get("new_book")}
        if not job.get("result_ready"):
            raise ConfigError(_("结果已过期或已被清理"))
        path = os.path.join(self._store().job_dir(job["job_id"]), "translated.epub")
        if not os.path.exists(path):
            raise ConfigError(_("结果文件不存在"))
        return {"output_mode": job_store.OUTPUT_DOWNLOAD, "path": path, "job_id": job["job_id"],
                "filename": translation.safe_download_name(job.get("book_title"))}

    @AsyncService.register_function
    def mark_downloaded(self, job_id: str) -> None:
        store = self._store()
        job = store.read()
        if not job or job.get("job_id") != job_id:
            return
        store.remove_job_dir(job_id)
        store.update(job_id, result_ready=False, result_downloaded=True)


# ---------------------------------------------------------------------------
# HTTP handlers（manifest.json 的 api_routes 声明，见该文件）


class ConfigHandler(BaseHandler):
    @js
    @auth
    def get(self):
        return {"err": "ok", "data": EpubTranslator().get_config()}

    @js
    @auth
    def post(self):
        try:
            patch = json.loads(self.request.body or b"{}")
        except Exception:
            return {"err": "params.invalid", "msg": _("请求体不是合法 JSON")}
        try:
            data = EpubTranslator().save_config(patch)
        except ConfigError as err:
            return {"err": "config.invalid", "msg": str(err)}
        return {"err": "ok", "data": data, "msg": _("配置已保存")}


class TestConnectionHandler(BaseHandler):
    @js
    @auth
    def post(self):
        try:
            body = json.loads(self.request.body or b"{}")
        except Exception:
            return {"err": "params.invalid", "msg": _("请求体不是合法 JSON")}
        result = EpubTranslator().test_connection(
            api_url=body.get("api_url", ""),
            api_key=body.get("api_key", ""),
            model=body.get("model", ""),
        )
        if result["ok"]:
            return {"err": "ok", "msg": result["message"], "data": result.get("details", {})}
        return {"err": "test.failed", "msg": result["message"], "data": result.get("details", {})}


class BooksHandler(BaseHandler):
    @js
    @auth
    def get(self):
        query = self.get_argument("q", "")
        limit = self.get_argument("limit", "20")
        data = EpubTranslator().search_books(query, limit)
        return {"err": "ok", "data": data}


class StatusHandler(BaseHandler):
    @js
    @auth
    def get(self):
        return {"err": "ok", "data": EpubTranslator().get_status()}


class TranslateHandler(BaseHandler):
    @js
    @auth
    def post(self):
        user_id = self.user_id()
        try:
            body = json.loads(self.request.body or b"{}")
        except Exception:
            return {"err": "params.invalid", "msg": _("请求体不是合法 JSON")}
        book_id = body.get("book_id")
        output_mode = body.get("output_mode", job_store.OUTPUT_DOWNLOAD)
        title = body.get("title") or None
        if not book_id:
            return {"err": "params.missing", "msg": _("缺少 book_id")}
        if output_mode not in (job_store.OUTPUT_DOWNLOAD, job_store.OUTPUT_NEW_BOOK):
            return {"err": "params.invalid", "msg": _("output_mode 只能是 download 或 new_book")}
        try:
            data = EpubTranslator().start_from_library(user_id, int(book_id), output_mode, title)
        except ConfigError as err:
            return {"err": "translate.invalid", "msg": str(err)}
        return {"err": "ok", "data": data}


class CancelHandler(BaseHandler):
    @js
    @auth
    def post(self):
        try:
            body = json.loads(self.request.body or b"{}")
        except Exception:
            return {"err": "params.invalid", "msg": _("请求体不是合法 JSON")}
        job_id = body.get("job_id")
        if not job_id:
            return {"err": "params.missing", "msg": _("缺少 job_id")}
        try:
            data = EpubTranslator().cancel(job_id)
        except ConfigError as err:
            return {"err": "cancel.invalid", "msg": str(err)}
        return {"err": "ok", "data": data}


class ResultHandler(BaseHandler):
    """下载翻译结果的二进制响应，不能走 `@js`（它总是把返回值当 JSON 写出去），所以这里
    手动做登录检查，不复用 `auth` 装饰器——`auth` 未登录时只是 `return` 一个 dict，脱离
    `@js` 就没人把它序列化/finish 掉，未登录请求会变成一个空的 200 响应而不是明确的
    401，等于把登录检查悄悄短路了。
    """

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.finish({"err": "user.need_login", "msg": _("请先登录")})
            return
        try:
            info = EpubTranslator().get_result_info()
        except ConfigError as err:
            self.set_status(400)
            self.finish({"err": "result.unavailable", "msg": str(err)})
            return
        if info["output_mode"] == job_store.OUTPUT_NEW_BOOK:
            self.set_header("Content-Type", "application/json")
            self.finish({"err": "ok", "data": {"new_book": info["new_book"]}})
            return
        path = info["path"]
        job_id = info["job_id"]
        self.set_header("Content-Type", "application/epub+zip")
        self.set_header(
            "Content-Disposition",
            'attachment; filename="%s"' % info["filename"],
        )
        with open(path, "rb") as f:
            self.write(f.read())
        self.finish()
        if job_id:
            EpubTranslator().mark_downloaded(job_id)

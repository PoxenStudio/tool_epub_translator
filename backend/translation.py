"""
执行一次翻译任务：起 LLM、跑 epub_translator.translate()、把进度写回 job_store、
失败处理、生成新书、清理临时文件。

从 EpubTranslator._run_translation 里抽出来，函数只依赖显式传入的参数（`api` /
`store` / `cfg` ...），不依赖 BaseTool 实例——这样"一次翻译具体做了什么"可以脱离
Tornado handler / AsyncService 队列机制单独阅读。真正跑在后台线程里的调用方是
tool.py 的 `EpubTranslator._run_translation`。
"""
import logging
import os
import re
import threading
import zipfile

from webserver.i18n import _

from . import job_store
from .config import DEFAULT_CONFIG, LANGUAGE_TO_CALIBRE_CODE


class _JobCancelled(Exception):
    """`on_progress` 发现取消标志被置位时抛出，用来提前跳出 epub_translator.translate()
    的内部循环——epub_translator 本身不认识这个异常，纯粹是我们借它的回调钩子实现的
    协作式取消，见 job_store.py 顶部说明。"""


def run(api, store, job_id, job_dir, source_path, output_mode,
        user_id, task_id, book_title, authors, book_id, cfg, override_title=None):
    """job_store 里已经有一条 status=running/stage=queued 的记录；这里把它跑完。"""
    target_path = os.path.join(job_dir, "translated.epub")
    error_message = None
    cancelled = False
    seen_usage = threading.Event()

    try:
        from epub_translator import LLM, SubmitKind, translate
    except ImportError as err:
        logging.error("[epub_translator] epub_translator package not importable: %s", err)
        _fail_job(api, store, job_id, task_id, user_id,
                   _("翻译依赖未就绪：base conda 环境里没有安装 epub_translator，请联系管理员"))
        _cleanup(None)
        return

    try:
        llm = LLM(
            key=cfg.get("api_key", ""),
            url=cfg.get("api_url", ""),
            model=cfg.get("model", ""),
            token_encoding=cfg.get("token_encoding", DEFAULT_CONFIG["token_encoding"]),
            timeout=float(cfg.get("timeout", DEFAULT_CONFIG["timeout"])),
            retry_times=int(cfg.get("retry_times", DEFAULT_CONFIG["retry_times"])),
            log_dir_path=job_dir,
        )

        def on_progress(fraction: float) -> None:
            # 协作式取消：epub_translator 自己不提供取消 API，只能借这个回调，每次它被
            # 调用（每章一次）时看一眼有没有人喊了取消，有就直接抛出去跳出 translate()。
            if store.is_cancel_requested():
                raise _JobCancelled()
            pct = max(0, min(100, int(round(fraction * 100))))
            if llm.total_tokens > 0:
                seen_usage.set()
            tokens = _tokens_snapshot(llm, seen_usage)
            store.update(job_id, progress=pct, stage="translating", tokens=tokens)
            api.tasks.update_progress(task_id, pct, progress_data={
                "status": "translating", "book_title": book_title, "tokens": tokens,
            })

        store.update(job_id, status=job_store.STATUS_RUNNING, stage="translating")
        translate(
            source_path=source_path,
            target_path=target_path,
            target_language=cfg.get("target_language", DEFAULT_CONFIG["target_language"]),
            submit=SubmitKind[cfg.get("submit_kind", DEFAULT_CONFIG["submit_kind"])],
            user_prompt=(cfg.get("user_prompt") or None),
            max_group_tokens=int(cfg.get("max_group_tokens", DEFAULT_CONFIG["max_group_tokens"])),
            concurrency=1,
            llm=llm,
            on_progress=on_progress,
        )

        if not os.path.exists(target_path) or not zipfile.is_zipfile(target_path):
            raise RuntimeError(_("翻译未生成有效的 EPUB 文件"))

        final_tokens = _tokens_snapshot(llm, seen_usage)
        new_book_info = None
        if output_mode == job_store.OUTPUT_NEW_BOOK:
            new_book_info = _create_new_book(
                api, target_path, book_title, authors, book_id, user_id, cfg, override_title,
            )
            store.update(
                job_id, status=job_store.STATUS_COMPLETED, progress=100, stage="done",
                tokens=final_tokens, new_book=new_book_info, result_ready=False,
            )
        else:
            store.update(
                job_id, status=job_store.STATUS_COMPLETED, progress=100, stage="done",
                tokens=final_tokens, result_ready=True,
            )

        api.tasks.complete_task(task_id)
        api.messages.send_message(user_id, _("EPUB《%s》翻译完成") % book_title, status="success")
    except _JobCancelled:
        logging.info("[epub_translator] translation cancelled by user: job_id=%s", job_id)
        cancelled = True
        store.update(job_id, status=job_store.STATUS_CANCELLED, stage="cancelled", error=None)
        try:
            api.tasks.complete_task(task_id)
        except Exception:
            pass
        try:
            api.messages.send_message(user_id, _("EPUB《%s》翻译已取消") % book_title, status="info")
        except Exception:
            pass
    except Exception as err:
        logging.exception("[epub_translator] translation failed: job_id=%s", job_id)
        error_message = str(err) or err.__class__.__name__
        _fail_job(api, store, job_id, task_id, user_id, error_message, book_title)
    finally:
        # 书库源文件不清理（Calibre 管理），只清理翻译产物（失败/取消时产物不完整，或者
        # 生成新书模式下产物已经被导入 Calibre，原地这份临时文件也用不上了）
        extra = target_path if (
            error_message or cancelled or output_mode == job_store.OUTPUT_NEW_BOOK
        ) else None
        _cleanup(extra)


def safe_download_name(book_title: str) -> str:
    name = re.sub(r"[^\w\-. 一-鿿]", "_", (book_title or "translated")).strip() or "translated"
    return name[:100] + ".epub"


def _tokens_snapshot(llm, seen_usage: threading.Event) -> dict:
    return {
        "input": llm.input_tokens,
        "input_cache": llm.input_cache_tokens,
        "output": llm.output_tokens,
        "total": llm.total_tokens,
        "available": seen_usage.is_set(),
    }


def _create_new_book(api, target_path, book_title, authors, book_id, user_id, cfg, override_title) -> dict:
    target_language = cfg.get("target_language")
    new_title = override_title or "%s [%s]" % (book_title, target_language)
    clean_title = api.utils.strip(new_title)
    clean_authors = [api.utils.strip(a) for a in (authors or [])] or [_("未知作者")]
    mi = api.calibre.new_metadata(clean_title, clean_authors)
    mi.title_sort = api.utils.get_title_sort(mi.title)
    code = LANGUAGE_TO_CALIBRE_CODE.get(target_language or "")
    if code:
        mi.languages = [code]

    new_book_id = api.calibre.import_book(mi, [target_path])
    if new_book_id is None:
        raise RuntimeError(_("导入翻译结果失败，Calibre 未返回书籍 ID"))
    try:
        api.db.create_item(new_book_id, user_id)
    except Exception as err:
        logging.warning("[epub_translator] Failed to create Item for book_id=%s: %s", new_book_id, err)

    source_book_cover = api.calibre.cover(book_id)
    if source_book_cover:
        api.calibre.set_cover(new_book_id, source_book_cover)

    return {
        "book_id": new_book_id,
        "title": new_title,
        "authors": authors,
        "url": "/book/%d" % new_book_id,
    }


def _fail_job(api, store, job_id, task_id, user_id, message, book_title=""):
    store.update(job_id, status=job_store.STATUS_FAILED, error=message, stage="failed")
    try:
        api.tasks.complete_task(task_id, error_message=message)
    except Exception:
        pass
    try:
        api.messages.send_message(
            user_id, _("EPUB《%s》翻译失败：%s") % (book_title or "", message), status="error",
        )
    except Exception:
        pass


def _cleanup(extra_path):
    """清理翻译产物文件（target_path），source_path 由 Calibre 管理不动。"""
    if extra_path and os.path.exists(extra_path):
        try:
            os.remove(extra_path)
        except OSError as err:
            logging.warning("[epub_translator] Failed to remove file %s: %s", extra_path, err)

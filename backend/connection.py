"""
测试 LLM 连接是否可用 —— 发一个最小的 chat.completions 请求验证 URL/Key/Model。

单独成一个模块是因为它只依赖 openai SDK，跟"怎么校验配置字段"（config.py）或
"怎么跑一次完整翻译"（translation.py）都没什么关系。
"""
from webserver.i18n import _

# 比正式翻译任务短得多：只是验证可达性，不该让用户等太久
TEST_CONNECTION_TIMEOUT = 15


def test_connection(api_url: str, api_key: str, model: str) -> dict:
    """返回 {ok: bool, message: str, details: dict}。

    api_key 由调用方决定：通常是"表单里填了就用表单的，没填就回退到已保存的"，这个
    取舍逻辑留给调用方（EpubTranslator.test_connection），这里只管拿到 key 之后怎么测。
    """
    api_url = str(api_url or "").strip()
    api_key = str(api_key or "").strip()
    model = str(model or "").strip()

    if not api_key:
        return {"ok": False, "message": _("API Key 未填写"), "details": {}}
    if not api_url:
        return {"ok": False, "message": _("API URL 未填写"), "details": {}}
    if not model:
        return {"ok": False, "message": _("模型名称未填写"), "details": {}}
    if not api_url.startswith("http://") and not api_url.startswith("https://"):
        return {"ok": False, "message": _("API URL 必须以 http:// 或 https:// 开头"), "details": {}}

    details = {"api_url": api_url, "model": model}

    try:
        from openai import OpenAI
    except ImportError:
        return {"ok": False, "message": _("后端缺少 openai SDK，无法测试"), "details": details}

    try:
        client = OpenAI(base_url=api_url, api_key=api_key, timeout=TEST_CONNECTION_TIMEOUT)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
        )
        reply = ""
        try:
            reply = resp.choices[0].message.content or ""
        except Exception:
            pass
        return {
            "ok": True,
            "message": _("连接成功，模型返回：%s") % (reply.strip() or "(OK)"),
            "details": details,
        }
    except Exception as err:
        err_str = str(err) or err.__class__.__name__
        return {
            "ok": False,
            "message": _("连接失败：%s") % err_str,
            "details": {**details, "error": err_str},
        }

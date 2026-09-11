"""
LLM 配置：字段定义、校验、对前端的展示形态。

从 tool.py 里单独拆出来，是因为"配置长什么样、怎么校验"是一块自成一体、可以脱离
任务调度逻辑单独阅读的内容——想改校验规则或者加一个配置项，只需要看这一个文件。
"""
from webserver.i18n import _


class ConfigError(ValueError):
    """配置或请求参数不合法，HTTP handler 捕获后转成 4xx 响应。"""


# 用户可编辑、会被持久化到 config.json 的字段（api_key 单独处理，见 validate_config）
CONFIG_FIELDS = (
    "api_url", "model", "token_encoding", "target_language",
    "submit_kind", "user_prompt", "max_group_tokens", "timeout", "retry_times",
)

DEFAULT_CONFIG = {
    "api_url": "",
    "model": "",
    "token_encoding": "o200k_base",
    "target_language": "Simplified Chinese",
    "submit_kind": "APPEND_BLOCK",
    "user_prompt": "",
    "max_group_tokens": 2600,
    "timeout": 120,
    "retry_times": 5,
}

# 与 epub_translator.xml_translator.SubmitKind 的取值保持一致
SUBMIT_KINDS = ("REPLACE", "APPEND_TEXT", "APPEND_BLOCK")

# 与 epub_translator.translation.language 模块导出的常量值保持一致（前端下拉框据此渲染）
TARGET_LANGUAGES = (
    "Simplified Chinese", "Traditional Chinese", "English", "Japanese", "Korean",
    "Spanish", "French", "German", "Portuguese", "Russian", "Italian", "Arabic",
    "Hindi", "Dutch", "Polish", "Turkish", "Vietnamese", "Thai", "Indonesian",
    "Swedish", "Danish", "Norwegian", "Finnish",
)

# 目标语言 -> Calibre ISO 639-2/T 语言代码，用于生成新书时设置书籍语言字段；未覆盖的语言
# 保留原书语言，不强行猜测。translation.py 生成新书时用到。
LANGUAGE_TO_CALIBRE_CODE = {
    "Simplified Chinese": "zho", "Traditional Chinese": "zho", "English": "eng",
    "Japanese": "jpn", "Korean": "kor", "Spanish": "spa", "French": "fra",
    "German": "deu", "Portuguese": "por", "Russian": "rus", "Italian": "ita",
    "Arabic": "ara", "Hindi": "hin", "Dutch": "nld", "Polish": "pol",
    "Turkish": "tur", "Vietnamese": "vie", "Thai": "tha", "Indonesian": "ind",
    "Swedish": "swe", "Danish": "dan", "Norwegian": "nor", "Finnish": "fin",
}


def clamp(value, lo, hi, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def validate_config(patch: dict, base: dict) -> dict:
    """把 `patch` 合并进 `base`，校验后返回新的完整配置（不含 api_key，由调用方单独处理）。"""
    merged = dict(base)
    for key in CONFIG_FIELDS:
        if key in patch and patch[key] is not None:
            merged[key] = patch[key]

    api_url = str(merged.get("api_url") or "").strip()
    if not api_url.startswith("http://") and not api_url.startswith("https://"):
        raise ConfigError(_("API URL 必须以 http:// 或 https:// 开头"))
    merged["api_url"] = api_url

    model = str(merged.get("model") or "").strip()
    if not model:
        raise ConfigError(_("模型名称不能为空"))
    merged["model"] = model[:200]

    token_encoding = str(merged.get("token_encoding") or "").strip()
    if not token_encoding:
        raise ConfigError(_("token encoding 不能为空"))
    merged["token_encoding"] = token_encoding[:100]

    target_language = str(merged.get("target_language") or "").strip()
    if target_language not in TARGET_LANGUAGES:
        raise ConfigError(_("不支持的目标语言：%s") % target_language)
    merged["target_language"] = target_language

    submit_kind = str(merged.get("submit_kind") or "").strip().upper()
    if submit_kind not in SUBMIT_KINDS:
        raise ConfigError(_("不支持的翻译模式：%s") % submit_kind)
    merged["submit_kind"] = submit_kind

    merged["user_prompt"] = str(merged.get("user_prompt") or "")[:4000]
    merged["max_group_tokens"] = clamp(merged.get("max_group_tokens"), 200, 8000, 2600)
    merged["timeout"] = clamp(merged.get("timeout"), 10, 600, 120)
    merged["retry_times"] = clamp(merged.get("retry_times"), 0, 10, 5)
    return merged


def present_config(cfg: dict) -> dict:
    """展示字段 + api_key（明文）+ has_api_key 标志，供 get_config/save_config 返回给前端。"""
    out = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in CONFIG_FIELDS}
    out["api_key"] = cfg.get("api_key", "")
    out["has_api_key"] = bool(cfg.get("api_key"))
    return out

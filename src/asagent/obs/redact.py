from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = ("api_key", "apikey", "authorization", "password", "secret", "token")
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+"),
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    # token 计数（input_tokens 等）是观测数据，不是凭据
    if lowered.endswith("tokens"):
        return False
    return any(s in lowered for s in _SENSITIVE_KEYS)


def _mask_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def redact(obj: Any) -> Any:
    """递归脱敏：敏感字段整体替换，文本中的密钥样式打码。"""
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            if _is_sensitive_key(str(key)):
                result[key] = "***"
            else:
                result[key] = redact(value)
        return result
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        return _mask_text(obj)
    return obj

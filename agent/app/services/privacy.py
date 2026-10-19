"""Best-effort redaction before observations leave the backend."""

import re

MASK = "[MASQUÉ]"
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|private[_-]?key|secret|(?:access|refresh|id)?[_-]?token|password|passwd|authorization|"
    r"patient(?:[_-]?id)?|user[_-]?id|email|ip[_-]?address|client[_-]?id)(?:$|[_-])",
    re.I,
)
_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+"), "Bearer " + MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), MASK),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    (re.compile(r"(?i)\b(https?://)[^\s/@:]+:[^\s/@]+@"), r"\1" + MASK + "@"),
    (re.compile(r'(?i)(["\'](?:api[_-]?key|private[_-]?key|secret|(?:access|refresh|id)?[_-]?token|password|passwd|authorization|'
                r'patient[_-]?id|user[_-]?id)["\']\s*:\s*["\'])[^"\']+'), r"\1" + MASK),
    (re.compile(r"(?i)\b((?:api[_-]?key|private[_-]?key|secret|(?:access|refresh|id)?[_-]?token|password|passwd|authorization|"
                r"patient[_-]?id|user[_-]?id)\s*[=:]\s*[\"']?)[^\s,;\"'}]+"), r"\1" + MASK),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), MASK),
    (re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"), MASK),
)


def redact_text(text: str, sensitive_values=()) -> str:
    result = text
    for value in sensitive_values:
        if isinstance(value, str) and len(value) >= 4:
            result = result.replace(value, MASK)
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_data(value, sensitive_values=()):
    """Return a new structure; keep Kubernetes names and evidence timestamps."""
    if isinstance(value, dict):
        return {key: (MASK if _SENSITIVE_KEY.search(str(key)) else
                      redact_data(item, sensitive_values)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_data(item, sensitive_values) for item in value]
    if isinstance(value, str):
        return redact_text(value, sensitive_values)
    return value

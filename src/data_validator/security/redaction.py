"""Redaction and masking utilities for secrets and sensitive values."""
from __future__ import annotations

import re
from typing import Any

SECRET_KEYS = {
    "password",
    "secret",
    "token",
    "access_key",
    "secret_key",
    "aws_secret_access_key",
    "api_key",
    "private_key",
    "credentials",
    "auth",
}


def is_secret_field(field_name: str) -> bool:
    """Return True if field name suggests sensitive credential data."""
    lower = field_name.lower()
    return any(s in lower for s in SECRET_KEYS)


def redact_secrets_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact values in dictionary where key implies a secret."""
    redacted: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            redacted[k] = redact_secrets_dict(v)
        elif is_secret_field(k):
            redacted[k] = "******"
        else:
            redacted[k] = v
    return redacted


def mask_value(val: Any) -> str:
    """Mask a sensitive data value for display/reporting without leaking full contents."""
    if val is None:
        return "<NULL>"

    s = str(val)
    if not s:
        return ""

    # Email pattern
    if "@" in s and "." in s:
        parts = s.split("@", 1)
        user = parts[0]
        domain = parts[1]
        masked_user = user[0] + "***" if len(user) > 1 else "*"
        return f"{masked_user}@{domain}"

    # General string
    if len(s) <= 3:
        return "*" * len(s)
    elif len(s) <= 8:
        return s[0] + ("*" * (len(s) - 2)) + s[-1]
    else:
        return s[:2] + ("*" * (len(s) - 4)) + s[-2:]

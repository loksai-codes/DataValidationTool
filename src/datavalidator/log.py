"""Logging with secret masking."""

from __future__ import annotations

import logging
import re
import threading
from typing import Set

_secrets: Set[str] = set()
_lock = threading.Lock()
_URL_PW = re.compile(r"(://[^:/\s]+:)([^@/\s]+)(@)")
_KV_PW = re.compile(r"(?i)((?:password|passwd|secret|access_key_secret|aws_secret_access_key)\s*[=:]\s*)(['\"]?)([^'\"\s,}]+)")


def register_secret(value: str) -> None:
    if value and len(value) >= 4:
        with _lock:
            _secrets.add(value)


def mask(text: str) -> str:
    if not isinstance(text, str):
        return text
    for s in sorted(_secrets, key=len, reverse=True):
        text = text.replace(s, "********")
    text = _URL_PW.sub(r"\1********\3", text)
    text = _KV_PW.sub(r"\1\2********", text)
    return text


class _MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = mask(msg)
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger("datavalidator")
    root.setLevel(level.upper())
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S"))
        h.addFilter(_MaskingFilter())
        root.addHandler(h)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f"datavalidator.{name}")
    if not any(isinstance(f, _MaskingFilter) for f in logger.filters):
        logger.addFilter(_MaskingFilter())
    return logger

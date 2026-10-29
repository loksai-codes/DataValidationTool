"""
Structured logging configuration with secret sanitization.
Ensures sensitive tokens, passwords, and AWS credentials are never leaked.
"""

import logging
import re
from typing import Set


class SecretMaskingFilter(logging.Filter):
    """Filter that intercepts log messages and masks sensitive substrings."""

    def __init__(self, secrets_to_mask: Set[str] | None = None):
        super().__init__()
        self.secrets = set(filter(None, secrets_to_mask or set()))
        # Common pattern matches for password parameters in URLs or strings
        self.url_pw_pattern = re.compile(r":([^@/:\s]{3,})@")
        self.aws_key_pattern = re.compile(r"(?i)(aws_secret_access_key|password)\s*=\s*['\"]?([^'\"\s]+)")

    def add_secret(self, secret: str) -> None:
        if secret and len(secret) >= 3:
            self.secrets.add(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            # Mask registered literal secrets
            for s in self.secrets:
                msg = msg.replace(s, "********")
            # Mask URL passwords
            msg = self.url_pw_pattern.sub(r":********@", msg)
            # Mask common config assignments
            msg = self.aws_key_pattern.sub(r"\1=********", msg)
            record.msg = msg
        return True


_global_masking_filter = SecretMaskingFilter()


def setup_logging(level: str = "INFO", additional_secrets: Set[str] | None = None) -> logging.Logger:
    """Configure root logger with mission-control formatting and secret sanitization."""
    if additional_secrets:
        for sec in additional_secrets:
            _global_masking_filter.add_secret(sec)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger("migration_validator")
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        handler.addFilter(_global_masking_filter)
        logger.addHandler(handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger inheriting sanitization."""
    return logging.getLogger(f"migration_validator.{name}")

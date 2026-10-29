"""Cooperative cancellation token for validation jobs."""
from __future__ import annotations

import threading


class ValidationCancelledError(Exception):
    """Raised when a validation job is cancelled cooperatively."""
    pass


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._reason: str = "User requested cancellation"

    def cancel(self, reason: str = "User requested cancellation") -> None:
        self._reason = reason
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise ValidationCancelledError(self._reason)

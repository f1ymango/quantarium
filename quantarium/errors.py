"""Errors with a stable machine-readable ``code`` and an optional ``hint``.

Clients, and agents especially, branch on ``code`` and follow ``hint`` to
recover, instead of parsing messages. The server turns any exception into a
response with a code: these classes carry their own, and other exceptions get
a fallback by type (see ``server.Handler._dispatch``).
"""

from __future__ import annotations


class QuantariumError(ValueError):
    """A request the client can fix."""

    def __init__(self, message: str, code: str = "invalid_request", hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


class NotFound(FileNotFoundError):
    """Something the request names does not exist."""

    def __init__(self, message: str, code: str = "not_found", hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def describe(exc: BaseException) -> tuple[str | None, str | None]:
    """The code and hint an exception carries, if any."""
    return getattr(exc, "code", None), getattr(exc, "hint", None)

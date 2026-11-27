from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """A rule the backend refuses to break. Rendered as {"error": {code, message, details}}."""

    status_code = 409

    def __init__(self, code: str, message: str, *, status_code: int | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code


class NotFound(DomainError):
    status_code = 404


class Unauthorized(DomainError):
    status_code = 401


class InvalidInput(DomainError):
    status_code = 422

"""Domain exception hierarchy (ARCHITECTURE.md §5.5).

Services and the orchestrator raise these framework-agnostic errors; the API
layer (``api.errors``) maps them to RFC 7807 ``application/problem+json``
responses. Keeping them free of FastAPI/HTTP imports lets workers and the
LangGraph nodes raise the same errors without a web context.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base application error. Subclasses set ``status_code`` / ``error_code``."""

    status_code: int = 500
    error_code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    status_code = 422
    error_code = "validation_error"
    default_message = "The request payload failed validation."


class AuthenticationError(AppError):
    status_code = 401
    error_code = "authentication_error"
    default_message = "Authentication is required or the credentials are invalid."


class ForbiddenError(AppError):
    status_code = 403
    error_code = "forbidden"
    default_message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"
    default_message = "The request conflicts with the current state of the resource."


class ProviderError(AppError):
    status_code = 502
    error_code = "provider_error"
    default_message = "An upstream provider failed to respond."


class RetrievalError(AppError):
    status_code = 503
    error_code = "retrieval_error"
    default_message = "Knowledge retrieval is temporarily unavailable."


__all__ = [
    "AppError",
    "ValidationError",
    "AuthenticationError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "ProviderError",
    "RetrievalError",
]

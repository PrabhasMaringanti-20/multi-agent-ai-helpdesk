"""Core cross-cutting foundation (framework-agnostic).

Per ARCHITECTURE.md §4, the ``core`` package owns configuration, security
(JWT + password hashing), RBAC, logging, middleware, the domain exception
hierarchy, and shared constants/enums. This module re-exports the most
frequently used symbols for ergonomic imports.
"""

from app.core.config import Settings, get_settings, settings

__all__ = ["Settings", "get_settings", "settings"]

"""Aggregates and prefixes all API v1 sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai_data,
    analytics,
    auth,
    chat,
    conversations,
    docsearch,
    feedback,
    kb,
    notifications,
    tickets,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
api_router.include_router(tickets.router)
api_router.include_router(kb.router)
api_router.include_router(analytics.router)
api_router.include_router(notifications.router)
api_router.include_router(feedback.router)
api_router.include_router(ai_data.router)
api_router.include_router(docsearch.router)

__all__ = ["api_router"]

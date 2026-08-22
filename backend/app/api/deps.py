"""FastAPI dependency-injection wiring (ARCHITECTURE.md §5.2).

Central place where the HTTP layer is composed: the request-scoped DB session,
the JWT-derived current user / principal (identity is ALWAYS taken from the
verified token, never the request body), the RBAC guards, and service getters.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config_schema import GraphDeps
from app.agents.engine import HelpdeskAIEngine, get_ai_engine
from app.core.config import get_settings
from app.core.constants import TokenType
from app.core.exceptions import AuthenticationError, ForbiddenError
from app.core.redis import get_redis, get_redis_client
from app.core.security import TokenError, decode_token
from app.db.session import get_session as _get_session
from app.models.user import User
from app.providers.registry import (
    get_embedding_provider,
    get_llm_provider,
    get_verifier_provider,
)
from app.rag.dense import DenseRetriever
from app.rag.reranker import HeuristicReranker
from app.rag.retriever import HybridRetriever
from app.rag.sparse import SparseRetriever
from app.rag.vectorstore import get_vector_store
from app.registries.category_registry import get_category_registry
from app.registries.prompt_registry import get_prompt_registry
from app.registries.threshold_registry import get_threshold_registry
from app.registries.tool_registry import get_tool_registry
from app.repositories.analytics_repo import AnalyticsRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.feedback_repo import FeedbackRepository
from app.repositories.kb_repo import KnowledgeRepository
from app.repositories.memory_repo import MemoryRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.ticket_repo import TicketRepository
from app.repositories.user_repo import UserRepository, UserSessionRepository
from app.schemas.auth import Principal
from app.schemas.common import PaginationParams
from app.services.analytics_service import AnalyticsService
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.docsearch_service import UploadsSearcher
from app.services.feedback_service import FeedbackService
from app.services.kb_service import KbService
from app.services.memory_service import MemoryService
from app.services.notification_service import NotificationService
from app.services.ticket_service import TicketService

# auto_error=False so we can raise our own RFC7807 401 instead of FastAPI's.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_session() -> AsyncIterator[AsyncSession]:
    """Re-export of the DB session dependency (kept in ``api.deps`` per §5.2)."""
    async for session in _get_session():
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(
        session=session,
        users=UserRepository(session),
        sessions=UserSessionRepository(session),
        audit=AuditRepository(session),
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)] = None,
) -> User:
    """Resolve, verify, and load the caller's user from the bearer access token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Missing or malformed Authorization header.")

    try:
        decoded = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    try:
        user_id = uuid.UUID(decoded.subject)
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("Token subject is not a valid user id.") from exc

    user = await UserRepository(session).get_with_role(user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("The account is inactive or no longer exists.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_principal(user: CurrentUser) -> Principal:
    """Derive the immutable request principal (id, org, role, permissions)."""
    return Principal.from_user(user)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_roles(*roles: str):
    """Dependency factory: require the caller to hold one of ``roles``."""

    allowed = tuple(roles)

    def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(allowed):
            raise ForbiddenError(f"This action requires one of the roles: {', '.join(allowed)}.")
        return principal

    return _guard


def require_permissions(*permissions: str):
    """Dependency factory: require the caller to hold every listed permission."""

    required = tuple(permissions)

    def _guard(principal: CurrentPrincipal) -> Principal:
        if not principal.has_all_permissions(required):
            raise ForbiddenError(f"This action requires permission(s): {', '.join(required)}.")
        return principal

    return _guard


def client_ip(request: Request) -> str | None:
    """Best-effort client IP (honors a single upstream proxy hop)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# --------------------------------------------------------------------------- #
# Shared resource / helper dependencies
# --------------------------------------------------------------------------- #
def get_audit_service(session: SessionDep) -> AuditService:
    return AuditService(AuditRepository(session))


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


def pagination_params(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> PaginationParams:
    return PaginationParams(page=page, size=size)


PaginationDep = Annotated[PaginationParams, Depends(pagination_params)]


# --------------------------------------------------------------------------- #
# AI engine dependency injection (Phase 11)
# --------------------------------------------------------------------------- #
def get_graph_deps(session: SessionDep) -> GraphDeps:
    """Assemble the per-request AI-engine dependency bundle (providers/services)."""
    settings = get_settings()
    embedder = get_embedding_provider()
    kb_repo = KnowledgeRepository(session)
    retriever = HybridRetriever(
        DenseRetriever(get_vector_store(), embedder),
        SparseRetriever(kb_repo),
        HeuristicReranker(),
        top_k=settings.RETRIEVAL_TOP_K,
        candidate_k=settings.RETRIEVAL_CANDIDATE_K,
    )
    llm_small = get_llm_provider("small")
    categories = get_category_registry()
    memory = MemoryService(
        MemoryRepository(session),
        ConversationRepository(session),
        llm_small,
        get_prompt_registry(),
        window_turns=settings.MEMORY_WINDOW_TURNS,
        summary_trigger_turns=settings.MEMORY_SUMMARY_TRIGGER_TURNS,
    )
    return GraphDeps(
        settings=settings,
        llm_large=get_llm_provider("large"),
        llm_small=llm_small,
        embedder=embedder,
        verifier=get_verifier_provider(),
        retriever=retriever,
        memory=memory,
        kb=KbService(retriever, kb_repo),
        tickets=TicketService(TicketRepository(session), categories),
        notifications=NotificationService(NotificationRepository(session)),
        analytics=AnalyticsService(AnalyticsRepository(session)),
        feedback=FeedbackService(FeedbackRepository(session)),
        audit=AuditService(AuditRepository(session)),
        prompts=get_prompt_registry(),
        categories=categories,
        thresholds=get_threshold_registry(),
        tools=get_tool_registry(),
        users=UserRepository(session),
        conversations=ConversationRepository(session),
        redis=get_redis_client(),
        uploads=UploadsSearcher(session),
    )


GraphDepsDep = Annotated[GraphDeps, Depends(get_graph_deps)]


def get_engine() -> HelpdeskAIEngine:
    """The process-wide compiled AI engine (graph compiled once, deps per-request)."""
    return get_ai_engine()


AiEngineDep = Annotated[HelpdeskAIEngine, Depends(get_engine)]


__all__ = [
    "get_session",
    "SessionDep",
    "get_auth_service",
    "AuthServiceDep",
    "get_current_user",
    "CurrentUser",
    "get_current_principal",
    "CurrentPrincipal",
    "require_roles",
    "require_permissions",
    "client_ip",
    "get_audit_service",
    "AuditServiceDep",
    "get_redis",
    "RedisDep",
    "pagination_params",
    "PaginationDep",
    "get_graph_deps",
    "GraphDepsDep",
    "get_engine",
    "AiEngineDep",
]

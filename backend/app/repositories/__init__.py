"""Repositories layer (SQLAlchemy data access; tenant-scoped; no business rules)."""

from app.repositories.analytics_repo import AnalyticsRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.base import BaseRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.feedback_repo import FeedbackRepository
from app.repositories.kb_repo import KnowledgeRepository
from app.repositories.memory_repo import MemoryRepository
from app.repositories.notification_repo import NotificationRepository
from app.repositories.ticket_repo import TicketRepository
from app.repositories.user_repo import UserRepository

__all__ = [
    "BaseRepository",
    "UserRepository",
    "ConversationRepository",
    "MemoryRepository",
    "TicketRepository",
    "KnowledgeRepository",
    "FeedbackRepository",
    "NotificationRepository",
    "AuditRepository",
    "AnalyticsRepository",
]

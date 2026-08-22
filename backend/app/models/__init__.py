"""ORM models package.

Importing this package registers every application table on
``app.models.base.Base.metadata`` (the Alembic ``target_metadata``). The
checkpointer-owned ``graph_checkpoints`` lives on a separate metadata
(``CheckpointBase``) and is intentionally not part of ``Base.metadata``.
"""

from app.models.base import Base
from app.models.checkpoint import CheckpointBase, GraphCheckpoint
from app.models.conversation import (
    Conversation,
    ConversationSummary,
    MemoryFact,
    Message,
)
from app.models.docsearch import UploadedChunk, UploadedDocument
from app.models.feedback import Feedback, LearningEvent, RelevanceSignal
from app.models.knowledge import (
    EmbeddingsMetadata,
    KbApproval,
    KbChunk,
    KbDocument,
    KbDocumentVersion,
    KbIngestionJob,
)
from app.models.ops import (
    AgentExecution,
    AgentRun,
    AnalyticsEvent,
    AuditLog,
    ConfidenceScore,
    File,
    Notification,
    SystemSetting,
    UsageStatistic,
)
from app.models.organization import Organization, Permission, Role, RolePermission
from app.models.rag_vector import RagVector
from app.models.registry import CategoryRegistry, PromptTemplate
from app.models.ticket import (
    EngineerNote,
    Escalation,
    Ticket,
    TicketAssignment,
    TicketAttachment,
    TicketEvent,
)
from app.models.user import User, UserSession

__all__ = [
    "Base",
    "CheckpointBase",
    "GraphCheckpoint",
    # identity / rbac
    "Organization",
    "Role",
    "Permission",
    "RolePermission",
    "User",
    "UserSession",
    # conversation / memory
    "Conversation",
    "Message",
    "ConversationSummary",
    "MemoryFact",
    # ticketing
    "Ticket",
    "TicketEvent",
    "TicketAttachment",
    "EngineerNote",
    "TicketAssignment",
    "Escalation",
    # knowledge base
    "KbDocument",
    "KbChunk",
    "KbDocumentVersion",
    "EmbeddingsMetadata",
    "KbIngestionJob",
    "KbApproval",
    # registry
    "CategoryRegistry",
    "PromptTemplate",
    # dense retrieval (local vector index)
    "RagVector",
    # document intelligence (uploaded files)
    "UploadedDocument",
    "UploadedChunk",
    # feedback / learning
    "Feedback",
    "RelevanceSignal",
    "LearningEvent",
    # ops / observability / analytics
    "Notification",
    "AuditLog",
    "File",
    "AgentRun",
    "AgentExecution",
    "ConfidenceScore",
    "AnalyticsEvent",
    "UsageStatistic",
    "SystemSetting",
]

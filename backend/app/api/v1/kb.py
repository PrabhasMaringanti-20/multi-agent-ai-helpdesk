"""Knowledge-base routes.

Read for everyone; role-gated create/publish for SME + Admin (the KB module,
#5). End-users/engineers see published docs; SME/Admin also see drafts.
Reuses the existing ingestion upload endpoint unchanged.
"""

from __future__ import annotations

import contextlib
import hashlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update

from app.api.deps import CurrentPrincipal, PaginationDep, SessionDep, require_permissions
from app.core.constants import DocStatus, RoleKey, SourceType
from app.core.exceptions import NotFoundError
from app.core.rbac import Permission
from app.models.knowledge import KbChunk
from app.rag.parsers import parse_document
from app.registries.category_registry import get_category_registry
from app.repositories.kb_repo import KnowledgeRepository
from app.schemas.common import MessageResponse, Page, build_page
from app.schemas.kb import KnowledgeDocumentResponse
from app.workers.ingestion_tasks import ingest_document

router = APIRouter(prefix="/kb", tags=["knowledge-base"])

_EDITOR_ROLES = {RoleKey.SME_REVIEWER.value, RoleKey.ADMIN.value}


class KnowledgeDocumentDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    category: str
    doc_status: str
    version: int
    retrieval_namespace: str
    last_verified_at: datetime | None = None
    body: str = ""


class KbCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    category: str = Field(default="application_error")
    body: str = Field(..., min_length=10)


@router.get("/documents", summary="List / search knowledge documents (role-aware)")
async def list_documents(
    principal: CurrentPrincipal,
    session: SessionDep,
    pagination: PaginationDep,
    q: str | None = Query(default=None, description="Title search"),
    category: str | None = Query(default=None),
) -> Page[KnowledgeDocumentResponse]:
    repo = KnowledgeRepository(session)
    # SME/Admin see every status; everyone else sees published only.
    statuses = None if principal.role in _EDITOR_ROLES else [DocStatus.PUBLISHED]
    rows = await repo.search_documents(
        principal.org_id,
        q=q,
        category=category,
        statuses=statuses,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    total = await repo.count_documents(principal.org_id, q=q, category=category, statuses=statuses)
    return build_page(
        [KnowledgeDocumentResponse.model_validate(d) for d in rows], total, pagination
    )


@router.get("/documents/{doc_id}", summary="View a knowledge document with its body")
async def get_document(
    doc_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> KnowledgeDocumentDetail:
    repo = KnowledgeRepository(session)
    doc = await repo.get_document(doc_id, principal.org_id)
    if doc is None or (
        doc.doc_status != DocStatus.PUBLISHED and principal.role not in _EDITOR_ROLES
    ):
        raise NotFoundError("Knowledge document not found.")
    chunks = await repo.list_chunks(doc.id)
    body = "\n\n".join(c.text for c in chunks)
    detail = KnowledgeDocumentDetail.model_validate(doc)
    detail.body = body
    return detail


@router.post(
    "/documents/create",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions(Permission.KB_WRITE))],
    summary="Create a markdown knowledge article (SME/Admin) — starts as draft",
)
async def create_document(
    payload: KbCreateRequest, principal: CurrentPrincipal, session: SessionDep
) -> KnowledgeDocumentDetail:
    registry = get_category_registry()
    category = payload.category if payload.category in registry else "application_error"
    namespace = registry.get(category).retrieval_namespace
    repo = KnowledgeRepository(session)
    doc = await repo.create(
        org_id=principal.org_id,
        title=payload.title,
        source_type=SourceType.MANUAL,
        category=category,
        retrieval_namespace=namespace,
        doc_status=DocStatus.DRAFT,
        version=1,
        checksum=hashlib.sha256(payload.body.encode()).hexdigest(),
        created_by_user_id=principal.user_id,
        source_uri=f"manual://{category}/{payload.title}",
    )
    await session.flush()
    await repo.add_chunk(
        chunk_id=uuid.uuid4(),
        doc_id=doc.id,
        org_id=principal.org_id,
        category_key=category,
        retrieval_namespace=namespace,
        chunk_index=0,
        text=payload.body,
        embedding_model_id="manual",
        doc_status=DocStatus.DRAFT,
        version=1,
        token_count=len(payload.body.split()),
        source_uri=doc.source_uri,
    )
    await repo.add_version(
        doc_id=doc.id,
        version=1,
        title=payload.title,
        doc_status=DocStatus.DRAFT,
        checksum=doc.checksum,
        source_uri=doc.source_uri,
        change_summary="Created via KB module",
        created_by_user_id=principal.user_id,
    )
    await session.commit()
    detail = KnowledgeDocumentDetail.model_validate(doc)
    detail.body = payload.body
    return detail


class KbEditRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None)


class KbVersionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    title: str
    doc_status: str
    change_summary: str | None = None
    created_at: datetime | None = None


@router.patch(
    "/documents/{doc_id}",
    dependencies=[Depends(require_permissions(Permission.KB_WRITE))],
    summary="Edit an article (SME/Admin) — bumps the version + snapshots history",
)
async def edit_document(
    doc_id: uuid.UUID, payload: KbEditRequest, principal: CurrentPrincipal, session: SessionDep
) -> KnowledgeDocumentDetail:
    repo = KnowledgeRepository(session)
    doc = await repo.get_document(doc_id, principal.org_id)
    if doc is None:
        raise NotFoundError("Knowledge document not found.")
    new_version = (doc.version or 1) + 1
    if payload.title:
        doc.title = payload.title
    if payload.body is not None:
        chunks = await repo.list_chunks(doc.id)
        if chunks:
            chunks[0].text = payload.body
            chunks[0].version = new_version
        else:
            await repo.add_chunk(
                chunk_id=uuid.uuid4(),
                doc_id=doc.id,
                org_id=principal.org_id,
                category_key=doc.category,
                retrieval_namespace=doc.retrieval_namespace,
                chunk_index=0,
                text=payload.body,
                embedding_model_id="manual",
                doc_status=doc.doc_status,
                version=new_version,
                token_count=len(payload.body.split()),
                source_uri=doc.source_uri,
            )
        doc.checksum = hashlib.sha256(payload.body.encode()).hexdigest()
    doc.version = new_version
    await repo.add_version(
        doc_id=doc.id,
        version=new_version,
        title=doc.title,
        doc_status=doc.doc_status,
        checksum=doc.checksum,
        source_uri=doc.source_uri,
        change_summary="Edited via KB module",
        created_by_user_id=principal.user_id,
    )
    await session.commit()
    chunks = await repo.list_chunks(doc.id)
    detail = KnowledgeDocumentDetail.model_validate(doc)
    detail.body = "\n\n".join(c.text for c in chunks)
    return detail


@router.get("/documents/{doc_id}/versions", summary="Version history for an article")
async def list_versions(
    doc_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> list[KbVersionDTO]:
    repo = KnowledgeRepository(session)
    doc = await repo.get_document(doc_id, principal.org_id)
    if doc is None:
        raise NotFoundError("Knowledge document not found.")
    rows = await repo.list_versions(doc.id)
    return [KbVersionDTO.model_validate(r) for r in rows]


async def _sync_vector_index(doc, chunks, *, publish: bool) -> None:
    """Keep the dense (vector) index in sync when a doc is published/unpublished.

    Best-effort: publishing embeds the chunks into the vector store so the article
    becomes semantically searchable; unpublishing removes them. Never raises — if
    embeddings are unavailable (quota), sparse full-text search still covers it and
    a later ``scripts/reindex_kb.py`` backfills the vectors.
    """
    from app.providers.registry import get_embedding_provider
    from app.rag.vectorstore import get_vector_store

    store = get_vector_store()
    ids = [str(c.id) for c in chunks]
    if not ids:
        return
    if not publish:
        with contextlib.suppress(Exception):
            await store.delete(ids=ids)
        return
    try:
        embedder = get_embedding_provider()
        texts = [c.text for c in chunks]
        vectors = (await embedder.embed(texts)).vectors
        metadatas = [
            {
                "org_id": str(c.org_id),
                "doc_id": str(c.doc_id),
                "category_key": c.category_key,
                "retrieval_namespace": c.retrieval_namespace,
                "doc_status": "published",
                "version": c.version,
                "source_uri": c.source_uri or "",
                "last_verified_at": (
                    doc.last_verified_at.isoformat() if doc.last_verified_at else None
                ),
                "embedding_model_id": embedder.model_id,
            }
            for c in chunks
        ]
        await store.upsert(ids=ids, embeddings=vectors, documents=texts, metadatas=metadatas)
    except Exception:  # noqa: BLE001 - publish still succeeds; sparse FTS covers it
        pass


async def _set_status(
    session, principal, doc_id: uuid.UUID, new_status: DocStatus
) -> KnowledgeDocumentDetail:
    repo = KnowledgeRepository(session)
    doc = await repo.get_document(doc_id, principal.org_id)
    if doc is None:
        raise NotFoundError("Knowledge document not found.")
    doc.doc_status = new_status
    if new_status == DocStatus.PUBLISHED:
        doc.last_verified_at = datetime.now(UTC)
    await session.execute(
        update(KbChunk).where(KbChunk.doc_id == doc.id).values(doc_status=new_status)
    )
    await session.commit()
    chunks = await repo.list_chunks(doc.id)
    await _sync_vector_index(doc, chunks, publish=(new_status == DocStatus.PUBLISHED))
    detail = KnowledgeDocumentDetail.model_validate(doc)
    detail.body = "\n\n".join(c.text for c in chunks)
    return detail


@router.post(
    "/documents/{doc_id}/publish",
    dependencies=[Depends(require_permissions(Permission.KB_WRITE))],
    summary="Publish a knowledge document (SME/Admin)",
)
async def publish_document(
    doc_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> KnowledgeDocumentDetail:
    return await _set_status(session, principal, doc_id, DocStatus.PUBLISHED)


@router.post(
    "/documents/{doc_id}/unpublish",
    dependencies=[Depends(require_permissions(Permission.KB_WRITE))],
    summary="Unpublish a knowledge document back to draft (SME/Admin)",
)
async def unpublish_document(
    doc_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> KnowledgeDocumentDetail:
    return await _set_status(session, principal, doc_id, DocStatus.DRAFT)


@router.post(
    "/documents",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permissions(Permission.KB_WRITE))],
    summary="Upload a PDF/DOCX/text document for ingestion",
)
async def upload_document(
    principal: CurrentPrincipal,
    file: UploadFile = File(...),
    category: str = Form("application_error"),
) -> MessageResponse:
    data = await file.read()
    text = parse_document(data, filename=file.filename, content_type=file.content_type)
    namespace = get_category_registry().get(category).retrieval_namespace
    ingest_document.delay(
        org_id=str(principal.org_id),
        created_by_user_id=str(principal.user_id),
        title=file.filename or "Untitled",
        category=category,
        namespace=namespace,
        text=text,
    )
    return MessageResponse(detail="Document accepted for ingestion.")


__all__ = ["router"]

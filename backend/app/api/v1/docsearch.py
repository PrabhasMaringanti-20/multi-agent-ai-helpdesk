"""Document-Intelligence API — attach files/URLs and AI-search across them.

POST /docsearch/inspect     (multipart) -> if Excel, returns its sheet names
                                            so the UI can ask "which tab?"
POST /docsearch/upload      (multipart file [+ sheet]) -> parse + index
POST /docsearch/url         {"url": "..."}             -> fetch + index
GET  /docsearch/documents                              -> my attached sources
DELETE /docsearch/documents/{id}                       -> remove one
POST /docsearch/search      {"query": "..."}           -> AI-summarized hits
                                                          (file + location + verbatim)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, SessionDep
from app.core.exceptions import NotFoundError, ValidationError
from app.providers.registry import get_llm_provider
from app.services import docsearch_service as ds
from app.services.docsearch_service import DocSearchService

router = APIRouter(prefix="/docsearch", tags=["document-search"])

_MAX_BYTES = 20 * 1024 * 1024  # 20 MB


class UrlRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


def _kind(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith((".xlsx", ".xlsm")):
        return "excel"
    return "text"


@router.post("/inspect", summary="Inspect an upload (returns Excel tabs to choose from)")
async def inspect(principal: CurrentPrincipal, file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise ValidationError("File is larger than 20 MB.")
    kind = _kind(file.filename or "")
    sheets = None
    if kind == "excel":
        try:
            sheets = ds.sheet_names(data)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"Could not read the Excel file: {exc}") from exc
    return {"filename": file.filename, "source_type": kind, "sheets": sheets}


@router.post("/upload", summary="Attach and index a file")
async def upload(
    principal: CurrentPrincipal,
    session: SessionDep,
    file: UploadFile = File(...),
    sheet: str | None = Form(default=None),
) -> dict:
    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise ValidationError("File is larger than 20 MB.")
    kind = _kind(file.filename or "")
    chosen_sheet = None
    try:
        if kind == "pdf":
            passages = ds._parse_pdf(data)
        elif kind == "docx":
            passages = ds._parse_docx(data)
        elif kind == "excel":
            chosen_sheet, passages = ds._parse_excel(data, sheet)
        else:
            passages = ds._parse_text(data)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Could not read the file: {exc}") from exc
    if not passages:
        raise ValidationError("No readable text was found in that file.")
    svc = DocSearchService(session, get_llm_provider("small"))
    return await svc.ingest(
        principal,
        filename=file.filename or "Untitled",
        source_type=kind,
        passages=passages,
        sheet=chosen_sheet,
    )


@router.post("/url", summary="Attach and index a public web page")
async def add_url(payload: UrlRequest, principal: CurrentPrincipal, session: SessionDep) -> dict:
    try:
        passages = ds.fetch_url(payload.url)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Could not fetch that URL: {exc}") from exc
    if not passages:
        raise ValidationError("No readable text was found at that URL.")
    svc = DocSearchService(session, get_llm_provider("small"))
    return await svc.ingest(
        principal,
        filename=payload.url,
        source_type="url",
        passages=passages,
        source_ref=payload.url,
    )


@router.get("/documents", summary="List my attached sources")
async def list_documents(principal: CurrentPrincipal, session: SessionDep) -> dict:
    svc = DocSearchService(session, get_llm_provider("small"))
    return {"documents": await svc.list_documents(principal)}


@router.delete("/documents/{doc_id}", summary="Remove an attached source")
async def delete_document(
    doc_id: uuid.UUID, principal: CurrentPrincipal, session: SessionDep
) -> dict:
    svc = DocSearchService(session, get_llm_provider("small"))
    if not await svc.delete_document(principal, doc_id):
        raise NotFoundError("Document not found.")
    return {"deleted": True}


@router.post("/search", summary="Search attached files; AI summarizes the hits")
async def search(payload: SearchRequest, principal: CurrentPrincipal, session: SessionDep) -> dict:
    svc = DocSearchService(session, get_llm_provider("small"))
    return await svc.search(principal, payload.query)


__all__ = ["router"]

"""AI Data API route — natural-language, LLM-driven access to the database.

POST /ai/query {"instruction": "..."}  ->  the LLM picks a data operation, the
service runs it against PostgreSQL, and the LLM explains the result. This is the
"API that lets software communicate with and manipulate data in a database",
with the LLM doing 100% of the interpretation.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, SessionDep
from app.providers.registry import get_llm_provider
from app.services.ai_data_api import TOOLS, AiDataApi

router = APIRouter(prefix="/ai", tags=["ai-data"])


class AiQueryRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=1000)


@router.get("/tools", summary="List the data operations the AI can perform")
async def list_tools(principal: CurrentPrincipal) -> dict:
    return {"tools": TOOLS}


@router.post("/query", summary="Ask the database in plain English (LLM-driven)")
async def ai_query(
    payload: AiQueryRequest, principal: CurrentPrincipal, session: SessionDep
) -> dict:
    service = AiDataApi(session, get_llm_provider("small"))
    return await service.run(payload.instruction, principal)


__all__ = ["router"]

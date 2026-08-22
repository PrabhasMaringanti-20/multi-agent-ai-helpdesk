"""AI Data API — a 100%-LLM natural-language interface to the database.

The LLM is the whole point here: it reads a plain-English instruction, chooses ONE
data operation (a "tool") and its arguments, the service runs that operation
against PostgreSQL (read OR manipulate), and the LLM turns the result back into a
plain-English answer. This is the "API that lets software communicate with and
manipulate data in a database", driven end-to-end by the LLM.

A tiny keyword planner is kept only as a fallback so a demo survives if the LLM
provider is temporarily unavailable (e.g. quota); the primary path is the LLM.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.constants import (
    ConversationStatus,
    TicketEventType,
    TicketPriority,
    TicketStatus,
)
from app.models.ticket import Ticket
from app.providers.base import ChatMessage
from app.registries.category_registry import get_category_registry
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.kb_repo import KnowledgeRepository
from app.repositories.ticket_repo import TicketRepository
from app.services.docsearch_service import UploadsSearcher

# Tool catalog shown to the LLM. Keep names/args stable — the LLM must return them.
TOOLS: list[dict[str, str]] = [
    {
        "name": "count_tickets",
        "args": "status?, category?",
        "desc": "Count tickets, optionally filtered by status or category.",
    },
    {
        "name": "list_tickets",
        "args": "status?, category?, limit?",
        "desc": "List recent tickets (subject/status/priority).",
    },
    {"name": "get_ticket", "args": "ticket_id", "desc": "Get one ticket's details by id."},
    {"name": "tickets_by_status", "args": "-", "desc": "Count of tickets grouped by status."},
    {"name": "tickets_by_category", "args": "-", "desc": "Count of tickets grouped by category."},
    {
        "name": "create_ticket",
        "args": "subject, category?, priority?",
        "desc": "CREATE a new ticket (manipulates data).",
    },
    {
        "name": "update_ticket_status",
        "args": "ticket_id, status",
        "desc": "UPDATE a ticket's status (manipulates data).",
    },
    {
        "name": "count_knowledge",
        "args": "category?",
        "desc": "Count knowledge-base articles, optionally by category.",
    },
    {
        "name": "search_knowledge",
        "args": "query, limit?",
        "desc": "Search knowledge articles by title.",
    },
    {
        "name": "search_documents",
        "args": "query, limit?",
        "desc": "Search files uploaded in Document Search by content, and answer from their text.",
    },
]
_VALID_STATUS = {s.value for s in TicketStatus}
_VALID_PRIORITY = {p.value for p in TicketPriority}


class DataPlan(BaseModel):
    tool: str = Field(description="one of the tool names")
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class AiDataApi:
    def __init__(self, session: Any, llm: Any) -> None:
        self.session = session
        self.llm = llm
        self.tickets = TicketRepository(session)
        self.kb = KnowledgeRepository(session)
        self.conversations = ConversationRepository(session)
        self.uploads = UploadsSearcher(session)

    # ---- public entrypoint ------------------------------------------------ #
    async def run(self, instruction: str, principal: Any) -> dict[str, Any]:
        plan, planner = await self._plan(instruction)
        try:
            result = await self._execute(plan, principal)
            ok = True
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}
            ok = False
        answer = await self._explain(instruction, plan, result, ok)
        return {
            "answer": answer,
            "tool": plan.tool,
            "args": plan.args,
            "planner": planner,  # "llm" or "keyword-fallback"
            "result": result,
        }

    # ---- 1) LLM chooses the operation ------------------------------------- #
    async def _plan(self, instruction: str) -> tuple[DataPlan, str]:
        catalog = "\n".join(f"- {t['name']}({t['args']}): {t['desc']}" for t in TOOLS)
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You translate a helpdesk admin's plain-English instruction into ONE data "
                    "operation. Choose the single best tool and its arguments. Use EXACT tool "
                    "names. status is one of open/triaged/in_progress/awaiting_user/resolved/"
                    "closed/reopened; priority is low/medium/high/urgent. Return JSON only."
                    f"\n\nTOOLS:\n{catalog}"
                ),
            ),
            ChatMessage(role="user", content=f"Instruction: {instruction}"),
        ]
        try:
            plan: DataPlan = await self.llm.generate_structured(messages, DataPlan)
            if plan.tool in {t["name"] for t in TOOLS}:
                return plan, "llm"
        except Exception:  # noqa: BLE001 - fall back to keywords so the demo survives
            pass
        return self._keyword_plan(instruction), "keyword-fallback"

    def _keyword_plan(self, text: str) -> DataPlan:
        t = text.lower()
        if any(w in t for w in ("create", "raise", "open a ticket", "log a ticket")):
            return DataPlan(tool="create_ticket", args={"subject": text.strip()})
        if ("status" in t or "resolve" in t or "close" in t) and any(c.isdigit() for c in t):
            return DataPlan(tool="update_ticket_status", args={})
        if "by category" in t:
            return DataPlan(tool="tickets_by_category", args={})
        if "by status" in t or ("how many" in t and "ticket" in t and "category" not in t):
            return DataPlan(tool="tickets_by_status", args={})
        if any(
            w in t
            for w in (
                "document",
                "uploaded",
                "upload",
                "attachment",
                "pdf",
                "excel",
                "spreadsheet",
                "docx",
            )
        ):
            return DataPlan(tool="search_documents", args={"query": text.strip()})
        if any(w in t for w in ("article", "knowledge", " kb")):
            return DataPlan(tool="count_knowledge", args={})
        if any(w in t for w in ("list", "show", "recent")) and "ticket" in t:
            return DataPlan(tool="list_tickets", args={"limit": 5})
        if "ticket" in t:
            return DataPlan(tool="count_tickets", args={})
        return DataPlan(tool="tickets_by_status", args={})

    # ---- 2) run the operation against the database ------------------------ #
    async def _execute(self, plan: DataPlan, principal: Any) -> dict[str, Any]:
        org = principal.org_id
        a = plan.args or {}
        status = a.get("status") if a.get("status") in _VALID_STATUS else None
        category = a.get("category")

        if plan.tool == "count_tickets":
            filters: dict[str, Any] = {}
            if status:
                filters["status"] = TicketStatus(status)
            if category:
                filters["category"] = category
            return {
                "count": await self.tickets.count_for_org(org, **filters),
                "filters": {"status": status, "category": category},
            }

        if plan.tool == "list_tickets":
            limit = min(int(a.get("limit", 5) or 5), 20)
            filters = {}
            if status:
                filters["status"] = TicketStatus(status)
            if category:
                filters["category"] = category
            rows = await self.tickets.list_for_org(org, limit=limit, **filters)
            return {
                "tickets": [
                    {
                        "id": str(t.id),
                        "subject": t.subject,
                        "status": t.status,
                        "priority": t.priority,
                        "category": t.category,
                    }
                    for t in rows
                ]
            }

        if plan.tool == "get_ticket":
            t = await self.tickets.get_for_org(_as_uuid(a.get("ticket_id")), org)
            if not t:
                return {"error": "ticket not found"}
            return {
                "id": str(t.id),
                "subject": t.subject,
                "status": t.status,
                "priority": t.priority,
                "category": t.category,
                "assigned_queue": t.assigned_queue,
                "escalation_reason": t.escalation_reason,
            }

        if plan.tool == "tickets_by_status":
            return {"by_status": await self._group(Ticket.status, org)}

        if plan.tool == "tickets_by_category":
            return {"by_category": await self._group(Ticket.category, org)}

        if plan.tool == "count_knowledge":
            return {"count": await self.kb.count_documents(org, category=category)}

        if plan.tool == "search_knowledge":
            rows = await self.kb.search_documents(
                org, q=a.get("query"), limit=min(int(a.get("limit", 5) or 5), 20)
            )
            return {
                "articles": [
                    {"id": str(d.id), "title": d.title, "category": d.category} for d in rows
                ]
            }

        if plan.tool == "search_documents":
            hits = await self.uploads.search(
                org, a.get("query") or "", limit=min(int(a.get("limit", 5) or 5), 10)
            )
            return {
                "documents": [
                    {
                        "source": (h.source_uri or "").replace("upload://", ""),
                        "excerpt": (h.text or "").strip()[:400],
                    }
                    for h in hits
                ]
            }

        if plan.tool == "create_ticket":
            return await self._create_ticket(a, principal)

        if plan.tool == "update_ticket_status":
            return await self._update_status(a, principal)

        return {"error": f"unknown tool '{plan.tool}'"}

    async def _group(self, column: Any, org: uuid.UUID) -> dict[str, int]:
        stmt = (
            select(column, func.count())
            .where(Ticket.org_id == org, Ticket.deleted_at.is_(None))
            .group_by(column)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(k): int(v) for k, v in rows}

    async def _create_ticket(self, a: dict[str, Any], principal: Any) -> dict[str, Any]:
        registry = get_category_registry()
        category = a.get("category") if a.get("category") in registry else "application_error"
        priority = a.get("priority") if a.get("priority") in _VALID_PRIORITY else "medium"
        subject = (a.get("subject") or "New request").strip()[:200]
        conv = await self.conversations.create(
            id=uuid.uuid4(),
            org_id=principal.org_id,
            user_id=principal.user_id,
            status=ConversationStatus.AWAITING_HUMAN,
            category=category,
            title=subject,
        )
        ticket = Ticket(
            org_id=principal.org_id,
            conversation_id=conv.id,
            created_by_user_id=principal.user_id,
            category=category,
            priority=TicketPriority(priority),
            status=TicketStatus.OPEN,
            assigned_queue=registry.get(category).handoff_queue,
            subject=subject,
            intake_fields={"created_via": "ai_data_api"},
            escalation_reason="created_via_ai_data_api",
            redacted_transcript={},
        )
        self.session.add(ticket)
        await self.session.flush()
        await self.session.commit()
        return {
            "created": True,
            "id": str(ticket.id),
            "subject": subject,
            "category": category,
            "priority": priority,
            "status": "open",
        }

    async def _update_status(self, a: dict[str, Any], principal: Any) -> dict[str, Any]:
        new_status = a.get("status")
        if new_status not in _VALID_STATUS:
            return {"error": f"invalid status '{new_status}'"}
        ticket = await self.tickets.get_for_org(_as_uuid(a.get("ticket_id")), principal.org_id)
        if not ticket:
            return {"error": "ticket not found"}
        old = str(ticket.status)
        ticket.status = TicketStatus(new_status)
        await self.tickets.add_event(
            ticket_id=ticket.id,
            event_type=TicketEventType.STATUS_CHANGED,
            actor_user_id=principal.user_id,
            from_status=old,
            to_status=new_status,
        )
        await self.session.commit()

        # Learning loop: when a ticket is resolved, auto-draft a KB article for
        # SME review (best-effort — never fail the status update on a draft error).
        kb_draft: dict[str, Any] | None = None
        if new_status in ("resolved", "closed"):
            try:
                from app.repositories.kb_repo import KnowledgeRepository
                from app.services.kb_draft_service import KbDraftService

                drafter = KbDraftService(
                    self.session, self.llm, KnowledgeRepository(self.session), self.tickets
                )
                doc, created = await drafter.draft_from_ticket(ticket, principal)
                kb_draft = {"doc_id": str(doc.id), "title": doc.title, "created": created}
            except Exception:  # noqa: BLE001 - drafting is best-effort
                kb_draft = None

        result = {"updated": True, "id": str(ticket.id), "from": old, "to": new_status}
        if kb_draft:
            result["kb_draft"] = kb_draft
        return result

    # ---- 3) LLM explains the result --------------------------------------- #
    async def _explain(
        self, instruction: str, plan: DataPlan, result: dict[str, Any], ok: bool
    ) -> str:
        try:
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "You are a helpdesk data assistant. In 1-3 short sentences, answer the "
                        "user's question using ONLY the DATA. State numbers plainly. Do not invent."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(f"Question: {instruction}\nOperation: {plan.tool}\nDATA: {result}"),
                ),
            ]
            text = (await self.llm.generate(messages)).text.strip()
            if text:
                return text
        except Exception:  # noqa: BLE001 - templated fallback
            pass
        return _template_answer(plan.tool, result)


def _as_uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def _template_answer(tool: str, result: dict[str, Any]) -> str:
    if "error" in result:
        return f"Could not complete that: {result['error']}."
    if "count" in result:
        return f"There are {result['count']}."
    if "by_status" in result:
        return "By status: " + ", ".join(f"{k}={v}" for k, v in result["by_status"].items())
    if "by_category" in result:
        return "By category: " + ", ".join(f"{k}={v}" for k, v in result["by_category"].items())
    if result.get("created"):
        return f"Created ticket {result['id']} ({result['subject']})."
    if result.get("updated"):
        return f"Updated ticket {result['id']} from {result['from']} to {result['to']}."
    if "tickets" in result:
        return f"Found {len(result['tickets'])} tickets."
    if "articles" in result:
        return f"Found {len(result['articles'])} knowledge articles."
    if "documents" in result:
        docs = result["documents"]
        if not docs:
            return "No matching uploaded documents were found."
        srcs = ", ".join(d["source"] for d in docs[:3])
        return f"Found {len(docs)} matching passage(s) in uploaded documents: {srcs}."
    return "Done."


__all__ = ["AiDataApi", "DataPlan", "TOOLS"]

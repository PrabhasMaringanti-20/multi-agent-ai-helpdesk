"""ingress_guard — deterministic (no-LLM) entry gate."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.config_schema import get_deps
from app.agents.state import AgentState

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DIGITS_RE = re.compile(r"\b\d{6,}\b")
_INJECTION_RE = re.compile(
    r"(ignore (all |the )?(previous|prior) instructions|disregard (the )?system|"
    r"reveal your (system )?prompt|you are now)",
    re.IGNORECASE,
)
_GREETINGS = {"hi", "hello", "hey", "thanks", "thank you", "good morning", "good evening"}


def _redact(text: str) -> str:
    text = _EMAIL_RE.sub("[email]", text)
    return _DIGITS_RE.sub("[number]", text)


def _control_intent(text: str) -> str | None:
    lowered = text.lower().strip()
    if lowered in _GREETINGS:
        return "greeting"
    if any(
        k in lowered for k in ("speak to a human", "talk to an agent", "human agent", "real person")
    ):
        return "human_request"
    if lowered in ("cancel", "never mind", "nevermind", "stop"):
        return "cancel"
    return None


async def ingress_guard(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    raw = state["raw_user_message"]
    normalized = " ".join(raw.split())
    query_hash = hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()
    injection = bool(_INJECTION_RE.search(normalized))
    control = _control_intent(normalized)

    conversation = state["conversation"].model_copy(update={"control_intent": control})
    updates: dict[str, Any] = {
        "normalized_query": normalized,
        "redacted_query": _redact(normalized),
        "query_hash": query_hash,
        "injection_flag": injection,
        "safety_verdict": "block" if injection else "ok",
        "conversation": conversation,
        "node_path": ["ingress_guard"],
        "audit_trail": [
            {"node": "ingress_guard", "safety": "block" if injection else "ok", "control": control}
        ],
    }

    if deps.redis is not None and not injection and control is None:
        try:
            cached = await deps.redis.get(f"answer:{state['org_id']}:{query_hash}")
            if cached:
                updates["cache_hit"] = True
                updates["cached_answer"] = cached
        except Exception:  # noqa: BLE001 - cache is best-effort
            pass
    return updates


__all__ = ["ingress_guard"]

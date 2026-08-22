"""Live provider smoke test (Phase 6 / Phase 9 steps 5-7).

Exercises the configured LLM + embedding provider through the abstraction:
a completion, a streamed completion, and an embedding. Requires the relevant
API key to be set in the environment.

Usage (from the backend/ directory, with deps installed):

    LLM_PROVIDER=gemini EMBEDDING_PROVIDER=gemini GEMINI_API_KEY=...  python scripts/check_providers.py
    LLM_PROVIDER=openai EMBEDDING_PROVIDER=openai OPENAI_API_KEY=...  python scripts/check_providers.py
    LLM_PROVIDER=claude ANTHROPIC_API_KEY=...                         python scripts/check_providers.py

Provider switching is just the LLM_PROVIDER / EMBEDDING_PROVIDER env vars.
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.providers.base import ChatMessage
from app.providers.registry import (
    get_embedding_provider,
    get_llm_provider,
    get_token_accountant,
)


async def main() -> None:
    settings = get_settings()
    print(f"LLM_PROVIDER={settings.LLM_PROVIDER}  EMBEDDING_PROVIDER={settings.EMBEDDING_PROVIDER}")

    llm = get_llm_provider("large")
    result = await llm.generate(
        [ChatMessage(role="user", content="Say hello in one short sentence.")]
    )
    print(f"\n[generate] model={result.model} tokens={result.usage.total_tokens}\n{result.text}")

    print("\n[stream] ", end="", flush=True)
    async for token in llm.stream([ChatMessage(role="user", content="Count from 1 to 5.")]):
        print(token, end="", flush=True)
    print()

    embedder = get_embedding_provider()
    embedding = await embedder.embed(["enterprise helpdesk vpn reset"])
    print(f"\n[embed] model={embedding.model} dim={len(embedding.vectors[0])}")

    print(f"\n[token accounting] {get_token_accountant().total}")


if __name__ == "__main__":
    asyncio.run(main())

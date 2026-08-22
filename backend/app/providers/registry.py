"""Provider factory: resolve LLM/Embedding/Verifier providers from settings.

The active provider is chosen by ``settings.LLM_PROVIDER`` /
``settings.EMBEDDING_PROVIDER`` (gemini | openai | claude | fake). Instances are
cached per (provider, tier) and share one rate limiter + token accountant so
cost/limits are tracked process-wide. Concrete adapter SDKs are imported lazily
inside the adapters, so unused providers never need their SDK installed.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.exceptions import ProviderError
from app.providers.base import (
    AsyncRateLimiter,
    BaseEmbeddingProvider,
    BaseLLMProvider,
    EmbeddingProvider,
    LLMProvider,
    TokenAccountant,
    VerifierProvider,
)
from app.providers.claude import ClaudeLLMProvider
from app.providers.fakes import (
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVerifierProvider,
)
from app.providers.gemini import GeminiEmbeddingProvider, GeminiLLMProvider
from app.providers.openai import OpenAIEmbeddingProvider, OpenAILLMProvider
from app.providers.verifier import LLMVerifier

_accountant = TokenAccountant()
_rate_limiter: AsyncRateLimiter | None = None
_llm_cache: dict[tuple[str, str], LLMProvider] = {}
_embed_cache: dict[str, EmbeddingProvider] = {}


def get_token_accountant() -> TokenAccountant:
    return _accountant


def _limiter() -> AsyncRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AsyncRateLimiter(get_settings().LLM_RATE_LIMIT_PER_MINUTE)
    return _rate_limiter


def _llm_kwargs(settings: Settings, tier: str) -> dict[str, object]:
    return {
        "tier": tier,
        "rate_limiter": _limiter(),
        "accountant": _accountant,
        "timeout": float(settings.LLM_TIMEOUT_SECONDS),
        "max_retries": settings.LLM_MAX_RETRIES,
        "base_delay": settings.LLM_RETRY_BASE_DELAY,
        "temperature": settings.LLM_TEMPERATURE,
        "max_output_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
    }


def _build_llm(provider: str, tier: str, settings: Settings) -> LLMProvider:
    if provider == "fake":
        return FakeLLMProvider(tier=tier)
    if provider == "gemini":
        model = settings.LLM_SMALL_MODEL if tier == "small" else settings.LLM_LARGE_MODEL
        return GeminiLLMProvider(model=model, **_llm_kwargs(settings, tier))
    if provider == "openai":
        model = settings.OPENAI_SMALL_MODEL if tier == "small" else settings.OPENAI_LARGE_MODEL
        return OpenAILLMProvider(model=model, **_llm_kwargs(settings, tier))
    if provider == "claude":
        model = (
            settings.ANTHROPIC_SMALL_MODEL if tier == "small" else settings.ANTHROPIC_LARGE_MODEL
        )
        return ClaudeLLMProvider(model=model, **_llm_kwargs(settings, tier))
    raise ProviderError(f"Unknown LLM provider: {provider}")


def _build_llm_chain(tier: str, settings: Settings) -> LLMProvider:
    """Primary provider, optionally wrapped in a fallback chain.

    ``LLM_FALLBACK_PROVIDERS`` lists extra providers (comma-separated) tried in
    order when the primary errors (quota/429). With none configured this returns
    the bare primary, so default behaviour is unchanged.
    """
    primary_name = settings.LLM_PROVIDER.lower()
    primary = _build_llm(primary_name, tier, settings)
    names = [n.strip().lower() for n in (settings.LLM_FALLBACK_PROVIDERS or "").split(",")]
    fallbacks: list[LLMProvider] = []
    for name in names:
        if not name or name == primary_name:
            continue
        try:
            fallbacks.append(_build_llm(name, tier, settings))
        except ProviderError:  # unknown/misconfigured fallback — just skip it
            continue
    if not fallbacks:
        return primary
    from app.providers.fallback import FallbackLLMProvider

    return FallbackLLMProvider([primary, *fallbacks], tier=tier)


def get_llm_provider(tier: str = "large") -> LLMProvider:
    settings = get_settings()
    provider = settings.LLM_PROVIDER.lower()
    key = (provider, tier)
    if key not in _llm_cache:
        _llm_cache[key] = _build_llm_chain(tier, settings)
    return _llm_cache[key]


def _build_embedding(provider: str, settings: Settings) -> EmbeddingProvider:
    common = {
        "dim": settings.EMBEDDING_DIM,
        "rate_limiter": _limiter(),
        "accountant": _accountant,
        "timeout": float(settings.LLM_TIMEOUT_SECONDS),
        "max_retries": settings.LLM_MAX_RETRIES,
        "base_delay": settings.LLM_RETRY_BASE_DELAY,
    }
    if provider == "fake":
        return FakeEmbeddingProvider(dim=settings.EMBEDDING_DIM)
    if provider == "gemini":
        return GeminiEmbeddingProvider(model=settings.EMBEDDING_MODEL, **common)
    if provider == "openai":
        return OpenAIEmbeddingProvider(model=settings.OPENAI_EMBEDDING_MODEL, **common)
    raise ProviderError(f"Unknown embedding provider: {provider}")


def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    provider = settings.EMBEDDING_PROVIDER.lower()
    if provider not in _embed_cache:
        _embed_cache[provider] = _build_embedding(provider, settings)
    return _embed_cache[provider]


def get_verifier_provider() -> VerifierProvider:
    settings = get_settings()
    if settings.LLM_PROVIDER.lower() == "fake":
        return FakeVerifierProvider()
    # Use the small/lite tier for the entailment judge: it keeps the per-turn
    # Gemini call count (and rate-limit pressure) down — the synthesizer already
    # uses the large tier. Transient judge errors are handled gracefully by the
    # grounding gate, so a weaker judge does not cause spurious escalations.
    return LLMVerifier(get_llm_provider("small"))


def reset_provider_cache() -> None:
    """Clear cached providers (used by tests after changing settings)."""
    global _rate_limiter
    _llm_cache.clear()
    _embed_cache.clear()
    _rate_limiter = None


# Keep abstract base imports referenced for typing consumers.
_ = (BaseLLMProvider, BaseEmbeddingProvider)

__all__ = [
    "get_llm_provider",
    "get_embedding_provider",
    "get_verifier_provider",
    "get_token_accountant",
    "reset_provider_cache",
]

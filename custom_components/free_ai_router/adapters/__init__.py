"""Anbieterdialekte. Auswahl ueber ``api_style`` aus der Registry."""

from __future__ import annotations

from .anthropic_ai import AnthropicAdapter
from .base import (
    ChatRequest,
    ChatResponse,
    ImageAttachment,
    ProviderAdapter,
    ProviderError,
    ToolCall,
    ToolSpec,
)
from .google_ai import GoogleAdapter
from .openai_compat import OpenAICompatAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {
    OpenAICompatAdapter.api_style: OpenAICompatAdapter(),
    GoogleAdapter.api_style: GoogleAdapter(),
    AnthropicAdapter.api_style: AnthropicAdapter(),
}


def get_adapter(api_style: str) -> ProviderAdapter:
    """Hole den Adapter zu einem ``api_style``."""
    try:
        return _ADAPTERS[api_style]
    except KeyError:
        raise ValueError(f"Kein Adapter fuer api_style {api_style!r}") from None


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ImageAttachment",
    "ProviderAdapter",
    "ProviderError",
    "ToolCall",
    "ToolSpec",
    "get_adapter",
]

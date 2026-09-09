"""Anthropic-Messages-Dialekt.

In Phase 1 liefert kein mitgelieferter Anbieter diesen Stil aus — er steht
hier, weil ``api_style: anthropic`` im Registry-Schema erlaubt ist und eine
Datendatei sonst auf einen Adapter zeigen koennte, den es nicht gibt.
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from ..ratelimit import parse_headers
from ..schema_tools import relax_schema
from .base import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ChatRequest,
    ChatResponse,
    ProviderAdapter,
    ProviderError,
    Stopwatch,
    ToolCall,
)

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(ProviderAdapter):
    api_style = "anthropic"

    @staticmethod
    def auth_headers(provider: Any, api_key: str) -> dict[str, str]:
        headers = ProviderAdapter.auth_headers(provider, api_key)
        headers.setdefault("anthropic-version", _ANTHROPIC_VERSION)
        return headers

    def _messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        """Anthropic kennt nur ``user`` und ``assistant``.

        Werkzeugergebnisse sind ``tool_result``-Bloecke in einer User-Nachricht,
        Werkzeugaufrufe ``tool_use``-Bloecke in einer Assistant-Nachricht.
        """
        messages: list[dict[str, Any]] = []
        for turn in request.turns:
            if turn.role == ROLE_SYSTEM:
                continue

            if turn.role == ROLE_TOOL:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": turn.tool_call_id,
                                "content": turn.text,
                            }
                        ],
                    }
                )
                continue

            blocks: list[dict[str, Any]] = []
            for image in turn.images:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image.mime_type,
                            "data": image.b64,
                        },
                    }
                )
            if turn.text:
                blocks.append({"type": "text", "text": turn.text})
            for index, call in enumerate(turn.tool_calls):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.call_id or f"call_{index}",
                        "name": call.name,
                        "input": call.arguments if isinstance(call.arguments, dict) else {},
                    }
                )
            if blocks:
                messages.append(
                    {
                        "role": "assistant" if turn.role == ROLE_ASSISTANT else "user",
                        "content": blocks,
                    }
                )
        return messages

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": self._messages(request),
        }
        system_text = request.system_text
        if system_text:
            payload["system"] = system_text
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        tools: list[dict[str, Any]] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": relax_schema(tool.parameters),
            }
            for tool in request.tools
        ]

        if request.json_schema:
            # Anthropic kennt kein response_format. Der etablierte Weg ist ein
            # erzwungenes Werkzeug, dessen Eingabeschema die Antwort ist.
            tools.append(
                {
                    "name": "antwort",
                    "description": "Gib die Antwort in genau dieser Struktur zurueck.",
                    "input_schema": relax_schema(request.json_schema),
                }
            )
            payload["tool_choice"] = {"type": "tool", "name": "antwort"}

        if tools:
            payload["tools"] = tools
        return payload

    async def chat(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        *,
        timeout: float,
    ) -> ChatResponse:
        watch = Stopwatch()
        async with session.post(
            f"{provider.base_url}/messages",
            json=self._payload(request),
            headers=self.auth_headers(provider, api_key),
            params=self.auth_params(provider, api_key),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            status, body = await self._read(response)
            headers = dict(response.headers)
        self._raise_for_status(status, body, headers, self.api_style)

        try:
            data = json.loads(body)
        except json.JSONDecodeError as err:
            raise ProviderError(f"Antwort ist kein JSON: {err}", status=status, body=body) from err

        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        parsed: Any = None
        for block in data.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                name = block.get("name", "")
                arguments = block.get("input") or {}
                if request.json_schema and name == "antwort":
                    parsed = arguments
                else:
                    tool_calls.append(
                        ToolCall(
                            name=name,
                            arguments=arguments,
                            call_id=str(block.get("id") or ""),
                        )
                    )

        usage = data.get("usage") or {}
        result = ChatResponse(
            text="".join(texts) or (json.dumps(parsed, ensure_ascii=False) if parsed else ""),
            parsed=parsed,
            tool_calls=tuple(tool_calls),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            finish_reason=data.get("stop_reason"),
            status=status,
            rate_limit=parse_headers(headers, self.api_style),
            model=str(data.get("model") or request.model),
            structured_mode="tool" if request.json_schema else None,
        )
        result.total_s = watch.total_s
        return result

    async def list_models(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        *,
        timeout: float,
    ) -> list[str]:
        async with session.get(
            f"{provider.base_url}/models",
            headers=self.auth_headers(provider, api_key),
            params=self.auth_params(provider, api_key),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            status, body = await self._read(response)
            self._raise_for_status(status, body, response.headers, self.api_style)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []
        return [
            str(item["id"])
            for item in (data.get("data") or [])
            if isinstance(item, dict) and item.get("id")
        ]

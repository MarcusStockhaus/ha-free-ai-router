"""OpenAI-kompatibler Dialekt (Groq, OpenRouter, OpenCode Zen)."""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from ..ratelimit import parse_headers
from ..schema_tools import describe_schema, relax_schema, to_strict_openai_schema
from .base import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ChatRequest,
    ChatResponse,
    ProviderAdapter,
    ProviderError,
    Stopwatch,
    ToolCall,
    iter_sse,
)

# Diese Fehlertexte heissen: "Schema-Modus kann ich nicht" — nicht "kaputt".
_SCHEMA_REJECTED = (
    "response_format",
    "json_schema",
    "unsupported",
    "not supported",
    "invalid_request_error",
)


class OpenAICompatAdapter(ProviderAdapter):
    api_style = "openai_compatible"

    # ---------------------------------------------------------- Nachrichten
    def _messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for turn in request.turns:
            if turn.role == ROLE_TOOL:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": turn.tool_call_id,
                        "content": turn.text,
                    }
                )
                continue

            if turn.role == ROLE_ASSISTANT:
                entry: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
                if turn.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.call_id or f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": _as_arguments(call.arguments),
                            },
                        }
                        for index, call in enumerate(turn.tool_calls)
                    ]
                messages.append(entry)
                continue

            if turn.images:
                content: list[dict[str, Any]] = [{"type": "text", "text": turn.text}]
                for image in turn.images:
                    content.append({"type": "image_url", "image_url": {"url": image.data_url}})
                messages.append({"role": turn.role, "content": content})
            else:
                messages.append({"role": turn.role, "content": turn.text})
        return messages

    def _payload(
        self, request: ChatRequest, *, structured_mode: str, stream: bool
    ) -> dict[str, Any]:
        messages = self._messages(request)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if stream:
            payload["stream"] = True

        if request.json_schema:
            if structured_mode == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "antwort",
                        "strict": True,
                        "schema": to_strict_openai_schema(request.json_schema),
                    },
                }
            elif structured_mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
                self._append_schema_hint(messages, request.json_schema)
            else:
                self._append_schema_hint(messages, request.json_schema)

        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": relax_schema(tool.parameters),
                    },
                }
                for tool in request.tools
            ]
            payload["tool_choice"] = "auto"

        return payload

    @staticmethod
    def _append_schema_hint(messages: list[dict[str, Any]], schema: dict[str, Any]) -> None:
        hint = (
            "Antworte ausschliesslich mit JSON nach genau diesem Schema, "
            f"ohne Erklaerung und ohne Codeblock: {describe_schema(schema)}"
        )
        messages.append({"role": "system", "content": hint})

    # -------------------------------------------------------------- Aufruf
    async def chat(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        *,
        timeout: float,
    ) -> ChatResponse:
        modes = self._modes(request)
        last_error: ProviderError | None = None

        for mode in modes:
            watch = Stopwatch()
            try:
                status, body, headers = await self._post(
                    session, provider, api_key, request, mode, timeout, stream=False
                )
            except ProviderError as err:
                # Nur beim Schema-Modus lohnt ein zweiter Versuch. Ein 429
                # oder ein toter Key wird durch einen anderen Modus nicht besser.
                if self._should_downgrade(err, mode, modes):
                    last_error = err
                    continue
                raise
            response = self._parse(body, headers, status, request, mode)
            response.total_s = watch.total_s
            return response

        assert last_error is not None
        raise last_error

    async def chat_streaming(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        *,
        timeout: float,
    ) -> ChatResponse:
        mode = self._modes(request)[0]
        payload = self._payload(request, structured_mode=mode, stream=True)
        watch = Stopwatch()
        url = f"{provider.base_url}/chat/completions"

        async with session.post(
            url,
            json=payload,
            headers={**self.auth_headers(provider, api_key), "Accept": "text/event-stream"},
            params=self.auth_params(provider, api_key),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            if response.status >= 400:
                body = await response.text()
                self._raise_for_status(response.status, body, response.headers, self.api_style)

            chunks: list[str] = []
            finish_reason: str | None = None
            usage: dict[str, Any] = {}
            async for payload_text in iter_sse(response):
                if payload_text == "[DONE]":
                    break
                try:
                    event = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        watch.first_token()
                        chunks.append(piece)
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

            info = parse_headers(response.headers, self.api_style)

        text = "".join(chunks)
        result = ChatResponse(
            text=text,
            parsed=_maybe_json(text) if request.json_schema else None,
            finish_reason=finish_reason,
            status=200,
            rate_limit=info,
            model=request.model,
            structured_mode=mode if request.json_schema else None,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
        result.total_s = watch.total_s
        result.ttft_s = watch.ttft_s
        return result

    async def list_models(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        *,
        timeout: float,
    ) -> list[str]:
        url = f"{provider.base_url}/models"
        async with session.get(
            url,
            headers=self.auth_headers(provider, api_key),
            params=self.auth_params(provider, api_key),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            status, body = await self._read(response)
            self._raise_for_status(status, body, response.headers, self.api_style)
            try:
                data = json.loads(body)
            except json.JSONDecodeError as err:
                raise ProviderError(f"Modell-Liste unlesbar: {err}", status=status) from err
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return []
        return [
            str(item["id"])
            for item in entries
            if isinstance(item, dict) and item.get("id")
        ]

    # -------------------------------------------------------------- Helfer
    @staticmethod
    def _modes(request: ChatRequest) -> list[str]:
        if not request.json_schema:
            return ["none"]
        return ["json_schema", "json_object", "prompt"]

    @staticmethod
    def _should_downgrade(err: ProviderError, mode: str, modes: list[str]) -> bool:
        if mode == modes[-1]:
            return False
        if err.status != 400:
            return False
        haystack = f"{err.message} {err.body}".lower()
        return any(marker in haystack for marker in _SCHEMA_REJECTED)

    async def _post(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        mode: str,
        timeout: float,
        *,
        stream: bool,
    ) -> tuple[int, str, dict[str, str]]:
        payload = self._payload(request, structured_mode=mode, stream=stream)
        url = f"{provider.base_url}/chat/completions"
        async with session.post(
            url,
            json=payload,
            headers=self.auth_headers(provider, api_key),
            params=self.auth_params(provider, api_key),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            status, body = await self._read(response)
            headers = dict(response.headers)
        self._raise_for_status(status, body, headers, self.api_style)
        return status, body, headers

    def _parse(
        self,
        body: str,
        headers: dict[str, str],
        status: int,
        request: ChatRequest,
        mode: str,
    ) -> ChatResponse:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as err:
            raise ProviderError(f"Antwort ist kein JSON: {err}", status=status, body=body) from err

        if isinstance(data.get("error"), dict):
            # Manche Anbieter liefern Fehler mit HTTP 200.
            message = str(data["error"].get("message", data["error"]))
            raise ProviderError(message, status=status, body=body)

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("Antwort ohne choices", status=status, body=body)

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        text = content or ""

        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            raw_args = function.get("arguments") or "{}"
            tool_calls.append(
                ToolCall(
                    name=name,
                    arguments=_maybe_json(raw_args) or raw_args,
                    call_id=str(call.get("id") or ""),
                )
            )

        usage = data.get("usage") or {}
        return ChatResponse(
            text=text,
            parsed=_maybe_json(text) if request.json_schema else None,
            tool_calls=tuple(tool_calls),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            finish_reason=choices[0].get("finish_reason"),
            status=status,
            rate_limit=parse_headers(headers, self.api_style),
            model=str(data.get("model") or request.model),
            structured_mode=mode if request.json_schema else None,
        )


def _as_arguments(arguments: dict[str, Any] | str) -> str:
    """OpenAI erwartet die Argumente eines Werkzeugaufrufs als JSON-Text."""
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False)


def _maybe_json(text: str | None) -> Any:
    """Versuche, JSON aus der Antwort zu ziehen — auch aus einem Codeblock."""
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None

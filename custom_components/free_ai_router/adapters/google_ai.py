"""Googles generativelanguage-Dialekt (AI Studio / Gemini)."""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from ..ratelimit import parse_headers
from ..schema_tools import relax_schema, to_google_schema
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
    iter_sse,
)


class GoogleAdapter(ProviderAdapter):
    api_style = "google"

    # ---------------------------------------------------------- Nachrichten
    def _contents(self, request: ChatRequest) -> list[dict[str, Any]]:
        """Googles ``contents``: nur die Rollen ``user`` und ``model``.

        System-Anteile wandern in ``systemInstruction``, Werkzeugergebnisse
        kommen als ``functionResponse`` in einem User-Zug zurueck.
        """
        contents: list[dict[str, Any]] = []
        for turn in request.turns:
            if turn.role == ROLE_SYSTEM:
                continue

            if turn.role == ROLE_TOOL:
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": turn.tool_name,
                                    "response": {"result": turn.text},
                                }
                            }
                        ],
                    }
                )
                continue

            parts: list[dict[str, Any]] = []
            if turn.text:
                parts.append({"text": turn.text})
            for image in turn.images:
                parts.append({"inline_data": {"mime_type": image.mime_type, "data": image.b64}})
            for call in turn.tool_calls:
                arguments = call.arguments if isinstance(call.arguments, dict) else {}
                parts.append({"functionCall": {"name": call.name, "args": arguments}})
            if not parts:
                continue
            contents.append(
                {"role": "model" if turn.role == ROLE_ASSISTANT else "user", "parts": parts}
            )
        return contents

    def _payload(self, request: ChatRequest, *, thinking: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contents": self._contents(request),
            "generationConfig": {"maxOutputTokens": request.max_output_tokens},
        }
        if thinking and request.thinking_budget is not None:
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingBudget": request.thinking_budget
            }
        system_text = request.system_text
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if request.temperature is not None:
            payload["generationConfig"]["temperature"] = request.temperature

        if request.json_schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            payload["generationConfig"]["responseSchema"] = to_google_schema(request.json_schema)

        if request.tools:
            payload["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": to_google_schema(relax_schema(tool.parameters)),
                        }
                        for tool in request.tools
                    ]
                }
            ]

        return payload

    @staticmethod
    def _attempts(request: ChatRequest) -> tuple[bool, ...]:
        """Mit und ohne ``thinkingConfig`` — in dieser Reihenfolge.

        Gemma und einige Flash-Varianten kennen das Feld nicht und quittieren
        es mit 400, teils mit einer nichtssagenden Meldung ("Request contains
        an invalid argument"). Deshalb wird bei *jedem* 400 einmal ohne
        wiederholt, statt auf den Wortlaut zu hoffen. Eine Fahnenliste je
        Modell zu pflegen waere beim naechsten Modellwechsel wieder veraltet.
        """
        if request.thinking_budget is None:
            return (False,)
        return (True, False)

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
        watch = Stopwatch()
        url = f"{provider.base_url}/models/{request.model}:generateContent"

        for thinking in self._attempts(request):
            async with session.post(
                url,
                json=self._payload(request, thinking=thinking),
                headers=self.auth_headers(provider, api_key),
                params=self.auth_params(provider, api_key),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                status, body = await self._read(response)
                headers = dict(response.headers)

            if thinking and status == 400:
                continue
            break

        self._raise_for_status(status, body, headers, self.api_style)

        result = self._parse(body, headers, status, request)
        result.total_s = watch.total_s
        return result

    async def chat_streaming(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        *,
        timeout: float,
    ) -> ChatResponse:
        watch = Stopwatch()
        url = f"{provider.base_url}/models/{request.model}:streamGenerateContent"
        params = {**self.auth_params(provider, api_key), "alt": "sse"}

        chunks: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        tool_calls: list[ToolCall] = []

        # Derselbe Rueckfall wie in chat(): sonst faellt ein Modell ohne
        # thinkingConfig schon bei der Lebendpruefung durch und alle weiteren
        # Messungen unterbleiben.
        attempts = self._attempts(request)
        for index, thinking in enumerate(attempts):
            probe = await session.post(
                url,
                json=self._payload(request, thinking=thinking),
                headers=self.auth_headers(provider, api_key),
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
            if probe.status == 400 and index + 1 < len(attempts):
                probe.release()
                continue
            break

        async with probe as response:
            if response.status >= 400:
                body = await response.text()
                self._raise_for_status(response.status, body, response.headers, self.api_style)

            async for payload_text in iter_sse(response):
                try:
                    event = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                if event.get("usageMetadata"):
                    usage = event["usageMetadata"]
                for candidate in event.get("candidates") or []:
                    if candidate.get("finishReason"):
                        finish_reason = candidate["finishReason"]
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        if "text" in part and part["text"]:
                            watch.first_token()
                            chunks.append(part["text"])
                        if "functionCall" in part:
                            call = part["functionCall"]
                            tool_calls.append(
                                ToolCall(
                                    name=call.get("name", ""),
                                    arguments=call.get("args") or {},
                                )
                            )
            info = parse_headers(response.headers, self.api_style)

        text = "".join(chunks)
        result = ChatResponse(
            text=text,
            parsed=_maybe_json(text) if request.json_schema else None,
            tool_calls=tuple(tool_calls),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            finish_reason=finish_reason,
            status=200,
            rate_limit=info,
            model=request.model,
            structured_mode="response_schema" if request.json_schema else None,
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
            params={**self.auth_params(provider, api_key), "pageSize": "200"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            status, body = await self._read(response)
            self._raise_for_status(status, body, response.headers, self.api_style)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as err:
            raise ProviderError(f"Modell-Liste unlesbar: {err}", status=status) from err
        names = []
        for item in data.get("models") or []:
            name = str(item.get("name", ""))
            if name.startswith("models/"):
                name = name[len("models/") :]
            if name:
                names.append(name)
        return names

    # -------------------------------------------------------------- Helfer
    def _parse(
        self, body: str, headers: dict[str, str], status: int, request: ChatRequest
    ) -> ChatResponse:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as err:
            raise ProviderError(f"Antwort ist kein JSON: {err}", status=status, body=body) from err

        if isinstance(data.get("error"), dict):
            raise ProviderError(
                str(data["error"].get("message", data["error"])), status=status, body=body
            )

        candidates = data.get("candidates") or []
        if not candidates:
            # Sicherheitsfilter liefern eine leere Kandidatenliste mit Begruendung.
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason", "keine Kandidaten in der Antwort")
            raise ProviderError(f"Antwort verworfen: {reason}", status=status, body=body)

        candidate = candidates[0]
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in (candidate.get("content") or {}).get("parts") or []:
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(name=call.get("name", ""), arguments=call.get("args") or {})
                )

        text = "".join(texts)
        usage = data.get("usageMetadata") or {}
        return ChatResponse(
            text=text,
            parsed=_maybe_json(text) if request.json_schema else None,
            tool_calls=tuple(tool_calls),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            finish_reason=candidate.get("finishReason"),
            status=status,
            rate_limit=parse_headers(headers, self.api_style),
            model=str(data.get("modelVersion") or request.model),
            structured_mode="response_schema" if request.json_schema else None,
        )


def _maybe_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None

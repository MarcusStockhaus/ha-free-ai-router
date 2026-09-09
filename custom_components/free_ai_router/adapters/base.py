"""Gemeinsames Request-/Response-Modell aller Anbieterdialekte.

Alles hier ist HA-frei und arbeitet direkt auf einer ``aiohttp.ClientSession``.
In Home Assistant wird die Session der Instanz durchgereicht, im Probe-CLI
eine eigene.
"""

from __future__ import annotations

import abc
import base64
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from ..ratelimit import RateLimitInfo, parse_headers


class ProviderError(Exception):
    """Ein Aufruf ist fehlgeschlagen.

    ``retryable`` heisst: derselbe Anbieter koennte es spaeter schaffen.
    ``is_rate_limit`` heisst: der Router soll auf einen anderen Kanal wechseln
    und der Ledger soll das Fenster als erschoepft markieren.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        is_rate_limit: bool = False,
        is_auth: bool = False,
        rate_limit: RateLimitInfo | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable
        self.is_rate_limit = is_rate_limit
        self.is_auth = is_auth
        self.rate_limit = rate_limit or RateLimitInfo()
        self.body = body

    def __str__(self) -> str:
        if self.status is None:
            return self.message
        return f"HTTP {self.status}: {self.message}"


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """Ein Bild fuer die Anfrage. Rohbytes, damit jeder Dialekt selbst kodiert."""

    mime_type: str
    data: bytes

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.b64}"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Ein angebotenes Werkzeug. In Phase 1 nur fuer die Faehigkeitsmessung."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] | str
    call_id: str = ""


ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    """Ein Zug im Gespraech.

    Nur fuer die ``conversation``-Entity noetig: Assist braucht echte
    Mehrfachrunden mit Werkzeugaufrufen und deren Ergebnissen. Ein
    ``ai_task``-Aufruf bleibt einzuegig und benutzt ``instructions``.
    """

    role: str
    text: str = ""
    images: tuple[ImageAttachment, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Eine normalisierte Anfrage, unabhaengig vom Dialekt.

    Entweder ``instructions`` (einzuegig) oder ``messages`` (Gespraech).
    Sind ``messages`` gesetzt, gewinnen sie.
    """

    model: str
    instructions: str = ""
    system: str | None = None
    images: tuple[ImageAttachment, ...] = ()
    messages: tuple[Message, ...] = ()
    json_schema: dict[str, Any] | None = None
    tools: tuple[ToolSpec, ...] = ()
    max_output_tokens: int = 1024
    temperature: float | None = None
    thinking_budget: int | None = None
    """Denkbudget in Token. ``None`` = Voreinstellung des Anbieters, ``0`` = aus.

    Neuere Modelle verbrauchen einen Teil des Ausgabebudgets fuers Nachdenken.
    Bei einer kurzen, strukturierten Antwort (Kamerabild, Klassifikation) ist
    das reine Verschwendung: das Nachdenken frisst das Budget, und uebrig
    bleibt eine leere Antwort.
    """

    @property
    def turns(self) -> tuple[Message, ...]:
        """Einheitliche Sicht: immer eine Nachrichtenfolge."""
        if self.messages:
            return self.messages
        turns: list[Message] = []
        if self.system:
            turns.append(Message(role=ROLE_SYSTEM, text=self.system))
        turns.append(Message(role=ROLE_USER, text=self.instructions, images=self.images))
        return tuple(turns)

    @property
    def system_text(self) -> str:
        """Alle System-Anteile zusammengefasst — fuer Dialekte mit eigenem Feld."""
        parts = [turn.text for turn in self.turns if turn.role == ROLE_SYSTEM and turn.text]
        return "\n\n".join(parts)


@dataclass(slots=True)
class ChatResponse:
    """Eine normalisierte Antwort."""

    text: str = ""
    parsed: Any = None
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    status: int = 200
    rate_limit: RateLimitInfo = field(default_factory=RateLimitInfo)
    total_s: float = 0.0
    ttft_s: float | None = None
    model: str = ""
    structured_mode: str | None = None
    """Wie Structured Output erreicht wurde: json_schema, json_object, prompt."""


class ProviderAdapter(abc.ABC):
    """Ein API-Dialekt."""

    api_style: str

    # ---------------------------------------------------------------- Auth
    @staticmethod
    def auth_headers(provider: Any, api_key: str) -> dict[str, str]:
        headers: dict[str, str] = dict(provider.headers_extra)
        if provider.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif provider.auth_type == "header":
            headers[provider.auth_name] = api_key
        return headers

    @staticmethod
    def auth_params(provider: Any, api_key: str) -> dict[str, str]:
        if provider.auth_type == "query":
            return {provider.auth_name: api_key}
        return {}

    # ------------------------------------------------------------ Aufrufe
    @abc.abstractmethod
    async def chat(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        *,
        timeout: float,
    ) -> ChatResponse:
        """Fuehre die Anfrage aus und normalisiere die Antwort."""

    async def chat_streaming(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        request: ChatRequest,
        *,
        timeout: float,
    ) -> ChatResponse:
        """Wie :meth:`chat`, misst zusaetzlich die Zeit bis zum ersten Token.

        Dialekte ohne Streaming fallen auf :meth:`chat` zurueck; ``ttft_s``
        bleibt dann leer statt geraten zu werden.
        """
        return await self.chat(session, provider, api_key, request, timeout=timeout)

    async def list_models(
        self,
        session: aiohttp.ClientSession,
        provider: Any,
        api_key: str,
        *,
        timeout: float,
    ) -> list[str]:
        """Modell-IDs, die der Anbieter aktuell fuehrt. Leer, wenn unbekannt."""
        return []

    # ------------------------------------------------------------- Helfer
    @staticmethod
    def _raise_for_status(
        status: int, body: str, headers: Mapping[str, str], api_style: str
    ) -> None:
        if status < 400:
            return
        info = parse_headers(headers, api_style)
        snippet = body.strip()[:400] or "(leere Antwort)"
        if status == 429:
            raise ProviderError(
                f"Limit erreicht — {snippet}",
                status=status,
                retryable=True,
                is_rate_limit=True,
                rate_limit=info,
                body=body,
            )
        if status in (401, 403):
            raise ProviderError(
                f"Schluessel abgelehnt — {snippet}",
                status=status,
                is_auth=True,
                rate_limit=info,
                body=body,
            )
        raise ProviderError(
            snippet,
            status=status,
            retryable=status >= 500 or status == 408,
            rate_limit=info,
            body=body,
        )

    @staticmethod
    async def _read(response: aiohttp.ClientResponse) -> tuple[int, str]:
        return response.status, await response.text()


async def iter_sse(response: aiohttp.ClientResponse) -> AsyncIterator[str]:
    """Zerlege einen ``text/event-stream`` in seine ``data:``-Nutzlasten."""
    async for raw_line in response.content:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                yield payload


class Stopwatch:
    """Gesamtdauer und Zeit bis zum ersten Token."""

    __slots__ = ("_start", "ttft_s")

    def __init__(self) -> None:
        self._start = time.monotonic()
        self.ttft_s: float | None = None

    def first_token(self) -> None:
        if self.ttft_s is None:
            self.ttft_s = time.monotonic() - self._start

    @property
    def total_s(self) -> float:
        return time.monotonic() - self._start

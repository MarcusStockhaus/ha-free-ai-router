"""Ledger — Kontingentbuchhaltung je Schluessel und Zeitfenster.

Gespeist aus zwei Quellen:

* eigener Zaehlung (jede abgeschickte Anfrage),
* den Antwortheadern des Anbieters (``x-ratelimit-remaining-*``).

Der Anbieter hat recht: wo ein Header ein ``remaining`` nennt, uebernimmt der
Ledger diesen Wert, auch wenn die eigene Zaehlung guenstiger aussieht. Ein
selbst beobachteter 429 schlaegt beides und sperrt das Fenster hart.

Wichtiger als der Tageszaehler ist das Minutenfenster: fuenf gleichzeitig
ausloesende Bewegungsmelder reissen 15 RPM lange vor 1.500 RPD. Deshalb die
Warteschlange — einreihen und notfalls verwerfen, nicht in Fehler laufen.

Kein ``homeassistant``-Import: die Persistenz wird als Callback hereingereicht,
in HA ist das ein ``Store``. Das haelt das Modul testbar.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .ratelimit import RateLimitInfo
from .registry import Model, Provider

_LOGGER = logging.getLogger(__name__)

SaveCallback = Callable[[dict[str, Any]], Awaitable[None]]

#: So lange gilt ein beobachteter 429 als Sperre, wenn der Anbieter keine
#: Wartezeit nennt.
DEFAULT_COOLDOWN_S = 60.0
#: Nach so vielen Sekunden ohne Erfolg gilt ein Kanal als tot (harte Fehler).
DEAD_COOLDOWN_S = 300.0


def bucket_key(provider: Provider, model: Model) -> str:
    """Worauf sich das Kontingent bezieht.

    Google zaehlt je Modell, OpenRouter je Schluessel ueber alle Modelle
    hinweg. Wer das verwechselt, verplant entweder Kontingent oder rennt in
    ein Limit, das der Zaehler nicht kennt.
    """
    if provider.limits_scope == "per_key":
        return provider.id
    return f"{provider.id}/{model.id}"


def _day_key(tz_name: str, now: float) -> str:
    """Tagesschluessel in der Zeitzone des Anbieters, nicht der lokalen."""
    try:
        tzinfo = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        _LOGGER.warning("Unbekannte Zeitzone %r, weiche auf UTC aus", tz_name)
        tzinfo = UTC
    return datetime.fromtimestamp(now, tz=tzinfo).strftime("%Y-%m-%d")


@dataclass(slots=True)
class BucketState:
    """Zaehlerstand eines Kontingent-Topfes."""

    minute_start: float = 0.0
    minute_requests: int = 0
    minute_tokens: int = 0
    day_key: str = ""
    day_requests: int = 0
    day_tokens: int = 0
    blocked_until: float = 0.0
    """Harte Sperre nach einem 429 oder einem toten Kanal."""
    block_reason: str = ""
    remaining_requests: int | None = None
    """Vom Anbieter gemeldeter Rest — schlaegt die eigene Zaehlung."""
    remaining_at: float = 0.0
    consecutive_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "minute_start": self.minute_start,
            "minute_requests": self.minute_requests,
            "minute_tokens": self.minute_tokens,
            "day_key": self.day_key,
            "day_requests": self.day_requests,
            "day_tokens": self.day_tokens,
            "blocked_until": self.blocked_until,
            "block_reason": self.block_reason,
            "remaining_requests": self.remaining_requests,
            "remaining_at": self.remaining_at,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BucketState:
        state = cls()
        for name in cls.__slots__:
            if name in data:
                setattr(state, name, data[name])
        return state


@dataclass(frozen=True, slots=True)
class Availability:
    """Antwort auf: kann dieser Topf jetzt eine Anfrage tragen?"""

    ok: bool
    reason: str = ""
    wait_s: float = 0.0
    """Wartezeit, nach der es voraussichtlich wieder geht. 0 = sofort."""
    headroom: float = 1.0
    """Anteil freien Kontingents, 0.0 bis 1.0. Fuer die Rangfolge."""

    @property
    def queueable(self) -> bool:
        """Nur das Minutenfenster ist zu — Warten hilft."""
        return not self.ok and 0 < self.wait_s <= 120


class QuotaExhausted(RuntimeError):
    """Alle Fenster zu, laenger als die Wartefrist erlaubt."""


@dataclass
class Ledger:
    """Zaehlerstand aller Kontingent-Toepfe.

    Nicht threadsicher, aber asyncio-sicher: alle Aenderungen laufen im
    Event-Loop, die Warteschlange ueber ``asyncio.Lock`` je Topf.
    """

    save: SaveCallback | None = None
    buckets: dict[str, BucketState] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False)
    _dirty: bool = field(default=False, repr=False)

    # ------------------------------------------------------------ Zustand
    def bucket(self, key: str) -> BucketState:
        state = self.buckets.get(key)
        if state is None:
            state = BucketState()
            self.buckets[key] = state
        return state

    def _roll(self, state: BucketState, tz_name: str, now: float) -> None:
        """Fenster weiterdrehen, bevor gelesen oder gezaehlt wird."""
        if now - state.minute_start >= 60.0:
            state.minute_start = now
            state.minute_requests = 0
            state.minute_tokens = 0
        today = _day_key(tz_name, now)
        if state.day_key != today:
            state.day_key = today
            state.day_requests = 0
            state.day_tokens = 0

    # ------------------------------------------------------- Verfuegbarkeit
    def availability(
        self, provider: Provider, model: Model, *, now: float | None = None
    ) -> Availability:
        """Kann dieser Kanal jetzt bedient werden?"""
        now = time.time() if now is None else now
        key = bucket_key(provider, model)
        state = self.bucket(key)
        self._roll(state, provider.daily_reset_timezone, now)

        if state.blocked_until > now:
            return Availability(
                ok=False,
                reason=state.block_reason or "gesperrt",
                wait_s=state.blocked_until - now,
                headroom=0.0,
            )

        limits = model.limits
        headroom = 1.0

        # Tagesfenster zuerst: dagegen hilft kein Warten von Sekunden.
        if limits.rpd:
            used = state.day_requests
            if used >= limits.rpd:
                return Availability(
                    ok=False,
                    reason=f"Tageslimit erreicht ({used}/{limits.rpd})",
                    wait_s=_seconds_to_midnight(provider.daily_reset_timezone, now),
                    headroom=0.0,
                )
            headroom = min(headroom, 1.0 - used / limits.rpd)

        # Vom Anbieter gemeldeter Rest, solange er frisch ist. Aeltere
        # Angaben werden ignoriert: ein Header von vor einer Stunde sagt
        # nichts ueber das aktuelle Fenster.
        if (
            state.remaining_requests is not None
            and state.remaining_requests <= 0
            and now - state.remaining_at < 120
        ):
            return Availability(
                ok=False,
                reason="Anbieter meldet Rest 0",
                wait_s=DEFAULT_COOLDOWN_S,
                headroom=0.0,
            )

        if limits.rpm:
            used = state.minute_requests
            if used >= limits.rpm:
                wait = max(0.0, 60.0 - (now - state.minute_start))
                return Availability(
                    ok=False,
                    reason=f"Minutenlimit erreicht ({used}/{limits.rpm})",
                    wait_s=wait,
                    headroom=0.0,
                )
            headroom = min(headroom, 1.0 - used / limits.rpm)

        if limits.tpm and state.minute_tokens >= limits.tpm:
            wait = max(0.0, 60.0 - (now - state.minute_start))
            return Availability(
                ok=False,
                reason=f"Token-Minutenlimit erreicht ({state.minute_tokens}/{limits.tpm})",
                wait_s=wait,
                headroom=0.0,
            )

        return Availability(ok=True, headroom=headroom)

    # ----------------------------------------------------------- Buchungen
    def record_request(
        self,
        provider: Provider,
        model: Model,
        *,
        tokens: int = 0,
        now: float | None = None,
    ) -> None:
        """Eine abgeschickte Anfrage verbuchen — vor der Antwort.

        ``tokens`` ist die Schaetzung, nicht der Messwert; sie wird von
        :meth:`record_success` berichtigt.
        """
        now = time.time() if now is None else now
        state = self.bucket(bucket_key(provider, model))
        self._roll(state, provider.daily_reset_timezone, now)
        state.minute_requests += 1
        state.day_requests += 1
        if tokens:
            state.minute_tokens += tokens
            state.day_tokens += tokens
        self._dirty = True

    def absorb_headers(
        self,
        provider: Provider,
        model: Model,
        info: RateLimitInfo,
        *,
        now: float | None = None,
    ) -> None:
        """Antwortheader auswerten. Der Anbieter hat recht."""
        if info.is_empty:
            return
        now = time.time() if now is None else now
        state = self.bucket(bucket_key(provider, model))
        if info.remaining_requests is not None:
            state.remaining_requests = info.remaining_requests
            state.remaining_at = now
        self._dirty = True

    def record_success(
        self,
        provider: Provider,
        model: Model,
        *,
        tokens: int = 0,
        estimated_tokens: int = 0,
        info: RateLimitInfo | None = None,
        now: float | None = None,
    ) -> None:
        """Erfolg verbuchen und die Token-Schaetzung berichtigen.

        ``record_request`` bucht vorab eine Schaetzung, damit ein enges
        Tokenfenster (Gemma: 16k/Minute) schon *vor* dem Absenden bremst und
        nicht erst, nachdem der Anbieter mit 429 geantwortet hat. Hier kommt
        die Differenz zum tatsaechlichen Verbrauch dazu — oder wieder weg.
        """
        now = time.time() if now is None else now
        state = self.bucket(bucket_key(provider, model))
        state.consecutive_failures = 0
        state.blocked_until = 0.0
        state.block_reason = ""
        delta = tokens - estimated_tokens
        if delta:
            self._roll(state, provider.daily_reset_timezone, now)
            state.minute_tokens = max(0, state.minute_tokens + delta)
            state.day_tokens = max(0, state.day_tokens + delta)
        if info is not None:
            self.absorb_headers(provider, model, info, now=now)
        self._dirty = True

    def record_rate_limited(
        self,
        provider: Provider,
        model: Model,
        *,
        retry_after_s: float | None = None,
        now: float | None = None,
    ) -> None:
        """Ein selbst beobachteter 429 — schlaegt Registry und Feed."""
        now = time.time() if now is None else now
        state = self.bucket(bucket_key(provider, model))
        wait = retry_after_s if retry_after_s and retry_after_s > 0 else DEFAULT_COOLDOWN_S
        state.blocked_until = max(state.blocked_until, now + wait)
        state.block_reason = f"429 vom Anbieter, gesperrt fuer {wait:.0f} s"
        # Bewusst *kein* kuenstliches ``remaining_requests = 0``: die Sperre
        # steht bereits in ``blocked_until``. Ein gefaelschter Header-Rest
        # wuerde den Kanal ueber die Header-Frist von zwei Minuten blockieren
        # und damit ein "Retry-After: 5" auf 120 Sekunden verlaengern.
        self._dirty = True
        _LOGGER.warning(
            "%s/%s: Limit erreicht, %.0f s gesperrt", provider.id, model.id, wait
        )

    def record_failure(
        self,
        provider: Provider,
        model: Model,
        *,
        fatal: bool = False,
        reason: str = "",
        now: float | None = None,
    ) -> None:
        """Ein Fehlschlag, der kein Limit war.

        Erst nach mehreren Fehlschlaegen in Folge wird gesperrt: ein einzelner
        Netzhaenger darf keinen Kanal abschalten.
        """
        now = time.time() if now is None else now
        state = self.bucket(bucket_key(provider, model))
        state.consecutive_failures += 1
        if fatal or state.consecutive_failures >= 3:
            state.blocked_until = max(state.blocked_until, now + DEAD_COOLDOWN_S)
            state.block_reason = reason or "wiederholt fehlgeschlagen"
            _LOGGER.warning(
                "%s/%s: %s — %.0f s uebersprungen",
                provider.id,
                model.id,
                state.block_reason,
                DEAD_COOLDOWN_S,
            )
        self._dirty = True

    # -------------------------------------------------------- Warteschlange
    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def wait_for_slot(
        self,
        provider: Provider,
        model: Model,
        *,
        max_wait_s: float,
    ) -> Availability:
        """Reihe dich ein, bis das Minutenfenster wieder aufgeht.

        Gibt die letzte Verfuegbarkeit zurueck. Ist sie nicht ``ok``, war die
        Wartefrist zu kurz — dann verwirft der Aufrufer sauber, statt den
        Aufruf blind abzuschicken und einen 429 zu produzieren.
        """
        deadline = time.time() + max_wait_s
        async with self._lock(bucket_key(provider, model)):
            while True:
                available = self.availability(provider, model)
                if available.ok:
                    return available
                remaining = deadline - time.time()
                if remaining <= 0 or available.wait_s > remaining:
                    return available
                await asyncio.sleep(min(available.wait_s + 0.05, remaining))

    # ------------------------------------------------------------ Persistenz
    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "buckets": {key: state.as_dict() for key, state in self.buckets.items()},
        }

    def restore(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        buckets = data.get("buckets") or {}
        for key, raw in buckets.items():
            if isinstance(raw, dict):
                self.buckets[key] = BucketState.from_dict(raw)

    async def async_save(self, *, force: bool = False) -> None:
        if self.save is None or (not self._dirty and not force):
            return
        await self.save(self.snapshot())
        self._dirty = False

    # ------------------------------------------------------------- Auskunft
    def usage(self, provider: Provider, model: Model) -> dict[str, Any]:
        """Zaehlerstand fuer Diagnose und (Phase 2) Verbrauchssensoren."""
        state = self.bucket(bucket_key(provider, model))
        return {
            "bucket": bucket_key(provider, model),
            "minute_requests": state.minute_requests,
            "day_requests": state.day_requests,
            "day_key": state.day_key,
            "blocked_until": state.blocked_until,
            "block_reason": state.block_reason,
            "remaining_requests": state.remaining_requests,
            "limits": model.limits.as_dict(),
        }


def _seconds_to_midnight(tz_name: str, now: float) -> float:
    try:
        tzinfo = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tzinfo = UTC
    local = datetime.fromtimestamp(now, tz=tzinfo)
    tomorrow = local.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = (tomorrow.timestamp() + 86400) - now
    return max(seconds, 0.0)

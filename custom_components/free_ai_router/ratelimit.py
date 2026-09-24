"""Normalisierung der Rate-Limit-Header.

Die Header-Namen unterscheiden sich je Anbieter, teilweise je Endpunkt, und
sie aendern sich ohne Ankuendigung. Deshalb zwei Ebenen:

* ``raw`` — alles, was nach Rate-Limit aussieht, unveraendert mitgeschrieben.
  Das Probe-CLI gibt es aus; daraus entsteht die Normalisierungstabelle.
* die normalisierten Felder — eine Auswertung nach bestem Wissen. Was hier
  nicht erkannt wird, geht nicht verloren, es steht in ``raw``.

Kein ``homeassistant``-Import.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Header, die wir roh mitnehmen. Bewusst weit gefasst.
_INTERESTING = re.compile(
    r"^(x-)?(ratelimit|rate-limit|retry-after|x-request-id|"
    r"anthropic-ratelimit|x-groq)", re.IGNORECASE
)

# "1m0s", "7.66s", "2m59.56s", "1h30m" -> Sekunden
_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)m?s)?$")


def _parse_duration(value: str) -> float | None:
    """Groq liefert Reset-Zeiten als Dauer ('7.66s', '2m59.56s')."""
    text = value.strip().lower()
    if not text:
        return None
    try:
        return float(text)  # reine Sekundenzahl
    except ValueError:
        pass
    match = _DURATION.fullmatch(text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _parse_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


@dataclass(frozen=True, slots=True)
class RateLimitInfo:
    """Was die Antwortheader ueber das Kontingent verraten."""

    limit_requests: int | None = None
    remaining_requests: int | None = None
    reset_requests_s: float | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None
    retry_after_s: float | None = None
    requests_window_s: float | None = None
    """Fensterlaenge des Anfragelimits — nur gesetzt, wenn der Anbieter sie nennt.

    Mistral schreibt sie in den Headernamen (``x-ratelimit-limit-req-minute``).
    Aus dem Reset-Zeitpunkt laesst sie sich nicht ablesen: Groq meldet in
    ``x-ratelimit-limit-requests`` laut eigener Doku immer das Tageslimit,
    fuellt es aber laufend auf — der Reset liegt dann oft unter zwei Minuten,
    und aus 1.000 am Tag wurden 1.000 je Minute. Live am 24.09.2026 in den
    Messwerten gefunden.
    """
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.raw

    def as_dict(self) -> dict[str, Any]:
        return {
            "limit_requests": self.limit_requests,
            "remaining_requests": self.remaining_requests,
            "reset_requests_s": self.reset_requests_s,
            "limit_tokens": self.limit_tokens,
            "remaining_tokens": self.remaining_tokens,
            "reset_tokens_s": self.reset_tokens_s,
            "retry_after_s": self.retry_after_s,
            "requests_window_s": self.requests_window_s,
            "raw": dict(self.raw),
        }


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def parse_headers(headers: Mapping[str, str], api_style: str = "") -> RateLimitInfo:
    """Lies Rate-Limit-Angaben aus Antwortheadern.

    Bekannte Auspraegungen:

    * OpenAI/Groq: ``x-ratelimit-limit-requests``, ``-remaining-requests``,
      ``-reset-requests`` (Dauer), dito ``-tokens``.
    * OpenRouter: ``x-ratelimit-limit``, ``-remaining``, ``-reset``
      (Millisekunden seit Epoche).
    * Anthropic: ``anthropic-ratelimit-requests-remaining`` usw.
    * Google AI Studio: liefert nichts davon; ``raw`` bleibt leer.
    """
    lowered = _lower(headers)
    raw = {key: value for key, value in lowered.items() if _INTERESTING.match(key)}

    # Mistral kodiert das Fenster im Headernamen statt in einem Reset-Wert:
    # x-ratelimit-limit-req-minute. Das wird hier zuerst geprueft, weil es die
    # Fensterlaenge sicher verraet — genauer als jede Schaetzung aus einem
    # Reset-Zeitpunkt.
    named_window = _named_window(lowered)

    limit_requests = _parse_int(
        lowered.get("x-ratelimit-limit-requests")
        or lowered.get("anthropic-ratelimit-requests-limit")
        or lowered.get("x-ratelimit-limit")
        or lowered.get("ratelimit-limit")
    )
    remaining_requests = _parse_int(
        lowered.get("x-ratelimit-remaining-requests")
        or lowered.get("anthropic-ratelimit-requests-remaining")
        or lowered.get("x-ratelimit-remaining")
        or lowered.get("ratelimit-remaining")
    )
    if named_window is not None:
        limit_requests, remaining_requests, named_reset = named_window
    else:
        named_reset = None
    limit_tokens = _parse_int(
        lowered.get("x-ratelimit-limit-tokens")
        or lowered.get("anthropic-ratelimit-tokens-limit")
    )
    remaining_tokens = _parse_int(
        lowered.get("x-ratelimit-remaining-tokens")
        or lowered.get("anthropic-ratelimit-tokens-remaining")
    )

    reset_requests_s = named_reset
    if reset_requests_s is None:
        reset_requests_s = _reset_seconds(
            lowered.get("x-ratelimit-reset-requests")
            or lowered.get("x-ratelimit-reset")
            or lowered.get("ratelimit-reset")
        )
    reset_tokens_s = _reset_seconds(lowered.get("x-ratelimit-reset-tokens"))

    retry_after = lowered.get("retry-after")
    retry_after_s = _parse_duration(retry_after) if retry_after else None

    return RateLimitInfo(
        limit_requests=limit_requests,
        remaining_requests=remaining_requests,
        reset_requests_s=reset_requests_s,
        limit_tokens=limit_tokens,
        remaining_tokens=remaining_tokens,
        reset_tokens_s=reset_tokens_s,
        retry_after_s=retry_after_s,
        requests_window_s=named_reset,
        raw=raw,
    )


#: Fensterlaengen, die manche Anbieter in den Headernamen schreiben.
_NAMED_WINDOWS = (
    ("minute", 60.0),
    ("hour", 3600.0),
    ("day", 86400.0),
    ("month", 30 * 86400.0),
)


def _named_window(lowered: dict[str, str]) -> tuple[int | None, int | None, float] | None:
    """Werte Header vom Typ ``x-ratelimit-limit-req-minute`` aus.

    Bei Gleichstand gewinnt das kuerzeste Fenster: das Minutenfenster bremst
    zuerst, und danach richtet sich die Warteschlange.
    """
    for suffix, seconds in _NAMED_WINDOWS:
        limit_key = f"x-ratelimit-limit-req-{suffix}"
        if limit_key not in lowered:
            continue
        limit = _parse_int(lowered[limit_key])
        remaining = _parse_int(lowered.get(f"x-ratelimit-remaining-req-{suffix}"))
        return limit, remaining, seconds
    return None


def _reset_seconds(value: str | None) -> float | None:
    """Reset-Angaben kommen als Dauer, Sekunden oder Epoche-Millisekunden."""
    if not value:
        return None
    seconds = _parse_duration(value)
    if seconds is None:
        return None
    # OpenRouter liefert einen Epoche-Zeitstempel in Millisekunden. Alles
    # jenseits von ~11 Tagen kann keine Wartezeit sein.
    if seconds > 1_000_000:
        import time

        delta = seconds / 1000.0 - time.time()
        return max(delta, 0.0)
    return seconds

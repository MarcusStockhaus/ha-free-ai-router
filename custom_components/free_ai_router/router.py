"""Router — aus Anforderung, Registry und Ledger-Stand eine Rangfolge.

Bewusst eine reine Funktion: kein Netzzugriff, kein Zustand, keine Zeit ausser
der hereingereichten. Das ist der Kern der Integration und die Stelle, an der
die Tests hingehoeren.

Der Ledger wird nur ueber ein Callback gelesen (``availability_of``), damit
Tests Kontingentstaende frei setzen koennen, ohne einen Ledger zu bauen.

Rangfolge, in dieser Reihenfolge:

1. Wer sofort kann, vor dem, der warten muesste.
2. Bei ``schnell``: Latenzklasse — dafuer ist das Profil da.
3. Reihenfolge aus der Registry — sie ist die redaktionelle Vorauswahl
   ("Erste Wahl" vs. "Reserve") und schlaegt Rechenwerte.
4. Freies Restkontingent, absteigend.

Punkt 3 vor Punkt 4 ist Absicht: sonst wandert die Vision-Anfrage zur
ungetesteten Reserve, nur weil deren Zaehler noch auf null steht.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .const import PROFILE_REQUIREMENTS, PROFILES
from .ledger import Availability
from .registry import Model, Provider

AvailabilityLookup = Callable[[Provider, Model], Availability]

_LATENCY_RANK = {"sehr_schnell": 0, "schnell": 1, "normal": 2, "langsam": 3}

# Ablehnungsgruende — auch die Abschlussuebersicht im Config Flow liest sie.
REASON_PROFILE = "Profil passt nicht"
REASON_VISION = "kann keine Bilder"
REASON_TOOLS = "kann keine Werkzeuge"
REASON_STRUCTURED = "kann kein Structured Output"
REASON_CONTEXT = "Kontextfenster zu klein"
REASON_DISABLED = "abgeschaltet"


@dataclass(frozen=True, slots=True)
class Channel:
    """Ein eingerichteter Kanal: Anbieter plus konkretes Modell.

    ``enabled`` ist ``False``, wenn die Faehigkeitsmessung das Modell als tot
    gemeldet hat — die Registry-Datei bleibt davon unberuehrt.
    """

    provider: Provider
    model: Model
    enabled: bool = True

    @property
    def key(self) -> str:
        return self.model.key


@dataclass(frozen=True, slots=True)
class Requirements:
    """Was diese eine Anfrage braucht."""

    profile: str
    needs_vision: bool = False
    needs_tools: bool = False
    needs_structured_output: bool = False
    approx_input_tokens: int = 0

    @classmethod
    def for_profile(
        cls,
        profile: str,
        *,
        has_attachments: bool = False,
        has_structure: bool = False,
        needs_tools: bool = False,
        approx_input_tokens: int = 0,
    ) -> Requirements:
        """Leite die Anforderung aus Profil und tatsaechlicher Anfrage ab.

        Was die Anfrage mitbringt, zaehlt mehr als das Profil: ein Bild an der
        ``schnell``-Entity braucht Vision, auch wenn das Profil es nicht
        verlangt.
        """
        if profile not in PROFILES:
            raise ValueError(f"Unbekanntes Profil {profile!r}")
        base = PROFILE_REQUIREMENTS[profile]
        return cls(
            profile=profile,
            needs_vision=bool(base["vision"]) or has_attachments,
            needs_tools=bool(base["tools"]) or needs_tools,
            needs_structured_output=has_structure,
            approx_input_tokens=approx_input_tokens,
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    channel: Channel
    availability: Availability
    rank: tuple[int, int, int, float]

    @property
    def provider(self) -> Provider:
        return self.channel.provider

    @property
    def model(self) -> Model:
        return self.channel.model

    @property
    def key(self) -> str:
        return self.channel.key

    @property
    def immediate(self) -> bool:
        return self.availability.ok

    def __str__(self) -> str:
        if self.availability.ok:
            return f"{self.key} (frei {self.availability.headroom:.0%})"
        return f"{self.key} (wartet {self.availability.wait_s:.0f} s)"


@dataclass(frozen=True, slots=True)
class Rejection:
    key: str
    reason: str

    def __str__(self) -> str:
        return f"{self.key}: {self.reason}"


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    """Geordnete Kandidatenliste plus die Begruendung fuer alles Aussortierte."""

    profile: str
    candidates: tuple[Candidate, ...] = ()
    rejected: tuple[Rejection, ...] = ()

    @property
    def has_candidate(self) -> bool:
        return bool(self.candidates)

    @property
    def immediate(self) -> tuple[Candidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.immediate)

    @property
    def queued_only(self) -> bool:
        """Es gibt Kandidaten, aber alle muessten warten."""
        return bool(self.candidates) and not self.immediate

    @property
    def first(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def explain(self) -> str:
        """Eine Zeile fuers Log — welcher Kanal, welche Reserve, was fehlt."""
        if not self.candidates:
            reasons = ", ".join(str(item) for item in self.rejected[:4]) or "keine Kanaele"
            return f"{self.profile}: kein Kandidat ({reasons})"
        order = " -> ".join(str(candidate) for candidate in self.candidates[:4])
        return f"{self.profile}: {order}"


def _capability_reject(requirements: Requirements, channel: Channel) -> str:
    """Warum dieser Kanal ausscheidet — leerer Text heisst: er bleibt drin."""
    if not channel.enabled:
        return REASON_DISABLED
    model = channel.model
    caps = model.capabilities

    # Normalfall: das Modell muss fuer dieses Profil vorgesehen sein.
    # Ausnahme Bild: haengt an einer Anfrage ein Bild, zaehlt nur noch, wer
    # Bilder kann — dann darf auch ein Modell einspringen, das fuer dieses
    # Profil nicht vorgesehen war. Sonst scheitert die Kameraanalyse daran,
    # dass sie an der falschen Entity ausgeloest wurde.
    if not requirements.needs_vision and requirements.profile not in model.profiles:
        return REASON_PROFILE

    if requirements.needs_vision and not caps.vision:
        return REASON_VISION
    if requirements.needs_tools and not caps.tools:
        return REASON_TOOLS
    if requirements.needs_structured_output and not caps.structured_output:
        return REASON_STRUCTURED
    if requirements.approx_input_tokens and caps.context_tokens < requirements.approx_input_tokens:
        return REASON_CONTEXT
    return ""


def plan(
    requirements: Requirements,
    channels: Sequence[Channel],
    availability_of: AvailabilityLookup,
) -> RoutingPlan:
    """Bilde die geordnete Kandidatenliste.

    Kanaele am Limit fliegen nicht raus — sie rutschen nach hinten. Ob sich
    Warten lohnt, entscheidet der Aufrufer anhand von
    :attr:`Availability.queueable`.
    """
    candidates: list[Candidate] = []
    rejected: list[Rejection] = []

    for index, channel in enumerate(channels):
        reason = _capability_reject(requirements, channel)
        if reason:
            rejected.append(Rejection(key=channel.key, reason=reason))
            continue

        availability = availability_of(channel.provider, channel.model)
        latency = _LATENCY_RANK.get(channel.model.latency_class, 2)
        rank = (
            0 if availability.ok else 1,
            latency if requirements.profile == "schnell" else 0,
            index,
            -availability.headroom,
        )
        candidates.append(Candidate(channel=channel, availability=availability, rank=rank))

    candidates.sort(key=lambda candidate: candidate.rank)
    return RoutingPlan(
        profile=requirements.profile,
        candidates=tuple(candidates),
        rejected=tuple(rejected),
    )


def channels_for_profile(
    channels: Iterable[Channel], profile: str
) -> tuple[Channel, ...]:
    """Alle Kanaele, die dieses Profil laut Registry bedienen sollen."""
    return tuple(channel for channel in channels if profile in channel.model.profiles)


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """Was ein Profil bedient — Grundlage der Abschlussuebersicht."""

    profile: str
    primary: Channel | None = None
    reserves: tuple[Channel, ...] = ()
    blockers: tuple[Rejection, ...] = ()

    @property
    def covered(self) -> bool:
        return self.primary is not None

    @property
    def has_reserve(self) -> bool:
        return bool(self.reserves)


def coverage(
    channels: Sequence[Channel], availability_of: AvailabilityLookup
) -> dict[str, CoverageEntry]:
    """Welches Profil wird von wem bedient, wo bleibt eine Luecke?

    Zeigt der Config Flow am Ende und das Diagnose-Attribut der Entities.
    Bewusst ohne Ruecksicht auf momentane Kontingentstaende: die Frage lautet
    "gibt es ueberhaupt einen Kanal", nicht "ist er gerade frei".
    """
    result: dict[str, CoverageEntry] = {}
    for profile in PROFILES:
        requirements = Requirements.for_profile(profile)
        profile_plan = plan(requirements, channels, availability_of)
        ordered = [candidate.channel for candidate in profile_plan.candidates]
        result[profile] = CoverageEntry(
            profile=profile,
            primary=ordered[0] if ordered else None,
            reserves=tuple(ordered[1:]),
            blockers=profile_plan.rejected,
        )
    return result


__all__ = [
    "Candidate",
    "Channel",
    "CoverageEntry",
    "Rejection",
    "Requirements",
    "RoutingPlan",
    "channels_for_profile",
    "coverage",
    "plan",
]

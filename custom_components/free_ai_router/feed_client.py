"""Den Feed holen, pruefen und zwischenspeichern.

Trennung zu :mod:`feed`: dort steht das Format und alles, was ohne Netz
auskommt (und deshalb auch im Prober laeuft), hier das Holen. Wer das Format
verstehen will, liest :mod:`feed`; wer wissen will, wann geladen wird, liest
diese Datei.

Drei Dinge, die eine Abrufschleife gegen einen fremden Server braucht:

* **Bedingte Abrufe.** ``ETag`` und ``Last-Modified`` gehen bei jeder Anfrage
  zurueck. Ein unveraenderter Feed kostet damit eine 304-Antwort und keine
  Uebertragung — bei stuendlichem Takt ist das der Unterschied zwischen einem
  hoeflichen und einem laestigen Client.
* **Eine Obergrenze.** Der Feed ist eine kleine Datei. Alles jenseits von
  :data:`MAX_FEED_BYTES` wird abgebrochen, statt Speicher zu fuellen.
* **Ein Ausfall bleibt folgenlos.** Faellt der Abruf aus, laeuft die
  Integration mit der mitgelieferten Registry weiter. Der Feed ist eine
  Verbesserung, keine Voraussetzung — er darf den Start nie aufhalten.

Gespeichert wird das rohe Dokument samt Signatur, nicht das ausgewertete
Ergebnis. Beim Laden wird erneut geprueft. Das kostet Millisekunden und macht
den Zwischenspeicher zu einer reinen Kopie ohne eigene Autoritaet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .feed import (
    FEED_PUBLIC_KEY_B64,
    FEED_URL,
    FeedDocument,
    FeedError,
    apply_feed,
    parse_feed,
)
from .registry import Registry

_LOGGER = logging.getLogger(__name__)

#: Der Feed ist ein paar Dutzend Kilobyte. Eine Megabyte-Grenze ist reichlich
#: und verhindert trotzdem, dass eine falsch ausgelieferte Datei den Speicher
#: einer kleinen Installation fuellt.
MAX_FEED_BYTES = 1_000_000

#: Zeitbudget fuer Dokument und Signatur zusammen.
FEED_TIMEOUT_S = 30.0

#: Wie oft nachgesehen wird. Der Feed aendert sich hoechstens stuendlich;
#: sechsmal am Tag reicht und bleibt als Fussabdruck unauffaellig.
FEED_INTERVAL_HOURS = 4


class FeedUnavailable(Exception):
    """Der Feed war nicht erreichbar. Kein Grund, irgendetwas abzubrechen."""


@dataclass(slots=True)
class CachedFeed:
    """Was zwischen zwei Starts aufgehoben wird."""

    document: str = ""
    signature: str = ""
    etag: str = ""
    last_modified: str = ""
    fetched_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "signature": self.signature,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def restore(cls, data: dict[str, Any] | None) -> CachedFeed:
        if not isinstance(data, dict):
            return cls()
        return cls(
            document=str(data.get("document", "")),
            signature=str(data.get("signature", "")),
            etag=str(data.get("etag", "")),
            last_modified=str(data.get("last_modified", "")),
            fetched_at=str(data.get("fetched_at", "")),
        )


def feed_configured() -> bool:
    """Hat dieser Build ueberhaupt einen Feed?

    Beides muss im Quelltext stehen: die Adresse und der Schluessel. Fehlt
    eines, ist der Feed aus — und zwar ohne Meldung, denn dann ist es keine
    Stoerung, sondern der Bauzustand.
    """
    return bool(FEED_URL and FEED_PUBLIC_KEY_B64)


async def async_fetch(
    session: aiohttp.ClientSession,
    cache: CachedFeed,
    *,
    url: str = FEED_URL,
    timeout: float = FEED_TIMEOUT_S,
) -> CachedFeed | None:
    """Hole Dokument und Signatur. ``None`` heisst: unveraendert.

    Erst das Dokument, dann die Signatur — und beide aus demselben Abruf
    zusammen uebernommen. Eine halb aktualisierte Kopie (neues Dokument, alte
    Signatur) wuerde sonst als Faelschung erscheinen.
    """
    if not url:
        raise FeedUnavailable("kein Feed in diesem Build")

    headers: dict[str, str] = {}
    if cache.etag:
        headers["If-None-Match"] = cache.etag
    if cache.last_modified:
        headers["If-Modified-Since"] = cache.last_modified

    zeit = aiohttp.ClientTimeout(total=timeout)
    try:
        async with session.get(url, headers=headers, timeout=zeit) as response:
            if response.status == 304:
                return None
            if response.status != 200:
                raise FeedUnavailable(f"{url}: HTTP {response.status}")
            rohbytes = await _read_limited(response, url)
            etag = response.headers.get("ETag", "")
            last_modified = response.headers.get("Last-Modified", "")

        async with session.get(f"{url}.sig", timeout=zeit) as response:
            if response.status != 200:
                raise FeedUnavailable(f"{url}.sig: HTTP {response.status}")
            signatur = (await _read_limited(response, f"{url}.sig")).decode("ascii", "replace")
    except (TimeoutError, aiohttp.ClientError) as err:
        raise FeedUnavailable(f"{url}: {err!r}") from err

    return CachedFeed(
        document=rohbytes.decode("utf-8", "replace"),
        signature=signatur.strip(),
        etag=etag,
        last_modified=last_modified,
        fetched_at=datetime.now(UTC).isoformat(),
    )


async def _read_limited(response: aiohttp.ClientResponse, url: str) -> bytes:
    """Lies hoechstens :data:`MAX_FEED_BYTES` und brich sonst ab."""
    stueck: list[bytes] = []
    gelesen = 0
    async for block in response.content.iter_chunked(64 * 1024):
        gelesen += len(block)
        if gelesen > MAX_FEED_BYTES:
            raise FeedUnavailable(f"{url}: groesser als {MAX_FEED_BYTES} Bytes")
        stueck.append(block)
    return b"".join(stueck)


def verify_cached(
    cache: CachedFeed,
    *,
    now: datetime | None = None,
    seen_generated_at: datetime | None = None,
    public_key_b64: str = FEED_PUBLIC_KEY_B64,
) -> FeedDocument:
    """Pruefe den Zwischenspeicher und gib das Dokument zurueck.

    Wird bei jedem Laden aufgerufen, nicht nur beim Abruf: der Speicher ist
    eine Kopie, kein Beweis.
    """
    if not cache.document or not cache.signature:
        raise FeedError("nichts im Zwischenspeicher")
    return parse_feed(
        cache.document.encode("utf-8"),
        cache.signature,
        now=now,
        seen_generated_at=seen_generated_at,
        public_key_b64=public_key_b64,
    )


class FeedManager:
    """Haelt den Zwischenspeicher und liefert die angereicherte Registry.

    Der Aufrufer gibt das Speichern herein — genauso wie beim Ledger, damit
    dieses Modul nichts ueber Home Assistants ``Store`` wissen muss.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        save: Any = None,
        url: str = FEED_URL,
        public_key_b64: str = FEED_PUBLIC_KEY_B64,
    ) -> None:
        self._session = session
        self._save = save
        self._url = url
        self._public_key_b64 = public_key_b64
        self.cache = CachedFeed()
        self.document: FeedDocument | None = None
        self.last_error: str = ""

    def restore(self, data: dict[str, Any] | None) -> None:
        """Zwischenspeicher aus dem Store uebernehmen und sofort pruefen."""
        self.cache = CachedFeed.restore(data)
        if not self.cache.document:
            return
        try:
            self.document = verify_cached(self.cache, public_key_b64=self._public_key_b64)
        except FeedError as err:
            # Unbrauchbar heisst weg: ein abgelaufenes oder nicht mehr
            # passendes Dokument soll nicht bei jedem Start neu scheitern.
            _LOGGER.info("Gespeicherter Feed verworfen: %s", err)
            self.cache = CachedFeed()
            self.document = None

    async def async_update(self) -> bool:
        """Nachsehen, ob es etwas Neues gibt. ``True`` = uebernommen."""
        if not (self._url and self._public_key_b64):
            return False
        try:
            frisch = await async_fetch(self._session, self.cache, url=self._url)
        except FeedUnavailable as err:
            self.last_error = str(err)
            _LOGGER.debug("Feed nicht abrufbar: %s", err)
            return False

        if frisch is None:
            self.last_error = ""
            return False

        bisher = self.document.generated_at if self.document else None
        try:
            document = verify_cached(
                frisch,
                seen_generated_at=bisher,
                public_key_b64=self._public_key_b64,
            )
        except FeedError as err:
            # Eine ungueltige Antwort laesst den bisherigen Stand unberuehrt.
            # Wer den Feed uebernimmt, soll damit nichts loeschen koennen.
            self.last_error = str(err)
            _LOGGER.warning("Feed abgelehnt: %s", err)
            return False

        self.cache = frisch
        self.document = document
        self.last_error = ""
        if self._save is not None:
            await self._save(frisch.as_dict())
        _LOGGER.info(
            "Feed uebernommen: %s Anbieter, %s Messungen, erzeugt %s",
            len(document.providers),
            len(document.measurements),
            document.generated_at.isoformat(),
        )
        return True

    def apply(self, base: Registry) -> Registry:
        """Die mitgelieferte Registry, angereichert — oder unveraendert."""
        if self.document is None:
            return base
        try:
            return apply_feed(base, self.document)
        except (FeedError, ValueError) as err:  # pragma: no cover - Notbremse
            _LOGGER.warning("Feed liess sich nicht anwenden: %s", err)
            return base

    def diagnostics(self) -> dict[str, Any]:
        return {
            "aktiv": bool(self._url and self._public_key_b64),
            "quelle": self._url,
            "erzeugt_am": self.document.generated_at.isoformat() if self.document else None,
            "abgerufen_am": self.cache.fetched_at or None,
            "anbieter": len(self.document.providers) if self.document else 0,
            "messungen": len(self.document.measurements) if self.document else 0,
            "letzter_fehler": self.last_error or None,
        }


__all__ = [
    "FEED_INTERVAL_HOURS",
    "MAX_FEED_BYTES",
    "CachedFeed",
    "FeedManager",
    "FeedUnavailable",
    "async_fetch",
    "feed_configured",
    "verify_cached",
]

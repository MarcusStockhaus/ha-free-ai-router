"""Gemeinsame Basis der Entities: ein Blick auf die Laufzeit.

Kein gemeinsames Geraet mehr. Fruehr trugen alle Router-Entities (die drei
``ai_task``-Profile, Assist, die vier Gesamtzaehler) ein gemeinsames Geraet
"Free AI Router". Sobald Anbieter zu eigenen Untereintraegen wurden (siehe
``AnbieterSubentryFlow``), stand dieses eine Geraet als einziges ausserhalb
jedes Untereintrags — und Home Assistant zeigt dafuer auf der
Integrationsseite die Ueberschrift "Geraete, die nicht zu einem Untereintrag
gehoeren". Ein eigener Fake-Untereintrag nur fuer dieses Geraet wurde gebaut
und wieder verworfen: er gehoerte nicht auf die Ebene, weil er sich weder
hinzufuegen noch entfernen liess wie ein echter Anbieter.

Die sauberere Loesung, die Home Assistants eigene KI-Integrationen (etwa
Google Generative AI, OpenAI Conversation) fuer genau diesen Fall nutzen:
Entities ohne Geraetebezug auf Ebene des Config Entry. Sie tauchen dann unter
Einstellungen -> Entitaeten auf und in den jeweiligen Fachbereichen (AI-Task-
und Assist-Einstellungen), aber nicht als eigene Geraetezeile auf der
Integrationsseite — und loesen die Ueberschrift damit gar nicht erst aus.

Folge: der Anzeigename verliert den Geraete-Praefix. "Free AI Router Schnell"
wird zu "Schnell", weil Home Assistant den Geraetenamen nur voranstellt, wenn
die Entity ueberhaupt ein Geraet hat (siehe
``entity_registry._async_get_full_entity_name``). Die Entity-IDs aendern sich
dadurch nicht — sie sind laengst vergeben und explizit gesetzt.

Anbieter-Entities (``RouterAnbieterSensor``) setzen ihr eigenes Geraet direkt
nach diesem Konstruktor und sind von alldem nicht betroffen.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import FreeAIRouterConfigEntry, RouterRuntime, signal_kanaele

MANUFACTURER = "Free AI Router"


class RouterEntity(Entity):
    """Entity-Basis mit Zugriff auf Registry, Ledger und Router."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: FreeAIRouterConfigEntry) -> None:
        self._entry = entry
        self._last_channel: str | None = None
        self._last_reserve_used: bool = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Nach einer Messung im Hintergrund die Attribute neu schreiben —
        # sonst zeigt "abgeschaltet" bis zur naechsten Anfrage den alten Stand.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_kanaele(self._entry.entry_id), self.async_write_ha_state
            )
        )

    @property
    def runtime(self) -> RouterRuntime:
        return self._entry.runtime_data

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Sichtbar machen, welcher Kanal zuletzt geliefert hat.

        Ohne das bleibt jeder Wechsel auf die Reserve unsichtbar, solange
        niemand ins Log schaut — und dann faellt ein stiller Dauerausfall des
        Erstkanals erst auf, wenn auch die Reserve weg ist.
        """
        attributes: dict[str, Any] = {
            "zuletzt_genutzter_kanal": self._last_channel,
            "reserve_gegriffen": self._last_reserve_used,
        }
        attributes.update(self.runtime.diagnostics())
        return attributes

    def _note_channel(self, key: str, used_reserve: bool) -> None:
        self._last_channel = key
        self._last_reserve_used = used_reserve
        # Reparatur-Hinweise gleich mitnehmen. Der Zehn-Minuten-Takt allein
        # wuerde einen abgelehnten Schluessel bis zu zehn Minuten verschweigen
        # — und das ist genau die Zeit, in der still ueber die Reserve
        # weitergelaufen wird. Die Pruefung ist ein paar Wortvergleiche, das
        # kostet neben einem HTTP-Aufruf an einen KI-Anbieter nichts.
        from .issues import async_pruefen

        async_pruefen(self.hass, self.runtime)

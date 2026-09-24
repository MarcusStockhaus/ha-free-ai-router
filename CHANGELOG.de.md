# Änderungsprotokoll

[English](CHANGELOG.md) · **Deutsch**

## 0.5.0 — 24.09.2026

### Inkompatible Änderungen

Bezeichner sind jetzt in jeder Sprache englisch. Anzeigenamen,
Attributbezeichnungen, Einheiten und Meldungen folgen weiterhin der
Systemsprache.

| Bisher | Jetzt |
|---|---|
| `ai_task.free_ai_router_schnell` | `ai_task.free_ai_router_fast` |
| `ai_task.free_ai_router_bildanalyse` | `ai_task.free_ai_router_image_analysis` |
| `sensor.free_ai_router_anfragen_heute` | `sensor.free_ai_router_requests_today` |
| `sensor.free_ai_router_token_heute` | `sensor.free_ai_router_tokens_today` |
| `sensor.free_ai_router_reserve_gegriffen_heute` | `sensor.free_ai_router_fallbacks_today` |
| `sensor.free_ai_router_verworfen_heute` | `sensor.free_ai_router_failed_today` |
| `sensor.<anbieter>_anfragen_heute` | `sensor.<anbieter>_requests_today` |
| Dienst `free_ai_router.neu_vermessen` | `free_ai_router.remeasure` |
| Felder `anbieter`, `nur_lebendigkeit` | `providers`, `liveness_only` |
| Attribute `zuletzt_genutzter_kanal`, `reserve_gegriffen`, `kanaele`, `abgeschaltet`, `abdeckung` | `last_channel`, `used_fallback`, `channels`, `disabled`, `coverage` |
| Sensorattribute `tag`, `anfragen`, `token`, `reserve_gegriffen`, `verworfen` | `day`, `requests`, `tokens`, `fallbacks`, `failed` |
| Anbietersensor `modelle`, `gesperrt`, `ausgegeben_usd`, `rest_usd` | `models`, `blocked`, `spent_usd`, `remaining_usd` |
| je Modell `anfragen_heute`, `tageslimit`, `rest`, `aktiv` | `requests_today`, `daily_limit`, `remaining`, `enabled` |

`ai_task.free_ai_router_reasoning` und `conversation.free_ai_router_assist`
bleiben unverändert. Die Unique IDs bleiben ebenfalls, Verlauf und
Statistiken bleiben erhalten.

**Bestehende Installationen behalten ihre bisherigen Entity-IDs.** Wer
umstellen will, benennt die Entities unter **Einstellungen → Entities** um und
passt Automationen, Skripte und Dashboards an, die sie verwenden.
Dienstaufrufe und Templates mit Attributschlüsseln müssen in jedem Fall
angepasst werden.

### Neu

- Attributbezeichnungen auf Deutsch und Englisch.
- Dokumentation auf Deutsch und Englisch: README, Anleitung zum Mitwirken,
  Entwicklungshinweise, Änderungsprotokoll.

### Geändert

- Blueprints verwenden als Vorgabe `ai_task.free_ai_router_image_analysis`.
- Die Antwort des Dienstes hat englische Schlüssel: `providers`, `measured`,
  `alive`, `models`, `changes`, `note`, `no_longer_listed`, `duration_s`,
  `scope`.
- Die Diagnose hat englische Schlüssel: `providers`, `runtime`.

## 0.4.0 — 24.09.2026

### Neu

- **Modell-Wächter.** Eine tägliche GitHub-Action vergleicht die
  Modell-Listen der Anbieter mit den Anbieterdateien und meldet neue und
  eingestellte Modelle als ein Issue je Anbieter, mit einer ersten Messung
  neuer Modelle.
- **Englische Oberfläche** durchgehend: Einrichtungsdialog, Entity-Namen,
  Reparaturhinweise, Dienst, Fehlermeldungen, Benachrichtigungen,
  Messberichte und Anbieter-Anleitungen. Die Sprache folgt der Systemsprache
  von Home Assistant.
- Englische Blueprints `camera_analysis.yaml` und `doorbell.yaml`.
- Anbieterdateien führen `steps_en`, `data_note_en` und `summary_en`.

### Geändert

- Entity-IDs sind fest und werden nicht mehr aus dem übersetzten Namen
  gebildet.
- Einheiten folgen der Systemsprache.
- `strings.json` ist die englische Quelle.

### Entfernt

- Der signierte Anbieter-Feed und sein Client. Die Integration entfernt den
  Zwischenspeicher `.storage/free_ai_router.feed`. Aktualisierte
  Anbieterdaten kommen als Release über HACS.

### Behoben

- Anfragelimits wurden aus Reset-Zeiten abgeleitet; dadurch wurde Groqs
  Tageslimit von 1.000 als Minutenlimit gespeichert. Limits werden jetzt nur
  mit ausdrücklich genanntem Fenster übernommen; die Migration 2.2 der
  Konfiguration entfernt die gespeicherten Werte.

## 0.3.0 — 24.09.2026

### Geändert

- **Die Einrichtung speichert nach dem ersten Schlüssel.** Der Dialog hat zwei
  Schritte, Anbieter und Schlüssel. Der Abschlussbildschirm zeigt die
  voraussichtliche Profilabdeckung und empfiehlt den nächsten Anbieter.
  Weitere Anbieter kommen über **Anbieter hinzufügen** dazu.
- **Hintergrundmessung.** Fähigkeiten werden 10 Sekunden nach dem Laden und
  danach alle 6 Stunden gemessen, nur für fällige Modelle. Das Ergebnis kommt
  als Benachrichtigung je Anbieter.
- Gespeicherte Messwerte werden in die laufende Instanz übernommen, ohne die
  Integration neu zu laden.

### Behoben

- Vorübergehende Fehler (Serverfehler, Zeitüberschreitungen, Ratenlimits)
  markieren ein Modell nicht mehr als nicht nutzbar. Nicht nutzbare Modelle
  tragen einen Grund und werden nach 7 Tagen erneut versucht.
- In eine HTTP-200-Antwort eingepackte Upstream-Fehler zählten als
  Messergebnis.
- Der Messbericht enthält keine rohen Anbieterantworten mehr.
- Fehlender Text nach **Schlüssel ersetzen**.
- Veraltetes `via_device` an den Anbieter-Geräten entfernt.

## 0.2.0 — 16.09.2026

### Neu

- Installation über HACS als benutzerdefiniertes Repository.
- Der Schlüsseltest unterscheidet abgelehnten Schlüssel, fehlendes
  Abonnement, erreichtes Limit und nicht erreichbaren Anbieter.
- Empfohlene Anbieter sind in der Anbieterauswahl markiert.
- Blueprints melden eine fehlgeschlagene Analyse als Benachrichtigung.
- Diagnose-Download mit geschwärzten Schlüsseln.

### Geändert

- Die Namen der Router-Entities enthalten „Free AI Router“.
- Der Reserve-Sensor ist keine Diagnose-Entity mehr und erscheint auf
  automatisch erzeugten Dashboards.
- Reparaturhinweise verweisen auf die tatsächlichen Knöpfe (**Anbieter
  hinzufügen**, **Schlüssel ersetzen**).

### Behoben

- Die Einrichtung konnte enden, ohne den Eintrag anzulegen, wenn der
  Übersichtsschritt geschlossen wurde (17.09.2026).
- Menütitel zeigte `MISSING_VALUE` (17.09.2026).
- Fehlendes Requirement `voluptuous-openapi` verhinderte das Laden auf
  frischen Installationen (17.09.2026).

## 0.1.0 — 12.09.2026

Erste öffentliche Fassung.

- KI-Aufgaben-Entities für die Profile Schnell, Bildanalyse und Reasoning;
  Gesprächsagent für Assist mit Gerätesteuerung.
- Routing mit Reserve über Google AI Studio, Groq, Mistral und OpenRouter;
  Minuten-, Tages-, Token- und monatliche Ausgabenlimits.
- Fähigkeitsmessung mit dem Schlüssel des Nutzers.
- Anbieter als Untereinträge mit eigenem Gerät und Verbrauchssensor.
- Verbrauchssensoren, Reparaturhinweise, Dienst zum Neuvermessen.
- Blueprints für Kameraanalyse und Türklingel (Deutsch).

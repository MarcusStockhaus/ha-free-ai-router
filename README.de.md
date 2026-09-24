<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/free_ai_router/brand/dark_logo@2x.png">
    <img alt="Free AI Router" src="custom_components/free_ai_router/brand/logo@2x.png" width="420">
  </picture>
</h1>

[English](README.md) · **Deutsch**

KI-Funktionen für Home Assistant auf Basis der kostenlosen Stufen mehrerer
KI-Anbieter. Free AI Router bündelt Google AI Studio, Groq, Mistral und
OpenRouter hinter den Standard-KI-Schnittstellen von Home Assistant, leitet
jede Anfrage an ein passendes Modell und weicht automatisch auf einen anderen
Anbieter aus, wenn ein Modell ausfällt oder sein Limit erreicht. Eine lokale
GPU ist nicht nötig — die Integration läuft auf Home Assistant Green, Yellow,
einem Raspberry Pi oder einem kleinen NUC.

---

## Funktionen

- **Standard-Entities von Home Assistant.** Drei
  [KI-Aufgaben](https://www.home-assistant.io/integrations/ai_task/)-Entities und
  ein [Gesprächsagent](https://www.home-assistant.io/integrations/conversation/)
  für Assist. Jede Automation und jeder Blueprint, der eine KI-Aufgaben-Entity
  auswählen lässt, funktioniert damit.
- **Routing mit automatischer Reserve.** Jede Anfrage geht an das beste
  verfügbare Modell für die Aufgabe. Ist ein Modell überlastet, lehnt es den
  Schlüssel ab oder erreicht es ein Limit, übernimmt das nächste passende
  Modell — wenn nötig bei einem anderen Anbieter.
- **Kontingentverwaltung.** Minuten-, Tages- und Tokenlimits sowie monatliche
  Ausgabendeckel werden je Modell mitgezählt. Anfragen, die nur an ein
  Minutenlimit stoßen, werden kurz eingereiht statt abgewiesen.
- **Fähigkeiten mit dem eigenen Schlüssel gemessen.** Bildverarbeitung,
  strukturierte Ausgabe, Werkzeugaufrufe und Antwortzeit werden für jedes
  Modell im Hintergrund gemessen. Die Messung zeigt, was das eigene Konto
  tatsächlich kann, und gilt vor veröffentlichten Angaben.
- **Einrichtung in Sekunden.** Anbieter wählen, Schlüssel einfügen, fertig.
  Weitere Anbieter kommen einzeln auf der Integrationsseite dazu.
- **Überwachung.** Verbrauchssensoren, Reparaturhinweise bei abgelehnten
  Schlüsseln, nicht abgedeckten Profilen und aufgebrauchten Budgets sowie eine
  Diagnosedatei zum Herunterladen.
- **Blueprints** für Kameraanalyse bei Bewegung und für Türklingeln, auf
  Deutsch und Englisch.
- **Deutsch und Englisch** in der gesamten Oberfläche.

## Anwendungsfälle

| Anwendungsfall | Entity | Beispiel |
|---|---|---|
| Kameraanalyse | `ai_task.free_ai_router_image_analysis` | Bewegung in der Einfahrt → „Ein Lieferwagen, eine Person mit einem Paket.“ Mit strukturierten Feldern wie `person`, `fahrzeug`, `paket` für Bedingungen. |
| Türklingel | `ai_task.free_ai_router_image_analysis` | Es klingelt → ein Satz dazu, wer vor der Tür steht, als Ansage auf einem Lautsprecher oder als Nachricht aufs Handy. |
| Sprachsteuerung | `conversation.free_ai_router_assist` | Assist mit einem Sprachmodell, das frei formulierte Anweisungen versteht und freigegebene Geräte steuert. |
| Textaufgaben in Automationen | `ai_task.free_ai_router_fast` | Kalender und Wetter des Tages zusammenfassen, eingehende Benachrichtigungen einordnen, Freitext in strukturierte Daten umwandeln. |
| Anspruchsvolle Aufgaben | `ai_task.free_ai_router_reasoning` | Aufgaben mit großem Kontext oder mehreren Denkschritten, etwa Automationslogik aus einer Beschreibung entwerfen. |

## Voraussetzungen

- Home Assistant 2025.6 oder neuer
- Eine Internetverbindung
- Ein kostenloses Konto bei mindestens einem unterstützten Anbieter. Google AI
  Studio und Groq decken zusammen alle Profile mit Reserve ab und verlangen
  keine Zahlungsdaten.

---

## Installation

### HACS

1. HACS → Menü (⋮) → **Benutzerdefinierte Repositories**
2. Repository `https://github.com/MarcusStockhaus/ha-free-ai-router`,
   Typ **Integration**
3. **Free AI Router** suchen, herunterladen und Home Assistant neu starten

### Manuell

`custom_components/free_ai_router` aus diesem Repository nach
`/config/custom_components/` kopieren und Home Assistant neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Free AI
Router**, oder [Einrichtung direkt öffnen](https://my.home-assistant.io/redirect/config_flow_start/?domain=free_ai_router).

1. **Anbieter wählen.** Jede Anbieterkarte nennt die Fähigkeiten, den Umgang
   mit den Daten, ob Zahlungsdaten verlangt werden und ob der Anbieter für den
   Anfang empfohlen ist.
2. **Schlüssel eingeben.** Die Karte verlinkt direkt auf die Key-Seite des
   Anbieters. Der Schlüssel wird sofort mit einem echten Aufruf geprüft. Geht
   er durch, ist die Integration eingerichtet.

Die Fähigkeitsmessung läuft danach im Hintergrund und dauert je Anbieter ein
bis zwei Minuten. Das Ergebnis kommt als Benachrichtigung. Bis dahin gelten
die Werte aus der Anbieterdatei, die Integration ist also sofort nutzbar.

Weitere Anbieter kommen auf der Integrationsseite über **Anbieter
hinzufügen** dazu. Jeder Anbieter erscheint dort als eigener Eintrag, mit
**Schlüssel ersetzen** und **Löschen**.

---

## Entities

| Entity | Zweck |
|---|---|
| `ai_task.free_ai_router_fast` | Profil Schnell: kurze Aufgaben, Zusammenfassungen, Einordnung |
| `ai_task.free_ai_router_image_analysis` | Profil Bildanalyse: Kamerabilder |
| `ai_task.free_ai_router_reasoning` | Profil Reasoning: großer Kontext, mehrstufige Aufgaben |
| `conversation.free_ai_router_assist` | Gesprächsagent für Assist, mit Gerätesteuerung |

Entity-IDs, Attributschlüssel und der Dienst sind in jeder Sprache gleich und
deshalb englisch. Blueprints und Automationen lassen sich damit zwischen
Installationen austauschen. Anzeigenamen, Attributbezeichnungen, Einheiten und
Meldungen folgen der Systemsprache von Home Assistant (Deutsch oder Englisch).

Jede dieser Entities führt folgende Attribute:

| Attribut | Bedeutung |
|---|---|
| `last_channel` | Anbieter und Modell, die die letzte Anfrage beantwortet haben |
| `used_fallback` | Ob die letzte Anfrage von einer Reserve übernommen wurde |
| `channels` | Modelle, die gerade genutzt werden |
| `disabled` | Modelle, die nach der Messung ausgeschlossen sind |
| `coverage` | Erste Wahl je Profil |

> **Aktualisierung von 0.4 oder älter:** Bestehende Installationen behalten
> ihre bisherigen deutschen Entity-IDs (etwa `ai_task.free_ai_router_bildanalyse`).
> Umbenennen lassen sie sich unter **Einstellungen → Entities**; Automationen,
> die sie verwenden, müssen gleichzeitig angepasst werden. Siehe
> [Änderungsprotokoll](CHANGELOG.de.md).

## Verwendung

### In einer Automation

```yaml
action: ai_task.generate_data
data:
  entity_id: ai_task.free_ai_router_image_analysis
  task_name: Einfahrt
  instructions: Ist eine Person oder ein Fahrzeug zu sehen? Antworte in einem Satz.
  attachments:
    media_content_id: media-source://camera/camera.einfahrt
    media_content_type: image/jpeg
response_variable: ergebnis
```

Mit einer `structure` kommt die Antwort als Felder statt als Text zurück — zum
Beispiel `person: true` — und lässt sich direkt in Bedingungen verwenden.

### Assist

[Sprachassistenten öffnen](https://my.home-assistant.io/redirect/voice_assistants/),
einen Assistenten auswählen und **Free AI Router Assist** als Gesprächsagenten
einstellen. Auf derselben Seite wird auch die bevorzugte KI-Aufgaben-Entity für
Automationen festgelegt.

### Blueprints

| Blueprint | Import |
|---|---|
| Kameraanalyse bei Bewegung | [Deutsch](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fkamera_analyse.yaml) · [Englisch](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fcamera_analysis.yaml) |
| Türklingel-Analyse | [Deutsch](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Ftuerklingel.yaml) · [Englisch](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fdoorbell.yaml) |

Beide Blueprints machen die Sperrzeit zur Pflichtangabe: Die Zahl der
Auslösungen ist der einzige wirksame Hebel, um Kontingent zu sparen, denn die
Anbieter berechnen ein Bild unabhängig von seiner Auflösung etwa gleich. Beide
melden außerdem einen Fehlschlag — etwa eine Kamera ohne Standbild — als
Benachrichtigung, statt nichts zu tun.

---

## Anbieter

Werte der kostenlosen Stufen, Stand September 2026. Limits gelten je Modell,
sofern nicht anders angegeben.

| Anbieter | Modelle | Anfragen pro Tag | Hinweise |
|---|---|---:|---|
| Google AI Studio | Gemini 3.5 / 3.1 Flash-Lite | je 500 | Hauptkanal für Bildanalyse. Inhalte werden zur Produktverbesserung genutzt. |
| | Gemma 4 31B / 26B | je 14.400 | Großer Puffer, 16.000 Token pro Minute. |
| | Gemini 3.5 / 3.8 Flash | je 20 | Profil Reasoning. |
| Groq | GPT-OSS 20B / 120B, Qwen3.8 27B | je 1.000 | Geringste Latenz, bevorzugt für Assist. 8.000 Token pro Minute. Kein Training auf Inhalten der kostenlosen Stufe. |
| Mistral | Ministral 3B / 8B, Mistral Small, Codestral | nicht veröffentlicht | Hohe Minutenlimits, gedeckelt auf 10 US-Dollar Nutzung im Monat. Inhalte werden zum Training verwendet. Setzt das API-Abonnement mit Plan FREE voraus. |
| OpenRouter | Nemotron-Modelle (kostenlos) | 50 je Schlüssel | Reserve außerhalb von Google. |

Gemessenes Verhalten, das bei der Planung von Automationen hilft:

- Ein Bild kostet rund 1.100 Token, unabhängig von seiner Auflösung.
  Verkleinern senkt die Kosten nicht.
- Die größeren Gemini-Flash-Modelle erlauben nur 20 Anfragen am Tag. Die
  Tageslast tragen die Flash-Lite-Modelle.
- Mehrere Anbieter bewerben kostenlose Stufen, die über die API nicht nutzbar
  sind. Deshalb misst die Integration, statt sich auf Dokumentation zu
  verlassen.

## Reserve und Limits

1. Die Modelle werden geordnet: verfügbare vor solchen, die warten müssten;
   im Profil Schnell nach Antwortzeit, sonst nach der gepflegten Reihenfolge der
   Anbieterdatei, danach nach verbleibendem Kontingent.
2. Ist nur ein Minutenlimit erreicht, wartet die Anfrage bis zu 20 Sekunden.
3. Tageslimits und monatliche Ausgabendeckel führen sofort zum nächsten
   Modell. Tageszähler laufen in der Zeitzone des Anbieters.
4. Ein abgelehnter Schlüssel sperrt das betroffene Modell sofort, statt es bei
   jeder Anfrage erneut zu versuchen, und löst einen Reparaturhinweis aus. Die
   Sperre endet, sobald eine Anfrage mit diesem Schlüssel wieder durchgeht.
5. Erst wenn kein Modell übrig ist, schlägt der Aufruf fehl, mit einer Meldung,
   die jeden Versuch nennt. Automationen fangen das mit
   `continue_on_error: true` ab.

## Fähigkeitsmessung

Die Hintergrundmessung läuft zehn Sekunden nach jedem Start und danach alle
sechs Stunden. Sie misst nur, was offen ist: neue Modelle, Modelle, die
vorübergehend nicht erreichbar waren, und Modelle, die seit einer Woche als
nicht nutzbar gelten. Funktionierende Modelle werden nicht periodisch neu
vermessen — ob sie antworten, zeigt der laufende Betrieb, ohne Kontingent zu
verbrauchen.

Der Dienst `free_ai_router.remeasure` („Neu vermessen“) misst auf Anforderung
alle Modelle:

```yaml
action: free_ai_router.remeasure
data:
  providers: [groq]     # optional, Vorgabe: alle eingerichteten Anbieter
  liveness_only: false  # optional, eine Anfrage je Modell statt aller Prüfungen
response_variable: bericht
```

Die Antwort nennt je Anbieter, wie viele Modelle gemessen wurden und
erreichbar sind, eine kurze Zeile je Modell und was sich geändert hat.
Ergebnisse, die nur auf einer vorübergehenden Störung beruhen (Netzfehler,
Serverfehler, Ratenlimit), überschreiben keine früheren Messungen.

## Sensoren

| Sensor | Bedeutung |
|---|---|
| `sensor.free_ai_router_requests_today` | Anfragen heute |
| `sensor.free_ai_router_tokens_today` | Token heute |
| `sensor.free_ai_router_fallbacks_today` | Anfragen, die heute eine Reserve übernommen hat |
| `sensor.free_ai_router_failed_today` | Anfragen, die kein Modell übernehmen konnte |
| `sensor.<anbieter>_requests_today` | Anfragen je Anbieter, etwa `sensor.groq_requests_today` |

Die Anbietersensoren führen den Stand jedes Modells im Attribut `models`
(`requests_today`, `daily_limit`, `remaining`, `enabled`), aktive Sperren in
`blocked` und — bei Anbietern mit monatlichem Ausgabendeckel — `budget_usd`,
`spent_usd` und `remaining_usd`.

Ein steigender Reserve-Zähler ist das früheste Zeichen dafür, dass ein
Hauptkanal dauerhaft ausgefallen ist.

```yaml
type: entities
title: Free AI Router
entities:
  - sensor.free_ai_router_requests_today
  - sensor.free_ai_router_tokens_today
  - sensor.free_ai_router_fallbacks_today
  - sensor.free_ai_router_failed_today
```

## Fehlersuche

- **Diagnose:** Integrationsseite → Menü → **Diagnose herunterladen**. Die
  Datei enthält Laufzeitzustand und Zählerstände; API-Schlüssel sind
  geschwärzt.
- **Schlüsseltest bei der Einrichtung** unterscheidet vier Fälle: Schlüssel
  abgelehnt, kein Kontingent für das Konto freigeschaltet, Limit erreicht und
  Anbieter nicht erreichbar. Jede Meldung nennt den nächsten Schritt.
- **Reparaturen** (Einstellungen → Reparaturen) melden abgelehnte Schlüssel,
  Profile ohne Modell und aufgebrauchte Monatsbudgets.

---

## Datenschutz und Sicherheit

- Anfrageinhalte gehen nur an den Anbieter, der die Anfrage bearbeitet. Jede
  Anbieterkarte nennt, wie dieser Anbieter die Daten verwendet.
- Keine Telemetrie, keine Affiliate-Links.
- Die Endpunkte der Anbieter sind durch eine im Code fest hinterlegte
  Allowlist beschränkt. Eine Anbieterdatei kann kein neues Ziel für Daten
  einführen.

## Einschränkungen

- Eine Internetverbindung ist erforderlich. Für sicherheitskritische
  Funktionen nicht geeignet.
- Die kostenlosen Stufen legen die Anbieter fest; sie können sich jederzeit
  ändern.
- Konten müssen selbst angelegt werden; Captcha und Nutzungsbedingungen
  verhindern eine Automatisierung.

## Aktualisierungen

Eine regelmäßige Prüfung vergleicht die von den Anbietern veröffentlichten
Modell-Listen mit den Anbieterdateien und meldet neue oder eingestellte
Modelle. Aktualisierte Anbieterdateien erscheinen als Update über HACS. Jede
Installation misst neue Modelle danach mit dem eigenen Schlüssel.

## Unterstützung

Ein Community-Projekt ohne Supportzusage. Issues und Pull Requests sind
willkommen. Ein neuer Anbieter braucht nur eine YAML-Datei — siehe
[CONTRIBUTING.de.md](CONTRIBUTING.de.md). Aufbau, Werkzeuge und gemessene
Befunde beschreibt [DEVELOPMENT.de.md](DEVELOPMENT.de.md), die Änderungen je
Version das [Änderungsprotokoll](CHANGELOG.de.md).

## Lizenz

[MIT](LICENSE)

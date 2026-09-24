# Entwicklung

[English](DEVELOPMENT.md) · **Deutsch**

Aufbau, Werkzeuge und die gemessenen Befunde hinter den
Entwurfsentscheidungen. Für Nutzer: [README](README.de.md); für
Anbieterdateien: [CONTRIBUTING.de.md](CONTRIBUTING.de.md).

---

## Aufbau

```
custom_components/free_ai_router/
├── __init__.py        Setup, Laufzeit, Migrationen, Update-Listener
├── config_flow.py     Einrichtungsdialog und Anbieter-Untereinträge
├── registry.py        Anbieterdateien → geprüftes Datenmodell
├── providers/*.yaml   eine Datei je Anbieter
├── allowlist.py       fest eincompilierte Host-Allowlist
├── router.py          welcher Kanal eine Anfrage bedient (reine Logik)
├── ledger.py          Kontingent, Sperren, Monatsausgaben, gespeichert
├── client.py          führt eine Anfrage über Kanäle mit Reserve aus
├── adapters/          API-Dialekte: openai_compatible, google, anthropic
├── capabilities.py    Messung eines Modells mit dem Schlüssel des Nutzers
├── hintergrund.py     Hintergrundmessung und Benachrichtigungen
├── ai_task.py         drei KI-Aufgaben-Entities, eine je Profil
├── conversation.py    Gesprächsagent für Assist
├── task_adapter.py    KI-Aufgabe ↔ Client: Struktur, Anhänge, JSON
├── sensor.py          Verbrauchssensoren
├── issues.py          Reparaturhinweise
├── services.py        Dienst free_ai_router.remeasure
├── diagnostics.py     Diagnose-Download, Schlüssel geschwärzt
├── imaging.py         Bildvorbereitung
└── sprache.py         im Code zusammengesetzte Texte, Deutsch und Englisch
```

**Kanal.** Ein Paar aus Anbieter und Modell mit seinen gemessenen
Fähigkeiten. Der Router filtert die Kanäle nach Profil und den Anforderungen
der Anfrage (Bilder, Werkzeuge, strukturierte Ausgabe, Kontextgröße) und ordnet
die übrigen: verfügbare vor wartenden, im Profil Schnell nach Antwortzeit,
sonst nach `preference` und verbleibendem Kontingent.

**Profile.** `schnell`, `vision` (Bildanalyse) und `reasoning` sind interne
Bezeichner des Registry-Formats. Die daraus gebildeten Entities haben
englische IDs (`ai_task.free_ai_router_fast`, `…_image_analysis`,
`…_reasoning`), die im Konstruktor fest gesetzt werden und damit nicht von der
Systemsprache abhängen.

**Konfiguration.** Ein Config Entry, ein Untereintrag je Anbieter (Typ
`anbieter`) mit Schlüssel und gemessenen Abweichungen. Aktuelle Fassung 2.2:

| Migration | Änderung |
|---|---|
| 1 → 2 | Anbieter aus `data["providers"]` in Untereinträge verschoben |
| 2.1 → 2.2 | Entfernt gespeicherte `rpm`/`rpd`-Messwerte, die aus Reset-Zeiten abgeleitet waren (siehe Befunde) |

**Update-Listener.** Gespeicherte Messwerte ändern einen Untereintrag. Sind
Anbieter und Schlüssel unverändert, baut der Listener die Kanäle in der
laufenden Instanz neu, statt den Eintrag neu zu laden — sonst bräche jeder
gespeicherte Anbieter die laufende Messung der anderen ab. Ein
Dispatcher-Signal lässt die Router-Entities ihre Attribute neu schreiben.

## Entwicklungsumgebung

```bash
python -m venv .venv
source .venv/bin/activate      # Linux, macOS
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
cp .env.example .env           # Schlüssel als FAR_KEY_<ANBIETER_ID>
```

Alle folgenden Befehle setzen die aktivierte Umgebung voraus.

### Tests und Linter

```bash
python -m pytest
python -m ruff check custom_components tools tests
```

Die Tests laufen ohne Netz und ohne Home Assistant. Router, Ledger,
Fähigkeitsmessung, Registry und Adapter sind reine Logik oder laufen gegen
einen lokalen Testserver, einschließlich ausfallendem Erstanbieter und
erreichten Limits. Module, die Home Assistant importieren, werden strukturell
geprüft: Jeder Schritt, Fehler und Abbruchgrund hat einen Text in beiden
Sprachen, Platzhalter stimmen überein, englische Texte enthalten kein Deutsch,
Bezeichner sind englisch, jeder ungeschützte Fremdimport steht in
`manifest.json`, beide Blueprint-Fassungen sind gleichwertig.

### Probe-CLI

Misst Anbieter ohne Home Assistant, mit demselben Code wie die Integration:

```bash
python tools/probe_cli.py --discover              # alle Anbieter, Modell-Listen abgleichen
python tools/probe_cli.py --provider groq         # ein Anbieter
python tools/probe_cli.py --cheap                 # nur Erreichbarkeit und Header
python tools/probe_cli.py --models                # nur Modell-Liste, kein Kontingent
python tools/probe_cli.py --json befund.json      # Rohbefunde sichern
```

Ausgabe je Modell: erreichbar, Bildeingabe, strukturierte Ausgabe,
Werkzeugaufrufe, Zeit bis zum ersten Token, Gesamtdauer, rohe
Rate-Limit-Header, Profilabdeckung.

**Bildtest.** Das Testbild besteht aus zwei zufällig gewählten Farbflächen aus
einer Palette von sieben. Bestanden ist die Prüfung nur, wenn das Modell
beide Farben nennt, über zwei Runden. Manche OpenAI-kompatiblen Endpunkte
verwerfen den Bildteil stillschweigend und antworten trotzdem; eine einzelne
rote Fläche war als Test zu schwach, weil Rot genau das ist, was ein blindes
Textmodell rät. Bei zwei aus sieben Farben liegt die Ratequote unter fünf
Prozent. Die Palette meidet Zwischentöne: Türkis wurde entfernt, nachdem ein
Modell es verlässlich „hellblau“ nannte und trotz gesehenen Bildes
durchfiel. Der Test beweist, dass ein Bild ankommt und ausgewertet wird, nicht
dass ein Modell eine Kameraszene versteht.

### Registry-Prüfung

```bash
python tools/validate_registry.py
```

Prüft jede Anbieterdatei gegen `registry_schema.json`, die Regeln des Loaders
und die Host-Allowlist.

## Messung

### Hintergrundmessung

`hintergrund.py` läuft 10 Sekunden nach dem Laden des Eintrags und danach alle
6 Stunden. Gemessen wird nur, was fällig ist (`faellige_modelle`):

- nie gemessen,
- bisher nur vorübergehend nicht erreichbar,
- nicht nutzbar ohne vermerkten Grund (Einträge von vor 0.3.0),
- seit mehr als 7 Tagen nicht nutzbar.

Funktionierende Modelle werden nicht periodisch nachgemessen; ob sie
antworten, zeigt der laufende Betrieb, ohne Kontingent zu verbrauchen. Eine
dauerhafte Benachrichtigung je Anbieter meldet das Ergebnis, wenn sich etwas
geändert hat oder bei der ersten Messung.

### Vorübergehend oder endgültig

`ist_voruebergehend` entscheidet, ob ein Fehlschlag etwas über das Modell
aussagt:

| Vorübergehend — Modell bleibt offen | Endgültig — Modell als nicht nutzbar vermerkt, mit Grund |
|---|---|
| Netzfehler, Zeitüberschreitung | Ratenlimit mit Kontingent 0 (`limit_requests == 0`) |
| HTTP 5xx | HTTP 400, 402, 404 |
| HTTP 408, 409, 425, 429 mit Kontingent | HTTP 403 bei gültigem Schlüssel |
| HTTP 401; HTTP 403 bei ungültigem Schlüssel | |
| Status unter 400 mit eingepacktem Upstream-Fehler | |

`uebernehmen` führt Ergebnisse zusammen: Vorübergehende Fehlschläge
überschreiben nie frühere Messungen. Ein Lauf nur auf Erreichbarkeit löscht
keine Fähigkeiten, denn „nicht gemessen“ heißt nicht „kann es nicht“.

### Limits

Ein Anfragelimit wird aus den Antwortheadern nur übernommen, wenn der Anbieter
das Fenster ausdrücklich nennt (`RateLimitInfo.requests_window_s`, etwa
Mistrals `x-ratelimit-limit-req-minute`). Alles andere kommt aus der
Anbieterdatei.

## Modell-Wächter

`tools/waechter.py` läuft täglich um 04:17 UTC über
`.github/workflows/modell-waechter.yml` (auch von Hand startbar). Je Anbieter:

1. Modell-Liste abrufen (`GET <base_url>/models`, kein Kontingent),
2. aussortieren, was der Router nicht nutzen kann (`REGELN`: Audio, Sprache,
   Bilderzeugung, Embeddings, Aliase, veraltete Modelle; bei OpenRouter alles
   ohne `:free`),
3. mit der Anbieterdatei vergleichen,
4. jedes neue Modell einmal anmessen — höchstens drei je Anbieter und Lauf,
   das neueste zuerst — mit demselben Code wie die Integration,
5. ein Issue mit dem Label `modell-waechter` anlegen oder aktualisieren.

Messergebnisse stehen in einer unsichtbaren Marke im Issue, damit sie am
nächsten Tag nicht erneut Kontingent kosten; vorübergehende Fehlschläge werden
nicht gemerkt. Ein geschlossenes Issue bleibt zu, bis sich die Liste der
Kandidaten ändert; ohne Kandidaten wird ein offenes Issue geschlossen.
Aufgenommen wird nichts automatisch — Profil und Tageslimit sind redaktionelle
Entscheidungen, und Änderungen erreichen die Installationen als Release über
HACS.

```bash
python tools/waechter.py --dry-run                # alle Anbieter, nichts melden
python tools/waechter.py --dry-run --nur groq     # ein Anbieter
```

In der Action kommen die Schlüssel aus den Repository-Secrets
`FAR_KEY_GOOGLE_AI_STUDIO`, `FAR_KEY_GROQ`, `FAR_KEY_MISTRAL` und
`FAR_KEY_OPENROUTER`. Exitcode 4 bedeutet, dass mindestens ein Anbieter seinen
Schlüssel abgelehnt oder keine Liste geliefert hat; die übrigen werden
trotzdem abgearbeitet.

## Mehrsprachigkeit

- `strings.json` ist die englische Quelle, `translations/en.json` identisch,
  `translations/de.json` deutsch. Abgedeckt sind Einrichtungsdialog,
  Entity-Namen, Attributbezeichnungen, Reparaturhinweise, der Dienst und
  Fehlermeldungen.
- `sprache.py` enthält alles, was im Code zusammengesetzt wird:
  Anbieterkarten, Abdeckung, Messberichte, Sperrgründe, Benachrichtigungen,
  Systemprompts, Einheiten. Die Sprache folgt `hass.config.language`
  (`de*` → Deutsch, sonst Englisch); Assist antwortet in der Sprache der
  Anfrage.
- **Einheiten** werden beim Anlegen der Entity nach Systemsprache gesetzt.
  Home Assistant übersetzt `unit_of_measurement` absichtlich nur ins
  Englische, damit Statistiken bei einem Sprachwechsel stabil bleiben.
- **Bezeichner** — Entity-IDs, Attributschlüssel, Dienst und Felder — sind
  englisch und werden nie übersetzt.
- Anbieterdateien führen `steps_en`/`steps_de`, `data_note_en`/`data_note_de`
  und optional `summary_en`/`summary_de`; unvollständige Übersetzungen lehnt
  der Loader ab.

## Test in Home Assistant

`custom_components/free_ai_router/` in das Verzeichnis `custom_components/`
einer Testinstanz kopieren, neu starten, Integration hinzufügen. Sinnvolle
Prüfungen:

- Einrichtungsdialog: Schlüsseltest mit gültigem und ungültigem Schlüssel,
  Abschlussbildschirm mit voraussichtlicher Abdeckung.
- `ai_task.generate_data` mit Text, mit Kamerabild und mit `structure`.
- Assist mit Gerätesteuerung.
- Hintergrundmessung: Benachrichtigung je Anbieter, die Attribute `channels`
  und `disabled` aktualisieren sich ohne Neuladen des Eintrags.
- Diagnose-Download: Schlüssel geschwärzt.

Wer Entity-Aktualisierungen über die REST-API prüft: `/api/states` liefert
eine zwischengespeicherte Darstellung; `last_reported` ist nur über die
Template-API verlässlich.

## Befunde

Gemessenes Verhalten, das den Entwurf geprägt hat. Daten beziehen sich auf
2026.

**Bilder kosten bei jeder Auflösung gleich viel** (11.09.). Dieselbe Szene an
`gemini-3.5-flash-lite` mit 256 bis 4096 Pixeln kostete immer 1.110
Prompt-Token; Groqs `qwen3.8-27b` verhielt sich genauso (792). Folgen in
`imaging.py`: Verkleinert wird nur über 1,5 MB (auf 2048 Pixel, um Uploadzeit
zu sparen), und der Ledger bucht je Bild 1.100 Token vor. Bei Groqs 8.000
Token pro Minute ist das der Unterschied zwischen angenommenen 30 und
tatsächlichen sieben Analysen pro Minute.

**Google meldet einen ungültigen Schlüssel mit HTTP 400** und
`API_KEY_INVALID`, nicht mit 401. Ohne Behandlung wurde ein toter
Google-Kanal bei jeder Anfrage neu versucht.

**Mistrals Limit 0 heißt: kein API-Abonnement.**
`x-ratelimit-limit-req-minute: 0` ist kein Limit: Mistral führt getrennte
Abonnements für API, Le Chat und Code. Erst das API-Abonnement mit Plan FREE
schaltet die Modelle frei; der Schlüsseltest meldet das als eigenen Fall.

**Mistrals kostenlose Stufe ist ein Ausgabendeckel** von 10 US-Dollar im
Monat statt eines Zeitfensters. Der Ledger führt die Monatsausgaben je
Anbieter aus gemessenen Token und `pricing`, Ein- und Ausgabe getrennt, bucht
vor dem Absenden vor und berichtigt nach der Antwort.

**Abgeleitete Anfragelimits waren falsch** (24.09.). Das Fenster eines
Anfragelimits wurde aus seiner Reset-Zeit abgeleitet. Groq füllt sein
Tageslimit laufend auf, der Reset liegt daher oft unter zwei Minuten, und aus
1.000 je Tag wurden 1.000 je Minute. Limits werden jetzt nur mit ausdrücklich
genanntem Fenster übernommen; Migration 2.2 hat die gespeicherten Werte
entfernt.

**Eingepackte Upstream-Fehler.** OpenRouter liefert Fehler des
dahinterliegenden Anbieters (etwa `ResourceExhausted`) mit HTTP 200. Als
Antwort gewertet, vermerkte die Bildprüfung „kann keine Bilder“. Solche
Antworten gelten jetzt als nicht messbar.

**Fähigkeiten weichen in beide Richtungen von der Dokumentation ab.** Groqs
Qwen nimmt Bilder an; Ministral 3B und 8B verarbeiten Bilder, was sich erst
mit der berichtigten Testpalette zeigte; mehrere von Google gelistete Modelle
antworten mit 404. Deshalb misst jede Installation mit dem eigenen Schlüssel,
und die Messung hat Vorrang.

**Home-Assistant-Frontend.**

- Der Titel eines Menü-Schritts bekommt keine Platzhalter (`renderMenuHeader`
  reicht `description_placeholders` nicht weiter); `{name}` in einem solchen
  Titel erscheint als `MISSING_VALUE`.
- Entities ohne Gerät bekommen kein Namenspräfix. Die Router-Entities haben
  kein Gerät — ein gemeinsames Gerät ließ die Integrationsseite „Geräte, die
  nicht zu einem Untereintrag gehören“ anzeigen —, deshalb beginnen ihre
  übersetzten Namen mit „Free AI Router“.
- Das Geräteregister entfernt kein Gerät, auf das keine Entity mehr verweist,
  solange sein Config Entry besteht.
- `via_device` ist seit 2026.8 veraltet und wird nicht mehr verwendet.

**Die Einrichtung muss sofort speichern.** Frühe Fassungen maßen alle Modelle
im Einrichtungsdialog und legten den Eintrag erst nach einem
Übersichtsschritt an. Im ersten echten Einsteigertest wurden vier Anbieter
eingegeben und der Dialog vor dem letzten Knopf geschlossen — nichts war
gespeichert. Der Eintrag entsteht jetzt, sobald der erste Schlüssel den Test
besteht; gemessen wird im Hintergrund.

## Releases

1. `version` in `manifest.json` erhöhen.
2. Eintrag in `CHANGELOG.md` und `CHANGELOG.de.md` ergänzen.
3. Tests, Linter und Registry-Prüfung ausführen.
4. Auf `main` pushen. HACS installiert vom Standardzweig; die CI prüft das
   Repository für HACS.

## Offene Punkte

- **Wächter-Schlüssel:** Die Repository-Secrets für Groq und Mistral wurden am
  24.09. abgelehnt und müssen ersetzt werden.
- **Ehemaliger Feed-Dienst:** Der Zweig `feed`, GitHub Pages und das Secret
  `FAR_FEED_PRIVATE_KEY` werden nicht mehr gebraucht und können entfernt
  werden.
- **Reasoning-Kapazität** ist knapp: zwei Gemini-Flash-Modelle mit je 20
  Anfragen am Tag, Groq begrenzt durch 8.000 Token pro Minute, Codestral.
  Genug für gelegentliche Aufgaben; ein weiterer Anbieter bräuchte eine
  Änderung der Allowlist.
- **Anthropic-Adapter** ist umgesetzt, aber von keinem Anbieter genutzt und
  daher nicht gegen einen echten Endpunkt getestet.
- **Brands:** noch kein Eintrag in `home-assistant/brands`; nur für den
  HACS-Standardkatalog nötig.

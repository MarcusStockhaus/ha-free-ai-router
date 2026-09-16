# Free AI Router

KI-Funktionen für Home Assistant, verteilt über mehrere kostenlose Anbieter.
Für Installationen ohne eigene GPU — Green, Yellow, Raspberry Pi, kleiner NUC.

> **Läuft bei mir.** Keine Supportzusage, Issues können unbeantwortet bleiben.
> Wer das liest, ist nicht enttäuscht, wenn es eintritt.

---

## Worum es geht

Das Einrichten ist der Punkt. Ein Assistent führt durch zwei, drei Anmeldungen,
prüft jeden Schlüssel sofort mit einem echten Aufruf und **misst selbst**, was
jedes Modell kann — Bilder, Schemata, Werkzeuge, Antwortzeit. Danach verteilt
ein Router die Anfragen still auf den jeweils passenden Kanal und weicht auf
die Reserve aus, wenn einer ausfällt.

Heraus kommen vier Entities:

| Entity | wofür |
|---|---|
| `ai_task.free_ai_router_schnell` | kurze Aufgaben, Zusammenfassen, Klassifizieren |
| `ai_task.free_ai_router_bildanalyse` | Kamerabilder |
| `ai_task.free_ai_router_reasoning` | großer Kontext, Automationsbau |
| `conversation.free_ai_router_assist` | Assist, mit Gerätesteuerung |

Dazu sechs Verbrauchssensoren und zwei Blueprints.

Jede Automation und jeder fremde Blueprint, der eine AI-Task-Entity auswählen
lässt, funktioniert damit. Die Integration baut nichts nach, was es schon gibt —
sie stellt sich hinter die Andockstelle, die Home Assistant selbst mitbringt.

## Was es nicht kann

- **Konten für dich anlegen.** Captcha und Nutzungsbedingungen setzen die Grenze.
- **Verhindern, dass ein Anbieter morgen abschaltet.** Drei von sechs geprüften
  Anbietern bewerben eine kostenlose Stufe, die über die API gar nicht
  erreichbar ist — siehe unten.
- **Ohne Internet arbeiten.** Fällt die Leitung aus, ist jede KI-Funktion weg.
  Für eine Türklingel-Benachrichtigung verschmerzbar, für alles Alarm-nahe
  disqualifizierend.
- **Deine Daten im Haus halten.** Sie gehen an den gewählten Anbieter. Die
  Anbieterkarte sagt vor der Eingabe des Schlüssels, was der damit macht.

---

## Installation

**Über HACS**, als benutzerdefiniertes Repository — nicht im Standardkatalog,
das ist bewusst so: Aufnahme dort verspricht eine Pflege, die dieses Projekt
mit seiner „Läuft bei mir"-Zeile in der Kopfzeile nicht geben will. Wer die
URL selbst einträgt, weiß, worauf er sich einlässt.

1. HACS → oben rechts die drei Punkte → **Benutzerdefinierte Repositories**
2. URL `https://github.com/MarcusStockhaus/ha-free-ai-router`, Kategorie
   **Integration**
3. Free AI Router suchen und herunterladen, Home Assistant neu starten

Die beiden Blueprints liegen nicht in diesem HACS-Eintrag — sie kommen im
Abschnitt [Blueprints](#blueprints) über einen Ein-Klick-Import.

### Ohne HACS

Auf einem System mit Shell-Zugang (SSH-Add-on oder Terminal):

```bash
cd /tmp && git clone https://github.com/MarcusStockhaus/ha-free-ai-router
cp -r ha-free-ai-router/custom_components/free_ai_router /config/custom_components/
cp -r ha-free-ai-router/blueprints/automation/free_ai_router /config/blueprints/automation/
```

Ohne Shell: das Repo als ZIP herunterladen und die beiden Ordner über das
File-Editor- oder Samba-Add-on an dieselben Stellen legen. Danach Home
Assistant neu starten.

### Einrichten

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Free AI
Router**, oder direkt per Klick:
[Einrichtungsassistenten öffnen](https://my.home-assistant.io/redirect/config_flow_start/?domain=free_ai_router).

Der Assistent zeigt je Anbieter eine Karte: was er kann, was er mit den Daten
macht, ob Zahlungsdaten verlangt werden, ob er für den Anfang empfohlen ist —
und einen Deep-Link direkt zur Key-Seite, nicht zur Startseite. Nach der
Eingabe läuft sofort ein echter Testaufruf, danach die Fähigkeitsmessung, mit
Fortschrittsbalken. Die dauert ein bis zwei Minuten, weil sie je Modell
mehrere echte Aufrufe macht. Am Ende steht, welches Profil von welchem Kanal
bedient wird, wo eine Lücke bleibt — und drei Links zu den nächsten Schritten
(Assist verbinden, beide Blueprints importieren).

Für den Anfang reichen **Google AI Studio und Groq**: zusammen tragen sie alle
drei Profile mit Reserve, ohne Zahlungsdaten und ohne Ausgabendeckel. Mistral
und OpenRouter sind zusätzliche Reserve, kein Ersatz für die beiden.

### Zugänge verwalten

Jeder eingerichtete Anbieter ist eine eigene Zeile auf der Integrationsseite,
mit eigenem Gerät und eigenem Verbrauchssensor:

```
Free AI Router
├─ Google AI Studio      1 Entität
├─ Groq                  1 Entität
└─ Mistral               1 Entität
   Anbieter hinzufügen
```

**Anbieter hinzufügen**, **Schlüssel ersetzen** und **Entfernen** sind die
Knöpfe, die Home Assistant dort selbst anbietet — die Integration baut keine
eigene Verwaltungsoberfläche daneben.

Ein Schlüsselwechsel läuft durch denselben Weg wie das Einrichten: Test,
Messung, Ergebnis. Ein anderer Schlüssel kann ein anderes Konto sein, und was
das Konto darf, ist damit offen. Der alte Schlüssel wird nirgends angezeigt;
zum Ersetzen braucht es ohnehin einen neuen.

Die vier Router-Entities (die drei `ai_task`-Profile, Assist) und die vier
Gesamtzähler stehen bewusst **nicht** auf dieser Seite — sie tragen kein
eigenes Gerät. Genauso macht es Home Assistants eigene Google-Generative-AI-
und OpenAI-Conversation-Integration: der Config Entry ist der Zugang, jedes
sichtbare Gerät gehört zu einem Untereintrag. Zu finden sind sie unter
**Einstellungen → Geräte & Dienste → Entitäten** (nach `free_ai_router`
filtern) oder direkt in den AI-Task- und Assist-Einstellungen. Ihr
Anzeigename trägt deshalb den Namen der Integration im Text selbst
(„Free AI Router Schnell" statt nur „Schnell") — ohne Gerät gäbe es sonst
keinen Hinweis, welche Integration eine Entity wie `ai_task.free_ai_router_schnell`
überhaupt anbietet, wenn sie neben denen anderer Integrationen in einer
Auswahlliste steht.

---

## Anschließen

Die eingerichteten Entities tun erst dann etwas, wenn sie irgendwo
angeschlossen sind. Die Übersicht am Ende des Einrichtungsassistenten
verlinkt dieselben drei Schritte direkt.

**Assist als Sprachassistent.** [Einstellungen → Sprachassistenten
öffnen](https://my.home-assistant.io/redirect/voice_assistants/), Free AI
Router als Gesprächsagenten wählen. Auf derselben Seite steht auch die
bevorzugte KI-Aufgaben-Entity — die, die eine Automation ohne eigene Angabe
einer `entity_id` benutzt.

**Kameraanalyse.** [Blueprint importieren](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fkamera_analyse.yaml),
Kamera und Bewegungsmelder wählen, Sperrzeit setzen.

**Türklingel.** [Blueprint importieren](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Ftuerklingel.yaml),
Klingelsensor und Kamera wählen.

Ohne eigene Blueprint- oder Automationsidee reicht auch ein einzelner
Diensteaufruf, etwa in den Entwicklerwerkzeugen zum Ausprobieren:

```yaml
action: ai_task.generate_data
data:
  entity_id: ai_task.free_ai_router_schnell
  instructions: Fasse in einem Satz zusammen, wie das Wetter heute wird.
```

---

## Anbieterlage, gemessen am 11.09.2026

Alle Zahlen aus echten Konten und Antwortheadern, nicht aus Dokumentation.
Momentaufnahmen — das mitgelieferte Probe-CLI hält sie aktuell.

| Kanal | Profile | RPM | RPD | TPM |
|---|---|---:|---:|---:|
| `gemini-3.5-flash-lite` | schnell, vision | 15 | 500 | 250k |
| `gemini-3.1-flash-lite` | schnell, vision | 15 | 500 | 250k |
| `gemma-4-31b-it` | vision, schnell — kein Schema | 30 | 14.400 | 16k |
| `gemma-4-26b-a4b-it` | vision, schnell — kein Schema | 30 | 14.400 | 16k |
| `gemini-3.5-flash` | reasoning, vision | 5 | **20** | 250k |
| `gemini-3.8-flash` | reasoning | 5 | **20** | 250k |
| `groq/openai/gpt-oss-20b` | schnell | 30 | 1.000 | 8k |
| `groq/qwen/qwen3.8-27b` | schnell, reasoning, **vision** | 30 | 1.000 | 8k |
| `groq/openai/gpt-oss-120b` | reasoning | 30 | 1.000 | 8k |
| `mistral/ministral-3b-2512` | schnell | 750 | — | 1.300k |
| `mistral/ministral-8b-2512` | schnell | 188 | — | 625k |
| `mistral/codestral-2508` | reasoning | 125 | — | 625k |
| `openrouter/nemotron-3-nano-omni…:free` | vision | 20 | 50 | — |
| `openrouter/nemotron-3.5-lightning:free` | schnell | 20 | 50 | — |

### Vier Befunde, die die übliche Planung umwerfen

**Die großen Flash-Modelle haben 20 Anfragen pro Tag.** Nicht 250, nicht 1.500.
Wer „Gemini Flash" als Kamera-Arbeitspferd einplant, bekommt zwanzig Analysen
und danach 429. Tragend sind allein die Flash-Lite-Modelle mit je 500/Tag.

**Ein Bild kostet rund 1.100 Token — unabhängig von der Auflösung.** Dasselbe
Motiv kostet bei 256×144 genauso viel wie bei 4096×2304: 30-fache Pixelzahl,
identischer Preis. Bilder vor dem Versand zu verkleinern spart deshalb
**nichts** und kostet nur Bildqualität. Verkleinert wird hier erst ab 1,5 MB,
und dann allein wegen der Uploadzeit.

**Groqs Qwen3.8 kann Bilder**, entgegen der verbreiteten Annahme. Damit hängt
die Bildanalyse nicht an einem einzigen Anbieter.

**Googles Gemma kann kein Schema.** Es nimmt Bilder an und beschreibt sie
richtig, liefert aber kein `responseSchema`. Für die übliche Kameraanalyse
(„Person? Paket?" als Felder) fällt es damit aus; der Router filtert es
korrekt heraus.

Realistisch für Kameraanalyse **mit Schema**: rund 2.000 am Tag über Google und
Groq, dazu Mistral als gedeckelter Puffer — 10 $ API-Nutzung im Monat, das
reicht für etwa 2.500 Analysen am Tag. Gegen das übliche Muster, drei bis vier
Außenkameras mit Bewegungsauslöser und 50 bis 300 Analysen am Tag, ist das
reichlich Luft.

### Anbieter, die es nicht in die Auswahl geschafft haben

Alle aus demselben Grund: eine beworbene kostenlose Stufe, die über die API
nicht erreichbar ist. Kein Randfall, sondern der Normalfall — deshalb misst
diese Integration, statt Dokumentation abzuschreiben.

- **OpenCode Zen** — `MissingSessionID`: *„OpenCode's free tier can only be
  used in OpenCode"*. Verlangt vorher Zahlungsdaten.
- **Cerebras** — `402 Payment required`. Die Limits-Seite des Kontos weist
  großzügige Kontingente aus, die API gibt sie ohne bezahlten Tarif nicht
  heraus.
- **Mistral**, teilweise — `mistral-small` und `magistral` melden
  `x-ratelimit-limit-req-minute: 0`. Mistral führt drei getrennte
  Produktlinien; der kostenlose *Le-Chat*-Tarif schaltet keine API-Modelle
  frei, dafür braucht es das eigene API-Abonnement.

---

## Blueprints

| Blueprint | wofür |
|---|---|
| `kamera_analyse.yaml` | Bewegungsmelder → Analyse. Sperrzeit als Pflichtfeld. |
| `tuerklingel.yaml` | Klingeln → wer vor der Tür steht. Erkennt `event`- und `binary_sensor`-Klingeln. |

Da Verkleinern nichts spart, ist die **Anzahl der Auslösungen** die einzige
Stellschraube, die es gibt. Deshalb sind Sperrzeit, genauer Auslöser und
Zusatzbedingung Eingabefelder und keine Fußnoten. Ein Bewegungsmelder an der
Straße kommt leicht auf 2.000 Auslösungen am Tag; ohne Sperrzeit ist das
Tagesbudget vor dem Mittagessen weg.

Beide Blueprints haben außerdem **„Fehler melden"** (Vorgabe: an). Liefert
`ai_task.generate_data` keine Antwort — meist eine Kamera ohne Standbild oder
alle Kanäle am Limit — läuft die Automation sonst lautlos ins Leere: sie tut
nichts, ohne dass irgendwo eine Meldung erscheint. Mit dem Feld kommt
stattdessen eine Benachrichtigung, die sagt, woran es lag.

## Sensoren

```
sensor.free_ai_router_anfragen_heute
sensor.free_ai_router_token_heute
sensor.free_ai_router_reserve_gegriffen_heute      ← der wichtigste
sensor.free_ai_router_verworfen_heute
sensor.free_ai_router_<anbieter>_anfragen_heute    ← Restkontingent in den Attributen
```

Bei einem Anbieter mit Ausgabendeckel stehen in denselben Attributen
`budget_usd`, `ausgegeben_usd` und `rest_usd`. Ist der Betrag aufgebraucht,
meldet sich das zusätzlich unter **Einstellungen → Reparaturen** — nicht als
Störung, sondern weil es die Erwartung ändert: der Puffer ist bis zum
Monatsersten weg.

Als Entities-Karte für ein Dashboard, ohne YAML-Vorwissen über
**Einstellungen → Dashboards → Karte hinzufügen → Entitäten** nachzubauen:

```yaml
type: entities
title: Free AI Router
entities:
  - sensor.free_ai_router_anfragen_heute
  - sensor.free_ai_router_token_heute
  - sensor.free_ai_router_reserve_gegriffen_heute
  - sensor.free_ai_router_verworfen_heute
```

*Reserve gegriffen heute* ist der Sensor, auf den es ankommt. Ein Erstkanal,
der still dauerhaft ausfällt, fällt sonst erst auf, wenn auch die Reserve weg
ist. Dieselbe Lage meldet sich zusätzlich von selbst unter **Einstellungen →
Reparaturen**, samt Link zur Key-Seite des betroffenen Anbieters.

## Nachmessen

```yaml
action: free_ai_router.neu_vermessen
data:
  nur_lebendigkeit: false     # optional
  anbieter: [mistral]         # optional, sonst alle
```

Die Fähigkeiten, mit denen der Router arbeitet, stammen aus dem Augenblick des
Einrichtens. Sie schlagen bewusst alles andere — `alive` und Limits hängen am
Konto und nicht am Modell, und was der eigene Schlüssel kann, weiß niemand
besser als die eigene Messung. Ohne diesen Dienst bliebe ein einmal gemessener
Wert allerdings für immer stehen.

Der Aufruf liefert eine Antwort, die sagt, was sich geändert hat:

```yaml
anbieter:
  mistral:
    gemessen: 4
    lebendig: 3
    aenderungen:
      - "ministral-3b-2512: vision nein -> ja"
dauer_s: 47.2
```

Zwei Vorsichtsmaßnahmen, dieselben wie im Feed-Dienst: antwortet **kein
einziges** Modell eines Anbieters, der vorher welche hatte, wird das Ergebnis
verworfen — das ist fast immer die eigene Leitung, und eine kaputte Leitung
darf nicht dazu führen, dass sich die Installation selbst die Kanäle
abschaltet. Und `nur_lebendigkeit` lässt die bisher gemessenen Fähigkeiten
unangetastet, statt sie zu löschen, weil nicht danach gefragt wurde.

---

## Was bei Limit oder Ausfall passiert

1. Der Router sortiert die Kanäle: wer sofort kann vor dem, der warten müsste;
   bei `schnell` nach Latenz, sonst nach der redaktionellen Reihenfolge,
   zuletzt nach freiem Restkontingent.
2. Ist nur das **Minutenfenster** zu, reiht sich die Anfrage bis zu 20 Sekunden
   ein. Fünf gleichzeitig auslösende Bewegungsmelder reißen 15 RPM lange vor
   500 RPD.
3. Gegen ein **Tagesfenster** hilft kein Warten — der Router wechselt sofort.
   Dasselbe gilt für den **Ausgabendeckel**: Mistrals kostenlose Stufe ist auf
   10 $ API-Nutzung im Monat begrenzt, und das ist kein Zeitfenster, sondern
   ein Geldbetrag. Der Ledger rechnet ihn aus den gemessenen Token und der
   Preisliste hoch, bucht schon beim Absenden vor und sperrt den Anbieter,
   wenn der Betrag erreicht ist — bis zum Monatsersten, in der Zeitzone des
   Anbieters. Der Deckel hängt am Konto: alle Modelle eines Anbieters teilen
   ihn sich.

   Ein- und Ausgabe werden getrennt gerechnet, weil sie getrennt bepreist
   sind. Bei `mistral-small` kostet die Ausgabe das Vierfache der Eingabe;
   mit einem Mischpreis wäre der Deckel bei langen Antworten zu spät
   erreicht. Der so gerechnete Betrag ist eine Schätzung — maßgeblich bleibt
   die Abrechnung des Anbieters.
4. Jeder Wechsel steht im Log und im Attribut `zuletzt_genutzter_kanal`.
5. Erst wenn **kein** Kanal übrig ist, endet der Aufruf mit einem
   `HomeAssistantError`, dessen Text jeden Versuch einzeln nennt. Eine
   Automation fängt ihn mit `continue_on_error: true` ab.

Ein abgelehnter Schlüssel schaltet den Kanal sofort ab, statt ihn bei jeder
Anfrage neu anzuklopfen — und gibt ihn wieder frei, sobald ein Aufruf
durchgeht. Die Zählerstände überleben Neustarts.

Der Tageszähler läuft in der Zeitzone des Anbieters, nicht in der lokalen:
Googles kostenlose Stufe setzt um Mitternacht Pacific zurück.

---

## Fehlersuche

**Diagnose herunterladen** steht auf der Integrationsseite unter dem
Drei-Punkte-Menü. Die Datei enthält Laufzeitzustand, Ledger-Zählerstände und
Feed-Status — ohne API-Schlüssel, die werden vor dem Export geschwärzt. Das
ist der erste Anhang für ein Issue, nicht zehn Rückfragen.

Ein abgelehnter Schlüssel beim Einrichten unterscheidet jetzt vier Fälle
statt eines Satzes „nicht angenommen": abgelehnt, kein Kontingent
freigeschaltet (bei Mistral zum Beispiel ein fehlendes API-Abonnement),
Limit erreicht, oder der Anbieter antwortet gerade nicht. Jeder Fall sagt,
was als Nächstes zu tun ist.

---

## Sicherheit

Die Registry-Dateien bestimmen, wohin Daten fließen. Deshalb steht zusätzlich
eine **fest eincompilierte Host-Allowlist** im Python-Code: eine Datendatei
kann niemals einen Endpunkt einführen, der dort nicht steht.

Das ist die zweite Linie hinter der Signatur des **Feed-Dienstes**, der
Modelle und Limits aktuell hält. Er darf Texte und Zahlen ändern, niemals aber
das Ziel. Der Feed ist in dieser Fassung **aus** — `FEED_URL` und
`FEED_PUBLIC_KEY_B64` stehen leer, die Integration holt also nichts und fragt
nichts. Wie er funktioniert und wie man ihn einschaltet, steht in
[FEED.md](FEED.md).

---

## Grundsatz

**Keine Affiliate-Links, keine bezahlten Empfehlungen, keine Telemetrie.**
Nutzdaten gehen ausschließlich an den jeweils gewählten Anbieter.

Das steht hier und nicht im Kleingedruckten, weil die Rechnung eindeutig ist:
selbst im besten Fall stünden einstellige Jahresbeträge gegen den Verdacht,
dass jede Empfehlung gekauft ist.

## Mitmachen

**Ein neuer Anbieter ist eine YAML-Datei, kein Python.** Wie das geht, steht in
[CONTRIBUTING.md](CONTRIBUTING.md) — mit einem echten Beispiel-Diff.

Entwicklung, Probe-CLI und Tests: [ENTWICKLUNG.md](ENTWICKLUNG.md).

## Lizenz

[MIT](LICENSE).

# Free AI Router — Phase 1

Home-Assistant-Integration, die KI-Anfragen über mehrere kostenlose Anbieter
verteilt. Für HA-Installationen ohne GPU.

**Das hier ist die Arbeitsfassung für den Eigengebrauch.** Die veröffentlichungsreife
README mit Erwartungsrahmen, Blueprints und `CONTRIBUTING.md` ist Phase 2.

Stand 11.09.2026: läuft in Home Assistant 2026.8.3. Alle sieben
Akzeptanzkriterien aus dem Implementierungsauftrag sind erfüllt — Config Flow,
Kameraanalyse mit Schema, Assist mit Gerätesteuerung, Ausfall des ersten
Anbieters mit Wechsel auf die Reserve. 124 Tests, ohne Netz und ohne HA.

---

## Schritt 0 — Probe-CLI, ohne Home Assistant

Zuerst messen, dann bauen. Das CLI prüft alle Anbieter durch und gibt aus, was
sie tatsächlich können — nicht, was in ihrer Doku steht.

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
```

```bash
cp .env.example .env
```

Schlüssel in `.env` eintragen, dann:

```bash
.venv/Scripts/python tools/probe_cli.py --discover
```

Ausgabe je Modell: lebt, Vision, Structured Output, Tool-Calling, Zeit bis zum
ersten Token, Gesamtdauer. Dazu die rohen Rate-Limit-Header je `api_style` und
eine Profil-Abdeckung. `--discover` hält zusätzlich die Modell-Liste des
Anbieters gegen die Registry und meldet veraltete Einträge.

Weitere Schalter:

```bash
.venv/Scripts/python tools/probe_cli.py --cheap                 # nur Liveness + Header
.venv/Scripts/python tools/probe_cli.py --provider groq         # ein Anbieter
.venv/Scripts/python tools/probe_cli.py --json befund.json      # Rohbefunde sichern
```

Das Vision-Testbild besteht aus **zwei zufällig gewählten Farbflächen** aus
einer Palette von sieben. Geprüft wird nicht, ob das Bild angenommen wird,
sondern ob das Modell **beide** Farben nennt.

Der Grund: manche OpenAI-kompatiblen Endpunkte verwerfen den Bildteil
stillschweigend und antworten trotzdem. Eine einzelne rote Fläche war als Test
zu schwach — „rot" ist genau die Farbe, die ein blindes Textmodell rät. Bei
zwei Farben aus sieben liegt die Ratequote unter fünf Prozent, und sie
wechseln bei jedem Lauf.

Was der Test *nicht* beweist: dass ein Modell eine Kameraszene versteht. Er
beweist, dass das Bild ankommt und ausgewertet wird.

---

## Neuen Anbieter hinzufügen

Eine YAML-Datei unter `custom_components/free_ai_router/providers/`. Kein Python.

```bash
.venv/Scripts/python tools/validate_registry.py
```

Prüft gegen `registry_schema.json`, den Loader und die Host-Allowlist.
Der Host muss zusätzlich in `allowlist.py` stehen — das ist Absicht: der
Feed-Dienst aus Phase 3 darf Modelle, Limits und Texte ändern, aber niemals,
wohin Daten fließen.

---

## Tests

```bash
.venv/Scripts/python -m pytest
```

Läuft ohne Netz und ohne Home Assistant. Der Router ist reine Logik; Adapter,
Ledger und Reserve werden gegen einen lokalen Testserver geprüft — inklusive
simuliertem Ausfall des ersten Anbieters und RPM-Anschlag.

```bash
.venv/Scripts/python -m ruff check custom_components tools tests
```

---

## Integration im Devcontainer

`custom_components/free_ai_router/` in die `config/custom_components/` des
HA-Devcontainers legen (oder verlinken), HA starten, Integration hinzufügen.

Der Einrichtungsassistent führt durch: Anbieterkarte → Deep-Link zur Key-Seite
→ Schlüssel eingeben → sofortiger Testaufruf → Fähigkeitsmessung mit
Fortschrittsanzeige → weiterer Anbieter oder Abschlussübersicht. Weitere
Anbieter später über *Konfigurieren* an der Integration.

Danach gibt es:

| Entity | Profil |
|---|---|
| `ai_task.free_ai_router_schnell` | kurze Aufgaben, Zusammenfassen, Klassifizieren |
| `ai_task.free_ai_router_bildanalyse` | Kamerabilder |
| `ai_task.free_ai_router_reasoning` | großer Kontext, Automationsbau |
| `conversation.free_ai_router_assist` | Assist, mit Gerätesteuerung |

Dazu sechs Sensoren: **Anfragen heute**, **Token heute**, **Reserve gegriffen
heute**, **Verworfen heute** und je Anbieter einer mit dem Restkontingent je
Modell in den Attributen.

Der wichtigste davon ist *Reserve gegriffen heute*. Ein Erstkanal, der still
dauerhaft ausfällt, fällt sonst erst auf, wenn auch die Reserve weg ist.

### Blueprint

`blueprints/automation/free_ai_router/kamera_analyse.yaml` — Kameraanalyse bei
Bewegung, mit Sperrzeit als Pflichtgedanken statt als Fußnote. Da Verkleinern
nichts spart (siehe oben), ist die Anzahl der Auslösungen die einzige
Stellschraube, die es gibt.

Ein Bildanhang schlägt das Profil: hängt an einer Anfrage an der
`schnell`-Entity ein Bild, routet der Router auf einen Vision-fähigen Kanal.
Sonst würde die Kameraanalyse daran scheitern, dass sie an der falschen Entity
ausgelöst wurde.

### Beispielaufruf

```yaml
action: ai_task.generate_data
data:
  entity_id: ai_task.free_ai_router_bildanalyse
  task_name: Türklingel
  instructions: >-
    Ist eine Person zu sehen? Trägt sie ein Paket?
  structure:
    person:
      selector:
        boolean: {}
    paket:
      selector:
        boolean: {}
  attachments:
    media_content_id: media-source://camera/camera.tuerklingel
    media_content_type: image/jpeg
response_variable: befund
```

---

## Was bei Limit oder Ausfall passiert

1. Der Router sortiert die Kanäle: wer sofort kann vor dem, der warten müsste;
   bei `schnell` nach Latenz, sonst nach der redaktionellen Reihenfolge
   (`preference` in der Anbieterdatei), zuletzt nach freiem Restkontingent.
2. Ist nur das **Minutenfenster** zu, reiht sich die Anfrage bis zu 20 Sekunden
   ein — fünf gleichzeitig auslösende Bewegungsmelder reißen 15 RPM lange vor
   500 RPD. Dasselbe gilt fürs **Tokenfenster**: bei Gemma sind 16k Token/Minute
   nach rund 13 Bildanalysen erreicht, das Tagesbudget von 14.400 dagegen kaum.
3. Gegen ein **Tagesfenster** hilft kein Warten: der Router wechselt sofort auf
   den nächsten Kanal.
4. Jeder Wechsel steht im Log (`Reserve gegriffen: …`) und im Attribut
   `zuletzt_genutzter_kanal` der Entity.
5. Erst wenn **kein** Kanal übrig ist, endet der Aufruf mit einem
   `HomeAssistantError`, dessen Text jeden Versuch einzeln nennt. Das ist der
   saubere Abbruch, keine durchgereichte Anbieter-Exception — eine Automation
   fängt ihn mit `continue_on_error: true` ab.

Der Tageszähler läuft in der Zeitzone des Anbieters, nicht in der lokalen:
Googles kostenlose Stufe setzt um Mitternacht Pacific zurück.

---

## Bilder werden nicht verkleinert — und warum

Die naheliegende Sparmassnahme funktioniert nicht. Dasselbe Motiv kostet bei
`gemini-3.5-flash-lite` **1110 Prompt-Token, ob 256x144 oder 4096x2304** —
30-fache Pixelzahl, identischer Preis. Groq verhaelt sich genauso. Die Anbieter
normalisieren das Bild vor der Abrechnung.

Verkleinert wird deshalb nur, was ueber 1,5 MB liegt (ein Handyfoto, kein
Kamerabild), und dann auf 2048 Pixel — das spart Uploadzeit, sonst nichts.
Kamerabilder behalten ihre volle Aufloesung.

Was dabei herauskam und wichtiger ist: **ein Bild kostet rund 1.100 Token.**
Nicht 258, wie die Kachelrechnung nahelegt. Der Ledger bucht den gemessenen
Wert vor dem Absenden vor — sonst rennt er bei engen Tokenfenstern in den 429,
statt vorher zu bremsen.

---

## Anbieterlage, am 09.09.2026 gemessen

Alle Zahlen aus dem eigenen Konto bzw. den Antwortheadern, nicht aus
Dokumentation. Sie sind Momentaufnahmen — das Probe-CLI hält sie aktuell.

| Kanal | Profile | RPM | RPD | TPM |
|---|---|---:|---:|---:|
| `gemini-3.5-flash-lite` | schnell, vision | 15 | 500 | 250k |
| `gemini-3.1-flash-lite` | schnell, vision | 15 | 500 | 250k |
| `gemma-4-31b-it` | vision, schnell — **kein Schema** | 30 | 14.400 | 16k |
| `gemma-4-26b-a4b-it` | vision, schnell — **kein Schema** | 30 | 14.400 | 16k |
| `gemini-3.5-flash` | reasoning, vision | 5 | **20** | 250k |
| `gemini-3.8-flash` | reasoning | 5 | **20** | 250k |
| `groq/openai/gpt-oss-20b` | schnell | 30 | 1.000 | 8k |
| `groq/qwen/qwen3.8-27b` | schnell, reasoning, **vision** | 30 | 1.000 | 8k |
| `groq/openai/gpt-oss-120b` | reasoning | 30 | 1.000 | 8k |
| `openrouter/nemotron-3-nano-omni…:free` | vision | 20 | 50 | — |
| `openrouter/nemotron-3.5-lightning:free` | schnell | 20 | 50 | — |
| `mistral/ministral-3b-2512` | schnell | 750 | — | 1.300k |
| `mistral/ministral-8b-2512` | schnell | 188 | — | 625k |
| `mistral/codestral-2508` | reasoning | 125 | — | 625k |

Drei Befunde, die die ursprüngliche Planung umwerfen:

**Die großen Flash-Modelle haben 20 Anfragen pro Tag.** Nicht 250, nicht 1.500.
Wer „Gemini Flash" als Kamera-Arbeitspferd einplant, bekommt zwanzig Analysen
und danach 429. Tragend sind allein die **Flash-Lite**-Modelle mit je 500/Tag.

**Gemma 4 nimmt Bilder an — bei 14.400 Anfragen pro Tag.** Zwei Modelle,
zusammen 28.800/Tag. Dafür nur 16.000 Token/Minute: bei rund 1.200 Token je
Bildanalyse sind das ~13 Anfragen/Minute, das Tokenfenster bindet also vor dem
Anfragenfenster. Der Ledger bucht die Schätzung deshalb *vor* dem Absenden.

**Aber Gemma kann kein `responseSchema`** — zweimal gemessen, jeweils leere
Antwort statt JSON. Für die übliche Kameraanalyse („Person? Paket?" als
strukturierte Felder) fällt es damit aus; der Router filtert es korrekt heraus.
Als Reserve taugt es für freie Bildbeschreibung. Das halbiert den scheinbaren
Puffer nicht — es trennt ihn in zwei Töpfe.

**Groqs Qwen3.8 kann Bilder** und erkennt sie korrekt. Damit hängt die
Bildanalyse nicht mehr an einem einzigen Anbieter — der härteste Punkt des
ursprünglichen Entwurfs ist entschärft.

**Der Vision-Test läuft in zwei Runden.** Eine einzelne Runde lässt sich mit
rund fünf Prozent Wahrscheinlichkeit erraten — und genau das ist passiert:
Mistrals Ministral bestand einen Lauf und fiel im nächsten durch. Bei zwei
Runden mit je neuen Farben liegt die Ratequote unter einem Promille.

Was dabei herauskam: Ministral ist **kein** Vision-Kanal. Es trifft je eine
Farbe und rät die zweite („Grün, Dunkelblau“ statt grün und orange). Wer
Türkis für Blau hält, beschreibt auch eine Kameraszene entsprechend. Mistral
bleibt als schneller, schemafester Text-Kanal — mehr nicht.

Realistisch, für **Kameraanalyse mit Schema** — der Normalfall:

| | Kapazität |
|---|---|
| Gemini Flash-Lite (2 Modelle) | 1.000/Tag |
| Groq Qwen3.8 | 1.000/Tag, aber 8k Token/Minute ≈ 6/min |
| Gemini 3.5 Flash | 20/Tag |
| OpenRouter Nemotron Omni | 50/Tag, unzuverlässig |

Für **freie Bildbeschreibung ohne Schema** kommen Gemmas 28.800/Tag dazu.
Mistral trägt zur Bildanalyse nichts bei, wohl aber zu `schnell` und
`reasoning`.

Das Kontingent ist damit kein Engpass mehr, sondern Buchhaltung — genau wie im
Konzept angenommen, nur über andere Kanäle als dort vermutet. Die ungedeckelten
Kanäle allein (Google, Groq) tragen rund 2.000 Analysen/Tag; Mistral kommt als
gedeckelter Puffer dahinter.

Gegen das Nutzungsmuster des Konzepts gehalten (50–300 Analysen/Tag bei drei
bis vier Außenkameras mit Bewegungsauslöser): reichlich Luft. Erst „jede
Frigate-Detektion ungefiltert" käme in die Nähe.

Reasoning bleibt die dünnste Stelle: zwei Google-Modelle mit je 20/Tag, Groq
mit 1.000/Tag bei 8k Token/Minute, dazu Mistrals Codestral.

**OpenRouters kostenlose Endpunkte sind kapazitätsbegrenzt, nicht nur
kontingentbegrenzt.** In zwei Durchläufen scheiterten jeweils andere Prüfungen
an `ResourceExhausted: Worker local total request limit reached (16/16)` beim
Upstream, und ein Modell lief in eine Zeitüberschreitung. Als Reserve, auf die
man sich verlässt, taugt das nicht.

### Anbieter, die es nicht in die Auswahl geschafft haben

Alle drei aus demselben Grund: eine kostenlose Stufe, die beworben wird, aber
über die API nicht erreichbar ist. Das ist kein Randfall, sondern der
Normalfall — deshalb misst diese Integration, statt Dokumentation abzuschreiben.

- **OpenCode Zen** — `MissingSessionID`: *„OpenCode's free tier can only be
  used in OpenCode"*. Verlangt vorher Zahlungsdaten.
- **Cerebras** — `402 Payment required`. Die Limits-Seite des Kontos weist
  großzügige Kontingente aus (450 Anfragen/Minute auf `qwen-3.8-27b`, mit
  Bildern), die API gibt sie ohne bezahlten Tarif nicht heraus.
- **Mistral**, teilweise — nur `codestral-latest` antwortet. `mistral-small`,
  `mistral-medium` und `magistral-small` melden
  `x-ratelimit-limit-req-minute: 0`. Kein Limit, sondern kein Zugang.

---

## Grundsatz

Keine Affiliate-Links, keine bezahlten Empfehlungen, keine Telemetrie.
Nutzdaten gehen ausschließlich an den jeweils gewählten Anbieter.

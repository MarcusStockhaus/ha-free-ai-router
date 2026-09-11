# Entwicklung

Alles, was beim Bauen und Messen hilft. Die Nutzersicht steht in
[README.md](README.md).

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


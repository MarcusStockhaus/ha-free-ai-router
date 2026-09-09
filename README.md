# Free AI Router — Phase 1

Home-Assistant-Integration, die KI-Anfragen über mehrere kostenlose Anbieter
verteilt. Für HA-Installationen ohne GPU.

**Das hier ist die Arbeitsfassung für den Eigengebrauch.** Die veröffentlichungsreife
README mit Erwartungsrahmen, Blueprints und `CONTRIBUTING.md` ist Phase 2.

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

Das Vision-Testbild ist eine 64×64-Fläche in Rot. Geprüft wird nicht nur, ob
das Bild angenommen wird, sondern ob das Modell die Farbe nennt — ein Modell,
das das Bild annimmt und dann rät, taugt als Vision-Kanal nichts.

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
   1.500 RPD.
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

## Grundsatz

Keine Affiliate-Links, keine bezahlten Empfehlungen, keine Telemetrie.
Nutzdaten gehen ausschließlich an den jeweils gewählten Anbieter.

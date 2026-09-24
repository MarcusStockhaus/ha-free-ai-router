# Mitwirken

[English](CONTRIBUTING.md) · **Deutsch**

Beiträge sind willkommen. Die wertvollsten brauchen kein Python:

> **Ein Anbieter ist eine YAML-Datei.** Einen Anbieter aufnehmen, ein Modell
> ergänzen oder ein Limit berichtigen ist eine Datenänderung in
> `custom_components/free_ai_router/providers/`.

Das Projekt ist ein Community-Projekt ohne Supportzusage. Ein Pull Request kann
eine Weile auf Durchsicht warten. Die CI prüft jede Anbieterdatei gegen das
Schema; eine fehlerhafte Änderung erklärt sich damit selbst, ohne auf eine
Antwort warten zu müssen.

---

## Womit anfangen

- **Issues des Modell-Wächters.** Ein zeitgesteuerter Lauf vergleicht die
  Modell-Listen der Anbieter mit den Anbieterdateien und führt je Anbieter ein
  Issue mit dem Label
  [`modell-waechter`](https://github.com/MarcusStockhaus/ha-free-ai-router/issues?q=label%3Amodell-waechter).
  Jedes Issue nennt neue Modelle — mit einer ersten Messung — und
  verschwundene. Daraus eine Änderung an der Anbieterdatei zu machen ist der
  typische erste Beitrag.
- **Abweichende Messungen.** Misst die Integration auf dem eigenen Konto etwas,
  das einer Anbieterdatei widerspricht (ein Modell ohne Bildverarbeitung, ein
  anderes Tageslimit), ist ein Pull Request mit der Messung willkommen.
- **Neue Anbieter.** Am besten passen kostenlose Stufen, die über eine API
  nutzbar sind und keine Zahlungsdaten verlangen.

## Erst messen

Dokumentation und Wirklichkeit weichen oft genug voneinander ab, dass sich
dieses Projekt nur auf Messungen stützt. Von sechs bisher geprüften Anbietern
haben drei eine kostenlose Stufe beworben, die über die API nicht nutzbar war,
und mehrere anderswo genannte Fähigkeiten stimmten nicht — in beide
Richtungen.

Entwicklungsumgebung einmalig anlegen:

```bash
python -m venv .venv
```

Aktivieren — ohne diesen Schritt greift `python` auf den Systeminterpreter zu,
und die Abhängigkeiten fehlen (`ModuleNotFoundError: No module named 'aiohttp'`):

```bash
source .venv/bin/activate      # Linux, macOS
.venv\Scripts\activate          # Windows
```

```bash
pip install -r requirements-dev.txt
cp .env.example .env           # Schlüssel als FAR_KEY_<ANBIETER_ID> eintragen
```

Die Modelle des Anbieters auflisten. Das ist eine einzige Anfrage und
verbraucht kein Kontingent:

```bash
python tools/probe_cli.py --provider <id> --models
```

Die Liste zeigt, was der Anbieter führt, nicht was der eigene Schlüssel darf.
Manche Anbieter listen Modelle, die dann mit 404 oder 402 antworten.

Anbieterdatei schreiben oder ändern, dann prüfen und vermessen:

```bash
python tools/validate_registry.py custom_components/free_ai_router/providers/<id>.yaml
python tools/probe_cli.py --provider <id> --discover
```

`--discover` misst jedes Modell (Erreichbarkeit, Bildeingabe, strukturierte
Ausgabe, Werkzeugaufrufe, Antwortzeit) und hält die Modell-Liste des Anbieters
gegen die Datei.

---

## Einen Anbieter aufnehmen

### 1. Host freigeben

Das ist die einzige Python-Änderung, und sie ist Absicht: Eine Datendatei darf
nie ein neues Ziel für Daten einführen. Anbieterdateien dürfen Modelle,
Limits und Texte ändern, aber nicht, wohin Kamerabilder gehen.

```diff
--- a/custom_components/free_ai_router/allowlist.py
+++ b/custom_components/free_ai_router/allowlist.py
@@ ALLOWED_HOSTS: frozenset[str] = frozenset(
         "api.mistral.ai",                     # Mistral
+        "api.example-ai.com",                 # Example AI
         "api.anthropic.com",                  # Anthropic (api_style-Referenz)
```

### 2. Anbieterdatei schreiben

Neue Datei `custom_components/free_ai_router/providers/example_ai.yaml`. Der
Dateiname muss der `id` entsprechen.

```yaml
id: example_ai
name: Example AI
api_style: openai_compatible          # openai_compatible | anthropic | google
base_url: https://api.example-ai.com/v1
auth:
  type: bearer                        # bearer | header | query

preference: 40                        # kleiner = früher; 10 = erste Wahl, 90 = Reserve
limits_scope: per_model               # per_model | per_key
daily_reset_timezone: UTC

onboarding:
  signup_url: https://console.example-ai.com/keys
  summary_en: Fast inference, second channel for Assist.
  summary_de: Schnelle Inferenz, zweiter Kanal für Assist.
  steps_en:
    - Sign in with a Google or GitHub account
    - Click "Create API key" and give it a name
    - Copy the key, it is shown only once
  steps_de:
    - Mit Google- oder GitHub-Konto anmelden
    - '"Create API key" klicken und einen Namen vergeben'
    - Schlüssel kopieren, er wird nur einmal angezeigt
  data_note_en: No training on free-tier content.
  data_note_de: Kein Training auf Inhalten der kostenlosen Stufe.
  credit_card_required: false

models:
  - id: example-27b
    label: Example 27B
    profiles: [schnell, reasoning]    # schnell | vision | reasoning
    latency_class: sehr_schnell       # sehr_schnell | schnell | normal | langsam
    capabilities:                     # Startwerte — die Messung hat Vorrang
      vision: false
      tools: true
      structured_output: true
      context_tokens: 131072
    limits:
      rpm: 30
      rpd: 1000
      tpm: 60000
```

Kein Adapter, keine Registrierung an anderer Stelle.

## Die Felder

| Feld | Bedeutung |
|---|---|
| `api_style` | API-Dialekt. Ein neuer Dialekt braucht einen Adapter in `adapters/`, also eine Code-Änderung. |
| `preference` | Redaktionelle Rangfolge über alle Anbieter, 0–100. Kleinere Werte kommen zuerst. |
| `limits_scope` | Ob der Anbieter Limits je Modell (Google) oder je Schlüssel über alle Modelle (OpenRouter) zählt. Eine Verwechslung führt zu Limits, die der lokale Zähler nicht kennt. |
| `daily_reset_timezone` | IANA-Zeitzone, in der das Tagesfenster des Anbieters umschlägt. Google: `America/Los_Angeles`. |
| `monthly_budget_usd` | Nur bei Anbietern mit monatlichem Ausgabendeckel statt Zeitfenster (Mistral: 10). Verlangt `pricing` für jedes Modell. |
| `extra_headers` | Feste Zusatzheader, etwa `HTTP-Referer` bei OpenRouter. |
| `onboarding.signup_url` | Direkter Link zur Key-Seite, nicht zur Startseite. |
| `onboarding.steps_en`, `steps_de` | Die Schritte bis zum Schlüssel, in beiden Sprachen gleich viele. Der Einrichtungsdialog zeigt die Fassung in der Systemsprache von Home Assistant. |
| `onboarding.data_note_en`, `data_note_de` | Eine ehrliche Zeile dazu, wie der Anbieter Anfrageinhalte verwendet. Steht direkt über dem Schlüsselfeld. |
| `onboarding.summary_en`, `summary_de` | Optional, aber nur gemeinsam. |
| `onboarding.credit_card_required` | Ob Zahlungsdaten verlangt werden, auch für die kostenlose Stufe. |
| `onboarding.empfohlen` | Optional. Markiert den Anbieter als Empfehlung für den Anfang. Redaktionell, kein Messwert. |
| `models[].profiles` | Welche Profile das Modell bedienen darf: `schnell`, `vision` (Bildanalyse), `reasoning`. |
| `models[].capabilities` | Startwerte. Die Messung mit dem Schlüssel des Nutzers hat Vorrang. |
| `models[].limits` | `rpm`, `rpd`, `tpm`, `tpd`. Fehlt ein Wert, ist er **unbekannt**, nicht unbegrenzt. |
| `models[].pricing` | `input_per_mtok`, `output_per_mtok` in US-Dollar. Pflicht, wenn `monthly_budget_usd` gesetzt ist. |
| `models[].latency_class` | Startwert für die Rangfolge im Profil Schnell. Die gemessene Antwortzeit hat Vorrang. |

Die maschinenlesbare Fassung dieser Regeln ist
`custom_components/free_ai_router/registry_schema.json`. Der Loader lehnt eine
Datei mit fehlender Übersetzung, ungleicher Schrittzahl oder einem Host
außerhalb der Allowlist ab.

## Der Pull Request

Ein, zwei Sätze genügen, sie sollten aber die Messung nennen:

> Nimmt Example AI auf. `--discover` zeigt drei Modelle; `example-27b` besteht
> Erreichbarkeit, Schema und Werkzeuge, keine Bildeingabe. Limits 30 RPM /
> 1.000 RPD laut Kontoseite.

Hat die Messung etwas Unerwartetes ergeben, ist das der wertvollste Teil des
Pull Requests.

## Code-Änderungen

- Code, Kommentare und interne Bezeichner der Integration sind deutsch; alles,
  was ein Nutzer sieht, gibt es auf Englisch und Deutsch. Texte für die
  Oberfläche gehören in `strings.json` (englische Quelle),
  `translations/en.json` und `translations/de.json`; im Python-Code
  zusammengesetzte Texte in `sprache.py`.
- Entity-IDs, Attributschlüssel, Dienstnamen und Dienstfelder sind englisch,
  weil sie sich nicht übersetzen lassen und in Automationen landen.
- Tests laufen ohne Netz und ohne Home Assistant.

## Vor dem Einreichen

```bash
python tools/validate_registry.py
python -m pytest
python -m ruff check custom_components tools tests
```

Die CI führt dieselben Prüfungen aus, dazu Manifest-, Übersetzungs- und
HACS-Prüfung. Für reine Anbieterdateien genügt der erste Befehl.

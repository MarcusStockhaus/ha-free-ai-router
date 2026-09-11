# Mitmachen

Der beste Hebel, den dieses Projekt hat, fällt aus der Architektur ohnehin ab:

> **Ein neuer Anbieter ist eine YAML-Datei. Kein Python.**

Anbieter kommen und gehen ständig, es gibt also dauerhaft etwas zu tun. Und die
Einstiegshürde liegt bei „bestehende Datei abschreiben", nicht bei
„Home-Assistant-Internas verstehen".

Zum Erwartungsrahmen: das Projekt hat **keine Supportzusage**. Ein Pull Request
kann liegenbleiben. Der Schema-Check in der CI ist genau dafür da — damit ein
fehlerhafter PR sich selbst erklärt, statt auf eine Antwort zu warten.

---

## Vorher messen, nicht abschreiben

Das ist keine Höflichkeitsfloskel, sondern die Grundregel dieses Projekts.
Von sechs geprüften Anbietern haben **drei** eine kostenlose Stufe beworben,
die über die API gar nicht erreichbar war. Und mehrere Fähigkeiten, die
überall behauptet werden, stimmten nicht — Googles Gemma kann kein
`responseSchema`, Groqs Qwen kann sehr wohl Bilder.

Also, in dieser Reihenfolge:

```bash
python -m venv .venv
```

Umfeld aktivieren — **das ist der Schritt, der gern vergessen wird.** Ohne ihn
greift `python` aufs System-Python, und die Abhängigkeiten fehlen
(`ModuleNotFoundError: No module named 'aiohttp'`):

```bash
source .venv/bin/activate      # Linux, macOS
.venv\Scripts\activate          # Windows
```

```bash
pip install -r requirements-dev.txt
cp .env.example .env           # Schlüssel eintragen
```

```bash
python tools/probe_cli.py --provider <deiner> --models
```

Das kostet einen einzigen Request und zeigt, welche Modell-IDs der Anbieter
tatsächlich führt. **Achtung:** die Modell-Liste sagt, was der *Anbieter*
führt, nicht was dein *Schlüssel* darf — Cerebras listet Modelle, die dann mit
404 antworten.

Danach die Datei schreiben und vermessen:

```bash
python tools/validate_registry.py custom_components/free_ai_router/providers/<deiner>.yaml
python tools/probe_cli.py --provider <deiner> --discover
```

---

## Der Diff

Hier ein vollständiges Beispiel: Cerebras hinzufügen. (Genau so entstanden —
und wieder entfernt, weil die API mit `402 Payment required` antwortete. Auch
das ist ein Ergebnis.)

### 1. Host in die Allowlist

Das ist der **einzige** Python-Teil, und er ist Absicht: eine Datendatei darf
niemals einen neuen Endpunkt einführen. Auch der signierte Feed-Dienst
([FEED.md](FEED.md)) darf Modelle und Limits aktualisieren, aber nie, wohin
Kamerabilder fließen.

```diff
--- a/custom_components/free_ai_router/allowlist.py
+++ b/custom_components/free_ai_router/allowlist.py
@@ ALLOWED_HOSTS: frozenset[str] = frozenset(
         "opencode.ai",                        # OpenCode Zen
         "openrouter.ai",                      # OpenRouter
+        "api.cerebras.ai",                    # Cerebras
         "api.mistral.ai",                     # Mistral
```

### 2. Die Anbieterdatei

Neu: `custom_components/free_ai_router/providers/cerebras.yaml`.
Der Dateiname muss der `id` entsprechen.

```yaml
id: cerebras
name: Cerebras
api_style: openai_compatible          # openai_compatible | anthropic | google
base_url: https://api.cerebras.ai/v1
auth:
  type: bearer                        # bearer | header | query

preference: 40                        # kleiner = früher. 10 = erste Wahl, 90 = Reserve
limits_scope: per_model               # per_model | per_key
daily_reset_timezone: UTC

onboarding:
  signup_url: https://cloud.cerebras.ai/platform/apikeys
  summary_de: Sehr schnelle Inferenz, zweiter Kanal für Assist neben Groq.
  steps_de:
    - Bei Cerebras Cloud mit Google- oder GitHub-Konto anmelden
    - 'Unter "Billing" prüfen, ob ein Tarif aktiv ist'
    - '"Generate API Key" klicken, Namen vergeben, Key kopieren'
  data_note_de: Kein Training auf Inhalten der kostenlosen Stufe.
  credit_card_required: false

models:
  - id: qwen-3.8-27b
    label: Qwen3.8 27B
    profiles: [schnell, reasoning]    # schnell | vision | reasoning
    latency_class: sehr_schnell
    capabilities:                     # Startwerte — die Messung gewinnt
      vision: false
      tools: true
      structured_output: true
      context_tokens: 131072
    limits:
      rpm: 450
      rpd: 648000
      tpm: 150000
```

Das war alles. Kein Python, kein Adapter, keine Registrierung irgendwo.

---

## Was im Pull Request stehen sollte

Ein Satz genügt, aber er sollte die Messung nennen:

> Cerebras ergänzt. `--discover` zeigt drei Modelle, `qwen-3.8-27b` mit
> 450 RPM / 648.000 RPD laut Kontoseite. Vision nicht getestet, weil kein
> Bildmodell in der kostenlosen Stufe.

Wenn die Messung etwas Unerwartetes ergeben hat, ist das der wertvollste Teil
des PR. Alle interessanten Befunde dieses Projekts kamen so zustande.

## Die Felder im Einzelnen

| Feld | Bedeutung |
|---|---|
| `api_style` | Welcher Dialekt. Neue Dialekte brauchen einen Adapter — dann ist es doch Python. |
| `preference` | Redaktionelle Rangfolge über alle Anbieter. Eine inhaltliche Aussage, kein Rechenwert. |
| `limits_scope` | Zählt der Anbieter je Modell (Google) oder je Schlüssel über alle Modelle (OpenRouter)? Wer das verwechselt, rennt in ein Limit, das der Zähler nicht kennt. |
| `daily_reset_timezone` | Wann das Tagesfenster umschlägt. Google: `America/Los_Angeles`. |
| `monthly_budget_usd` | Nur falls der Anbieter einen Ausgabendeckel hat statt eines Zeitfensters (Mistral: 10). Dann sind `pricing`-Angaben Pflicht. |
| `capabilities` | Startwerte. Was die Fähigkeitsmessung feststellt, gewinnt — und ein selbst beobachteter 429 gewinnt über beides. |
| `limits` | Leer heißt **unbekannt**, nicht unbegrenzt. |
| `data_note_de` | Eine ehrliche Zeile, kein Rechtstext. Sie steht im Einrichtungsassistenten direkt über dem Eingabefeld für den Schlüssel. |

Die maschinenlesbare Fassung derselben Regeln steht in
`custom_components/free_ai_router/registry_schema.json`.

## Vor dem Abschicken

```bash
python tools/validate_registry.py
python -m pytest
python -m ruff check custom_components tools tests
```

Dasselbe läuft in der CI. Für reine Anbieterdateien reicht der erste Befehl.

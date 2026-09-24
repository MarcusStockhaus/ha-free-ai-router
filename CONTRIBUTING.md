# Contributing

**English** · [Deutsch](CONTRIBUTING.de.md)

Contributions are welcome. The most valuable ones need no Python at all:

> **A provider is a YAML file.** Adding a provider, adding a model or
> correcting a limit is a data change in `custom_components/free_ai_router/providers/`.

This is a community project without a support commitment. A pull request may
take a while to be reviewed. The CI checks every provider file against the
schema, so an invalid change explains itself without waiting for a reply.

---

## Where to start

- **Model watcher issues.** A scheduled job compares the providers' model
  lists with the provider files and opens one issue per provider with the
  label [`modell-waechter`](https://github.com/MarcusStockhaus/ha-free-ai-router/issues?q=label%3Amodell-waechter).
  Each issue lists new models — with a first measurement — and models that
  have disappeared. Turning such an issue into a provider file change is the
  typical first contribution.
- **Measured differences.** If the integration measures something on your
  account that contradicts a provider file (a model that cannot process
  images, a different daily limit), a pull request with the measurement is
  welcome.
- **New providers.** Free tiers that are usable through an API and do not
  require payment details are the best fit.

## Measure first

Documentation and reality differ often enough that this project relies only
on measurements. Of six providers checked so far, three advertised a free
tier that was not usable through the API, and several capabilities stated
elsewhere turned out to be wrong in both directions.

Set up the development environment once:

```bash
python -m venv .venv
```

Activate it — without this step `python` uses the system interpreter and the
dependencies are missing (`ModuleNotFoundError: No module named 'aiohttp'`):

```bash
source .venv/bin/activate      # Linux, macOS
.venv\Scripts\activate          # Windows
```

```bash
pip install -r requirements-dev.txt
cp .env.example .env           # add your keys as FAR_KEY_<PROVIDER_ID>
```

List the models the provider offers. This is a single request and uses no
quota:

```bash
python tools/probe_cli.py --provider <id> --models
```

The list shows what the provider offers, not what your key may use. Some
providers list models that then answer with 404 or 402.

Write or change the provider file, then validate and measure it:

```bash
python tools/validate_registry.py custom_components/free_ai_router/providers/<id>.yaml
python tools/probe_cli.py --provider <id> --discover
```

`--discover` measures every model (liveness, image input, structured output,
tool calling, latency) and compares the provider's model list with the file.

---

## Adding a provider

### 1. Allow the host

This is the only Python change, and it is intentional: a data file must never
be able to introduce a new destination for data. Provider files may change
models, limits and texts, but not where camera images are sent.

```diff
--- a/custom_components/free_ai_router/allowlist.py
+++ b/custom_components/free_ai_router/allowlist.py
@@ ALLOWED_HOSTS: frozenset[str] = frozenset(
         "api.mistral.ai",                     # Mistral
+        "api.example-ai.com",                 # Example AI
         "api.anthropic.com",                  # Anthropic (api_style reference)
```

### 2. Write the provider file

New file `custom_components/free_ai_router/providers/example_ai.yaml`. The
file name must match the `id`.

```yaml
id: example_ai
name: Example AI
api_style: openai_compatible          # openai_compatible | anthropic | google
base_url: https://api.example-ai.com/v1
auth:
  type: bearer                        # bearer | header | query

preference: 40                        # lower = earlier; 10 = first choice, 90 = fallback
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
    capabilities:                     # starting values — measurement takes precedence
      vision: false
      tools: true
      structured_output: true
      context_tokens: 131072
    limits:
      rpm: 30
      rpd: 1000
      tpm: 60000
```

No adapter, no registration elsewhere. The profile and latency identifiers
are internal values of the registry format and stay as shown.

## Field reference

| Field | Meaning |
|---|---|
| `api_style` | API dialect. A new dialect requires an adapter in `adapters/`, which is a code change. |
| `preference` | Editorial ranking across all providers, 0–100. Lower values are tried first. |
| `limits_scope` | Whether the provider counts limits per model (Google) or per key across all models (OpenRouter). Getting this wrong leads to limits the local counter does not know about. |
| `daily_reset_timezone` | IANA time zone in which the provider's daily window resets. Google: `America/Los_Angeles`. |
| `monthly_budget_usd` | Only for providers with a monthly spending cap instead of a time window (Mistral: 10). Requires `pricing` for every model. |
| `extra_headers` | Static additional headers, e.g. `HTTP-Referer` for OpenRouter. |
| `onboarding.signup_url` | Deep link to the key page, not the home page. |
| `onboarding.steps_en`, `steps_de` | Steps up to the key, same number in both languages. The setup dialog shows the version matching the Home Assistant system language. |
| `onboarding.data_note_en`, `data_note_de` | One honest line on how the provider uses request content. Shown directly above the key field. |
| `onboarding.summary_en`, `summary_de` | Optional, but only together. |
| `onboarding.credit_card_required` | Whether payment details are required, even for the free tier. |
| `onboarding.empfohlen` | Optional. Marks the provider as recommended to start with. Editorial, not a measurement. |
| `models[].profiles` | Which profiles the model may serve: `schnell` (fast), `vision` (image analysis), `reasoning`. |
| `models[].capabilities` | Starting values. The measurement with the user's key takes precedence. |
| `models[].limits` | `rpm`, `rpd`, `tpm`, `tpd`. Missing means **unknown**, not unlimited. |
| `models[].pricing` | `input_per_mtok`, `output_per_mtok` in USD. Required when `monthly_budget_usd` is set. |
| `models[].latency_class` | Starting value for the ranking in the fast profile. Measured latency takes precedence. |

The machine-readable version of these rules is
`custom_components/free_ai_router/registry_schema.json`. The loader rejects a
file with missing translations, unequal step counts or a host outside the
allowlist.

## The pull request

One or two sentences are enough, but they should name the measurement:

> Adds Example AI. `--discover` shows three models; `example-27b` passes
> liveness, schema and tools, no image input. Limits 30 RPM / 1,000 RPD
> according to the account page.

If the measurement showed something unexpected, that is the most valuable
part of the pull request.

## Code changes

- Code, comments and internal identifiers in the integration are German;
  everything a user sees exists in English and German. User-facing strings
  belong in `strings.json` (English source), `translations/en.json` and
  `translations/de.json`; text assembled in Python belongs in `sprache.py`.
- Entity IDs, attribute keys, service names and service fields are English,
  because they cannot be translated and end up in automations.
- Tests run without network access and without Home Assistant.

## Before submitting

```bash
python tools/validate_registry.py
python -m pytest
python -m ruff check custom_components tools tests
```

The CI runs the same checks plus manifest, translation and HACS validation. For provider files alone, the first command is sufficient.

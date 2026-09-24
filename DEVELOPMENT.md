# Development

**English** · [Deutsch](DEVELOPMENT.de.md)

Architecture, tooling and the measured findings behind the design decisions.
For users, see the [README](README.md); for provider files, see
[CONTRIBUTING.md](CONTRIBUTING.md).

---

## Architecture

```
custom_components/free_ai_router/
├── __init__.py        setup, runtime, migrations, update listener
├── config_flow.py     setup dialog and provider subentries
├── registry.py        provider files → validated data model
├── providers/*.yaml   one file per provider
├── allowlist.py       compiled-in host allowlist
├── router.py          which channel serves a request (pure logic)
├── ledger.py          quota, blocks, monthly spending, persisted
├── client.py          runs a request across channels with fallback
├── adapters/          API dialects: openai_compatible, google, anthropic
├── capabilities.py    measurement of a model with the user's key
├── hintergrund.py     background measurement and notifications
├── ai_task.py         three AI task entities, one per profile
├── conversation.py    conversation agent for Assist
├── task_adapter.py    AI task ↔ client: structure, attachments, JSON
├── sensor.py          usage sensors
├── issues.py          repair notices
├── services.py        service free_ai_router.remeasure
├── diagnostics.py     diagnostics download, keys redacted
├── imaging.py         image preparation
└── sprache.py         texts assembled in code, English and German
```

**Channel.** A provider/model pair with its measured capabilities. The
router filters channels by profile and request requirements (images, tools,
structured output, context size) and orders the remaining ones: available
before waiting, fast profile by latency, otherwise by `preference` and
remaining quota.

**Profiles.** `schnell` (fast), `vision` (image analysis) and `reasoning`
are internal identifiers of the registry format. The entities built from them
have English IDs (`ai_task.free_ai_router_fast`, `…_image_analysis`,
`…_reasoning`), set explicitly in the constructor so they do not depend on
the system language.

**Configuration.** One config entry, one subentry per provider (type
`anbieter`) holding the key and the measured overrides. Current version 2.2:

| Migration | Change |
|---|---|
| 1 → 2 | Providers moved from `data["providers"]` into subentries |
| 2.1 → 2.2 | Removes stored `rpm`/`rpd` measurements that were derived from reset times (see findings) |

**Update listener.** Saving measurements changes a subentry. If providers and
keys are unchanged, the listener rebuilds the channels in place instead of
reloading the entry — otherwise every saved provider would cancel the running
measurement of the others. A dispatcher signal makes the router entities
rewrite their attributes.

## Development environment

```bash
python -m venv .venv
source .venv/bin/activate      # Linux, macOS
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
cp .env.example .env           # keys as FAR_KEY_<PROVIDER_ID>
```

All commands below assume the activated environment.

### Tests and linter

```bash
python -m pytest
python -m ruff check custom_components tools tests
```

Tests run without network access and without Home Assistant. Router, ledger,
capabilities, registry and adapters are pure logic or run against a local
test server, including a failing first provider and exhausted limits. Modules
that import Home Assistant are checked structurally: every step, error and
abort reason has a text in both languages, placeholders match, English texts
contain no German, identifiers are English, every unprotected third-party
import is listed in `manifest.json`, both blueprint variants are equivalent.

### Probe CLI

Measures providers without Home Assistant, using the same code as the
integration:

```bash
python tools/probe_cli.py --discover              # all providers, compare model lists
python tools/probe_cli.py --provider groq         # one provider
python tools/probe_cli.py --cheap                 # liveness and headers only
python tools/probe_cli.py --models                # model list only, no quota used
python tools/probe_cli.py --json result.json      # store raw results
```

Output per model: alive, image input, structured output, tool calling, time
to first token, total duration, raw rate-limit headers, profile coverage.

**Image test.** The test image consists of two randomly chosen colour areas
from a palette of seven. The check passes only if the model names both
colours, over two rounds. Some OpenAI-compatible endpoints silently drop the
image part and answer anyway; a single red area was too weak a test, because
red is exactly what a blind text model guesses. With two of seven colours the
guessing rate is below five percent. The palette avoids intermediate hues:
turquoise was removed after a model consistently called it "light blue" and
failed despite seeing the image. The test proves that an image arrives and is
evaluated, not that a model understands a camera scene.

### Registry validation

```bash
python tools/validate_registry.py
```

Checks every provider file against `registry_schema.json`, the loader rules
and the host allowlist.

## Measurement

### Background measurement

`hintergrund.py` runs 10 seconds after the entry is loaded and then every
6 hours. It measures only what is due (`faellige_modelle`):

- never measured,
- previously only temporarily unavailable,
- unusable without a recorded reason (entries from before 0.3.0),
- unusable for more than 7 days.

Working models are not re-measured periodically; live traffic shows whether
they respond without using quota. A persistent notification per provider
reports the result when something changed or on the first measurement.

### Temporary versus definitive

`ist_voruebergehend` decides whether a failure says anything about the model:

| Temporary — model stays pending | Definitive — model marked unusable, with reason |
|---|---|
| network errors, timeouts | rate limit with quota 0 (`limit_requests == 0`) |
| HTTP 5xx | HTTP 400, 402, 404 |
| HTTP 408, 409, 425, 429 with quota | HTTP 403 while the key is valid |
| HTTP 401; HTTP 403 while the key is invalid | |
| status < 400 carrying an upstream error | |

`uebernehmen` merges results: temporary failures never overwrite earlier
measurements. A liveness-only run never clears capabilities, because "not
measured" is not "cannot do it".

### Limits

A request limit is taken from response headers only when the provider states
the window explicitly (`RateLimitInfo.requests_window_s`, e.g. Mistral's
`x-ratelimit-limit-req-minute`). Everything else comes from the provider
file.

## Model watcher

`tools/waechter.py` runs daily at 04:17 UTC via
`.github/workflows/modell-waechter.yml` (also startable by hand). Per
provider it:

1. fetches the model list (`GET <base_url>/models`, no quota used),
2. filters out what the router cannot use (`REGELN`: audio, speech, image
   generation, embeddings, aliases, deprecated models; on OpenRouter
   everything without `:free`),
3. compares with the provider file,
4. measures each new model once — at most three per provider and run, newest
   first — with the same code as the integration,
5. creates or updates one issue with the label `modell-waechter`.

Measurement results are stored in a hidden marker in the issue body, so they
do not use quota again the next day; temporary failures are not stored. A
closed issue stays closed until the list of candidates changes; without
candidates an open issue is closed. Nothing is added to provider files
automatically — profile and daily limit are editorial decisions, and changes
reach installations as a release through HACS.

```bash
python tools/waechter.py --dry-run                # all providers, report nothing
python tools/waechter.py --dry-run --nur groq     # one provider
```

In the action, keys come from the repository secrets `FAR_KEY_GOOGLE_AI_STUDIO`,
`FAR_KEY_GROQ`, `FAR_KEY_MISTRAL` and `FAR_KEY_OPENROUTER`. Exit code 4 means
at least one provider rejected its key or returned no list; the others are
still processed.

## Localization

- `strings.json` is the English source, `translations/en.json` identical,
  `translations/de.json` German. They cover the setup dialog, entity names,
  attribute labels, repair notices, the service and error messages.
- `sprache.py` holds everything assembled in code: provider cards, coverage,
  measurement reports, block reasons, notifications, system prompts, units.
  The language follows `hass.config.language` (`de*` → German, otherwise
  English); Assist answers in the language of the request.
- **Units** are set per system language when the entity is created. Home
  Assistant intentionally translates `unit_of_measurement` only into English
  to keep statistics stable across language changes.
- **Identifiers** — entity IDs, attribute keys, service and fields — are
  English and never translated.
- Provider files carry `steps_en`/`steps_de`, `data_note_en`/`data_note_de`
  and optionally `summary_en`/`summary_de`; the loader rejects incomplete
  translations.

## Testing in Home Assistant

Copy `custom_components/free_ai_router/` into the `custom_components/`
directory of a test instance, restart, add the integration. Useful checks:

- Setup dialog: key test with a valid and an invalid key, the completion
  screen with expected coverage.
- `ai_task.generate_data` with text, with a camera image and with a
  `structure`.
- Assist with device control.
- Background measurement: notification per provider, attributes `channels`
  and `disabled` update without reloading the entry.
- Diagnostics download: keys redacted.

When checking entity updates through the REST API, note that
`/api/states` returns a cached representation; `last_reported` is only
reliable through the template API.

## Findings

Measured behaviour that shaped the design. Dates refer to 2026.

**Images cost the same at any resolution** (11 Sep). The same scene sent to
`gemini-3.5-flash-lite` at 256 to 4096 pixels always cost 1,110 prompt tokens;
Groq's `qwen3.8-27b` behaved the same way (792). Consequences in
`imaging.py`: images are only downscaled above 1.5 MB (to 2048 pixels, to save
upload time), and the ledger reserves 1,100 tokens per image. With Groq's
8,000 tokens per minute this is the difference between an assumed 30 and an
actual seven analyses per minute.

**Google reports an invalid key as HTTP 400** with `API_KEY_INVALID`, not
401. Without handling this, a dead Google channel was retried on every
request.

**Mistral's limit 0 means no API subscription.** `x-ratelimit-limit-req-minute: 0`
is not a limit: Mistral runs separate subscriptions for API, Le Chat and
code. Only the API subscription with the FREE plan unlocks the models; the
key check reports this as its own case.

**Mistral's free tier is a spending cap** of USD 10 per month rather than a
time window. The ledger tracks monthly spending per provider from measured
tokens and `pricing`, input and output separately, reserves before sending
and corrects after the response.

**Derived request limits were wrong** (24 Sep). The window of a request limit
had been derived from its reset time. Groq replenishes its daily limit
continuously, so the reset is often under two minutes away, and 1,000 per day
was stored as 1,000 per minute. Limits are now taken only with an explicit
window; migration 2.2 removed the stored values.

**Wrapped upstream errors.** OpenRouter returns errors of the upstream
provider (e.g. `ResourceExhausted`) with HTTP 200. Counted as an answer, the
image check recorded "cannot process images". Such responses are now not
measurable.

**Capabilities differ from documentation in both directions.** Groq's Qwen
accepts images; Ministral 3B and 8B process images, which only showed once
the test palette was fixed; several models listed by Google answer with 404. This is why every
installation measures with its own key and the measurement takes precedence.

**Home Assistant frontend.**

- A menu step's title receives no placeholders (`renderMenuHeader` does not
  pass `description_placeholders`); `{name}` in such a title renders as
  `MISSING_VALUE`.
- Entities without a device get no name prefix. The router entities have no
  device — a shared device made the integration page show "Devices that don't
  belong to a sub-entry" — so their translated names start with
  "Free AI Router".
- The device registry does not remove a device that no entity references
  while its config entry still exists.
- `via_device` is deprecated since 2026.8 and no longer used.

**Setup must save immediately.** Early versions measured all models inside
the setup dialog and created the entry only after a summary step. In the
first real beginner test, four providers were entered and the dialog was
closed before the final button — nothing was saved. The entry is now created
as soon as the first key passes the check; measuring happens in the
background.

## Releases

1. Update `version` in `manifest.json`.
2. Add an entry to `CHANGELOG.md` and `CHANGELOG.de.md`.
3. Run tests, linter and registry validation.
4. Push to `main`. HACS installs from the default branch; the CI validates
   the repository for HACS.

## Open items

- **Reasoning capacity** is thin: two Gemini Flash models with 20 requests per
  day each, Groq limited by 8,000 tokens per minute, Codestral. Enough for
  occasional tasks; a further provider would need an allowlist change.
- **Anthropic adapter** is implemented but not used by any provider, and
  therefore untested against a real endpoint.
- **Brands:** no entry in `home-assistant/brands` yet; only required for the
  HACS default catalogue.

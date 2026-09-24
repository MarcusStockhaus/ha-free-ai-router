# Changelog

**English** · [Deutsch](CHANGELOG.de.md)

## 0.5.0 — 2026-09-24

### Breaking changes

Identifiers are now English in every language. Display names, attribute
labels, units and messages continue to follow the system language.

| Before | Now |
|---|---|
| `ai_task.free_ai_router_schnell` | `ai_task.free_ai_router_fast` |
| `ai_task.free_ai_router_bildanalyse` | `ai_task.free_ai_router_image_analysis` |
| `sensor.free_ai_router_anfragen_heute` | `sensor.free_ai_router_requests_today` |
| `sensor.free_ai_router_token_heute` | `sensor.free_ai_router_tokens_today` |
| `sensor.free_ai_router_reserve_gegriffen_heute` | `sensor.free_ai_router_fallbacks_today` |
| `sensor.free_ai_router_verworfen_heute` | `sensor.free_ai_router_failed_today` |
| `sensor.<provider>_anfragen_heute` | `sensor.<provider>_requests_today` |
| service `free_ai_router.neu_vermessen` | `free_ai_router.remeasure` |
| fields `anbieter`, `nur_lebendigkeit` | `providers`, `liveness_only` |
| attributes `zuletzt_genutzter_kanal`, `reserve_gegriffen`, `kanaele`, `abgeschaltet`, `abdeckung` | `last_channel`, `used_fallback`, `channels`, `disabled`, `coverage` |
| sensor attributes `tag`, `anfragen`, `token`, `reserve_gegriffen`, `verworfen` | `day`, `requests`, `tokens`, `fallbacks`, `failed` |
| provider sensor `modelle`, `gesperrt`, `ausgegeben_usd`, `rest_usd` | `models`, `blocked`, `spent_usd`, `remaining_usd` |
| per model `anfragen_heute`, `tageslimit`, `rest`, `aktiv` | `requests_today`, `daily_limit`, `remaining`, `enabled` |

`ai_task.free_ai_router_reasoning` and `conversation.free_ai_router_assist`
are unchanged. Unique IDs are unchanged, so history and statistics are kept.

**Existing installations keep their previous entity IDs.** To switch, rename
the entities under **Settings → Entities** and update automations, scripts
and dashboards that reference them. Service calls and templates that use
attribute keys must be updated in any case.

### Added

- Attribute labels in English and German.
- Documentation in English and German: README, contributing guide,
  development notes, changelog.

### Changed

- Blueprints default to `ai_task.free_ai_router_image_analysis`.
- Service response keys are English: `providers`, `measured`, `alive`,
  `models`, `changes`, `note`, `no_longer_listed`, `duration_s`, `scope`.
- Diagnostics keys are English: `providers`, `runtime`.

## 0.4.0 — 2026-09-24

### Added

- **Model watcher.** A daily GitHub Action compares the providers' model
  lists with the provider files and reports new and discontinued models as
  one issue per provider, including a first measurement of new models.
- **English user interface** throughout: setup dialog, entity names, repair
  notices, service, error messages, notifications, measurement reports and
  provider onboarding. The language follows the Home Assistant system
  language.
- English blueprints `camera_analysis.yaml` and `doorbell.yaml`.
- Provider files carry `steps_en`, `data_note_en` and `summary_en`.

### Changed

- Entity IDs are fixed and no longer derived from the translated name.
- Units follow the system language.
- `strings.json` is the English source.

### Removed

- The signed provider feed and its client. The integration removes its
  cache `.storage/free_ai_router.feed`. Provider updates are delivered as
  releases through HACS.

### Fixed

- Request limits were derived from reset times, which stored Groq's daily
  limit of 1,000 as a per-minute limit. Limits are now accepted only with an
  explicit window; configuration migration 2.2 removes the stored values.

## 0.3.0 — 2026-09-24

### Changed

- **Setup saves after the first key.** The dialog consists of two steps,
  provider and key. The completion screen shows the expected profile
  coverage and recommends the next provider. Additional providers are added
  via **Add provider**.
- **Background measurement.** Capabilities are measured 10 seconds after
  loading and then every 6 hours, only for models that are due. The result
  arrives as a notification per provider.
- Saving measurements updates the running instance without reloading the
  integration.

### Fixed

- Temporary failures (server errors, timeouts, rate limits) no longer mark a
  model as unusable. Unusable models carry a reason and are retried after
  7 days.
- Upstream errors wrapped in an HTTP 200 response were counted as a
  measurement result.
- The measurement report no longer contains raw provider responses.
- Missing text after **Replace key**.
- Deprecated `via_device` removed from provider devices.

## 0.2.0 — 2026-09-16

### Added

- Installation through HACS as a custom repository.
- The key check distinguishes rejected key, missing subscription, reached
  limit and unavailable provider.
- Recommended providers are marked in the provider selection.
- Blueprints report a failed analysis as a notification.
- Diagnostics download with redacted keys.

### Changed

- Router entity names include "Free AI Router".
- The fallback sensor is no longer a diagnostic entity and appears on
  automatically generated dashboards.
- Repair notices point to the actual buttons (**Add provider**,
  **Replace key**).

### Fixed

- Setup could end without creating the entry when the summary step was
  closed (2026-09-17).
- Menu title showed `MISSING_VALUE` (2026-09-17).
- Missing requirement `voluptuous-openapi` prevented loading on fresh
  installations (2026-09-17).

## 0.1.0 — 2026-09-12

First public version.

- AI task entities for the profiles fast, image analysis and reasoning;
  conversation agent for Assist with device control.
- Routing with fallback across Google AI Studio, Groq, Mistral and
  OpenRouter; per-minute, per-day, token and monthly spending limits.
- Capability measurement with the user's key.
- Providers as config subentries with their own device and usage sensor.
- Usage sensors, repair notices, service for measuring again.
- Blueprints for camera analysis and doorbells (German).

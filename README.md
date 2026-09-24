<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/free_ai_router/brand/dark_logo@2x.png">
    <img alt="Free AI Router" src="custom_components/free_ai_router/brand/logo@2x.png" width="420">
  </picture>
</h1>

**English** · [Deutsch](README.de.md)

AI features for Home Assistant based on the free tiers of several AI providers.
Free AI Router bundles Google AI Studio, Groq, Mistral and OpenRouter behind
Home Assistant's standard AI interfaces, routes every request to a suitable
model and falls back to another provider automatically when a model fails or
reaches its limit. No local GPU is required — it runs on Home Assistant Green,
Yellow, a Raspberry Pi or a small NUC.

---

## Features

- **Standard Home Assistant entities.** Three [AI task](https://www.home-assistant.io/integrations/ai_task/)
  entities and one [conversation agent](https://www.home-assistant.io/integrations/conversation/)
  for Assist. Every automation and blueprint that lets you pick an AI task
  entity works with them.
- **Routing with automatic fallback.** Each request goes to the best available
  model for the task. If a model is overloaded, rejects the key or reaches a
  limit, the next suitable model takes over — at another provider if
  necessary.
- **Quota tracking.** Per-minute, per-day and token limits as well as monthly
  spending caps are tracked per model. Requests that hit only a per-minute
  limit are queued briefly instead of failing.
- **Capabilities measured with your own key.** Image input, structured output,
  tool calling and response time are measured for every model in the
  background. The measurement reflects what your account can actually do and
  takes precedence over published information.
- **Setup in seconds.** Choose a provider, paste the key, done. Additional
  providers are added individually on the integration page.
- **Monitoring.** Usage sensors, repair notices for rejected keys, uncovered
  profiles and used-up budgets, and a downloadable diagnostics file.
- **Blueprints** for camera analysis on motion and for doorbells, in English
  and German.
- **English and German** throughout the user interface.

## Use cases

| Use case | Entity | Example |
|---|---|---|
| Camera analysis | `ai_task.free_ai_router_image_analysis` | Motion at the driveway → "A delivery van, one person carrying a parcel." With structured fields such as `person`, `vehicle`, `package` for conditions. |
| Doorbell | `ai_task.free_ai_router_image_analysis` | Someone rings → a one-sentence description of who is at the door, spoken on a speaker or sent to a phone. |
| Voice control | `conversation.free_ai_router_assist` | Assist with a language model that understands free-form requests and controls exposed devices. |
| Text tasks in automations | `ai_task.free_ai_router_fast` | Summarize the day's calendar and weather, classify incoming notifications, turn free text into structured data. |
| Demanding tasks | `ai_task.free_ai_router_reasoning` | Tasks with a large context or several reasoning steps, such as drafting automation logic from a description. |

## Requirements

- Home Assistant 2025.6 or newer
- An internet connection
- A free account with at least one supported provider. Google AI Studio and
  Groq together cover all profiles with a fallback and require no payment
  details.

---

## Installation

### HACS

1. HACS → menu (⋮) → **Custom repositories**
2. Repository `https://github.com/MarcusStockhaus/ha-free-ai-router`,
   type **Integration**
3. Search for **Free AI Router**, download it and restart Home Assistant

### Manual

Copy `custom_components/free_ai_router` from this repository to
`/config/custom_components/` and restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → Free AI Router**, or
[open the setup directly](https://my.home-assistant.io/redirect/config_flow_start/?domain=free_ai_router).

1. **Choose a provider.** Each provider card lists its capabilities, how it
   handles your data, whether payment details are required, and whether it is
   recommended to start with.
2. **Enter the key.** The card links directly to the provider's key page. The
   key is checked immediately with a real request. If it succeeds, the
   integration is set up.

The capability measurement then runs in the background and takes one to two
minutes per provider. The result arrives as a notification. Until then, the
values from the provider file apply, so the integration is usable right away.

Additional providers are added on the integration page via **Add provider**.
Each provider appears as its own entry there, with **Replace key** and
**Delete**.

---

## Entities

| Entity | Purpose |
|---|---|
| `ai_task.free_ai_router_fast` | Fast profile: short tasks, summaries, classification |
| `ai_task.free_ai_router_image_analysis` | Image analysis profile: camera images |
| `ai_task.free_ai_router_reasoning` | Reasoning profile: large context, multi-step tasks |
| `conversation.free_ai_router_assist` | Conversation agent for Assist, with device control |

Entity IDs, attribute keys and the service are identical in every language,
so blueprints and automations can be shared between installations. Display
names, attribute labels, units and messages follow the Home Assistant system
language (English or German).

Each of these entities exposes the following attributes:

| Attribute | Meaning |
|---|---|
| `last_channel` | Provider and model that answered the last request |
| `used_fallback` | Whether the last request was served by a fallback |
| `channels` | Models currently in use |
| `disabled` | Models excluded after measurement |
| `coverage` | First-choice model per profile |

> **Upgrading from 0.4 or earlier:** existing installations keep their
> previous German entity IDs (for example `ai_task.free_ai_router_bildanalyse`).
> They can be renamed under **Settings → Entities**; automations that reference
> them have to be updated at the same time. See the [changelog](CHANGELOG.md).

## Usage

### In an automation

```yaml
action: ai_task.generate_data
data:
  entity_id: ai_task.free_ai_router_image_analysis
  task_name: Driveway
  instructions: Is a person or a vehicle visible? Answer in one sentence.
  attachments:
    media_content_id: media-source://camera/camera.driveway
    media_content_type: image/jpeg
response_variable: result
```

With a `structure`, the answer is returned as fields instead of text — for
example `person: true` — and can be used directly in conditions.

### Assist

[Open voice assistants](https://my.home-assistant.io/redirect/voice_assistants/),
select an assistant and choose **Free AI Router Assist** as the conversation
agent. The preferred AI task entity for automations is set on the same page.

### Blueprints

| Blueprint | Import |
|---|---|
| Camera analysis on motion | [English](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fcamera_analysis.yaml) · [German](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fkamera_analyse.yaml) |
| Doorbell analysis | [English](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Fdoorbell.yaml) · [German](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FMarcusStockhaus%2Fha-free-ai-router%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ffree_ai_router%2Ftuerklingel.yaml) |

Both blueprints make the cooldown a required setting: the number of
triggers is the only effective way to save quota, because providers bill an
image at roughly the same cost regardless of its resolution. Both also report
a failure — for example a camera without a still image — as a notification
instead of doing nothing.

---

## Providers

Free-tier values as of September 2026. Limits are per model unless stated
otherwise.

| Provider | Models | Requests per day | Notes |
|---|---|---:|---|
| Google AI Studio | Gemini 3.5 / 3.1 Flash-Lite | 500 each | Main channel for image analysis. Content is used for product improvement. |
| | Gemma 4 31B / 26B | 14,400 each | Large buffer, 16,000 tokens per minute. |
| | Gemini 3.5 / 3.8 Flash | 20 each | Reasoning profile. |
| Groq | GPT-OSS 20B / 120B, Qwen3.8 27B | 1,000 each | Lowest latency, preferred for Assist. 8,000 tokens per minute. No training on free-tier content. |
| Mistral | Ministral 3B / 8B, Mistral Small, Codestral | not published | High per-minute limits, capped at USD 10 of usage per month. Content is used for training. Requires the API subscription with the FREE plan. |
| OpenRouter | Nemotron models (free) | 50 per key | Fallback outside Google. |

Measured behaviour worth knowing when planning automations:

- An image costs roughly 1,100 tokens, independent of its resolution.
  Downscaling does not reduce cost.
- The larger Gemini Flash models allow only 20 requests per day. The
  Flash-Lite models carry the daily load.
- Several providers advertise free tiers that are not usable through the API.
  This is why the integration measures instead of relying on documentation.

## Fallback and limits

1. Candidate models are ordered: available ones before those that would have
   to wait; for the fast profile by latency, otherwise by the curated order in
   the provider file, then by remaining quota.
2. If only a per-minute limit is reached, the request waits up to 20 seconds.
3. Daily limits and monthly spending caps switch to the next model
   immediately. Daily counters follow the provider's time zone.
4. A rejected key blocks the affected model immediately instead of retrying it
   on every request, and raises a repair notice. The block is lifted as soon as
   a request with that key succeeds again.
5. Only when no model is left does the call fail, with a message listing each
   attempt. Automations can handle this with `continue_on_error: true`.

## Measuring capabilities

The background measurement runs ten seconds after every start and then every
six hours. It only measures what is pending: new models, models that were
temporarily unavailable, and models that have been unusable for a week.
Working models are not re-measured periodically — live traffic already shows
whether they respond, without using quota.

The service `free_ai_router.remeasure` measures all models on demand:

```yaml
action: free_ai_router.remeasure
data:
  providers: [groq]     # optional, default: all configured providers
  liveness_only: false  # optional, one request per model instead of all checks
response_variable: report
```

The response lists, per provider, how many models were measured and are
alive, a short line per model, and what changed. Results caused by temporary
disruptions (network errors, server errors, rate limits) never overwrite
earlier measurements.

## Sensors

| Sensor | Meaning |
|---|---|
| `sensor.free_ai_router_requests_today` | Requests today |
| `sensor.free_ai_router_tokens_today` | Tokens today |
| `sensor.free_ai_router_fallbacks_today` | Requests served by a fallback today |
| `sensor.free_ai_router_failed_today` | Requests no model could handle |
| `sensor.<provider>_requests_today` | Requests per provider, e.g. `sensor.groq_requests_today` |

The provider sensors carry the state of each model in the attribute `models`
(`requests_today`, `daily_limit`, `remaining`, `enabled`), active blocks in
`blocked`, and — for providers with a monthly spending cap — `budget_usd`,
`spent_usd` and `remaining_usd`.

A rising fallback count is the earliest sign that a primary channel has
failed permanently.

```yaml
type: entities
title: Free AI Router
entities:
  - sensor.free_ai_router_requests_today
  - sensor.free_ai_router_tokens_today
  - sensor.free_ai_router_fallbacks_today
  - sensor.free_ai_router_failed_today
```

## Troubleshooting

- **Diagnostics:** integration page → menu → **Download diagnostics**. The file
  contains runtime state and counters; API keys are redacted.
- **Key check during setup** distinguishes four cases: key rejected, no quota
  enabled for the account, limit reached, and provider unavailable. Each
  message states what to do next.
- **Repairs** (Settings → Repairs) report rejected keys, profiles without a
  model, and used-up monthly budgets.

---

## Privacy and security

- Request content is sent only to the provider that handles the request.
  Each provider card states how that provider uses the data.
- No telemetry, no affiliate links.
- Provider endpoints are restricted by an allowlist compiled into the code. A
  provider file cannot introduce a new destination for data.

## Limitations

- An internet connection is required. Not suitable for safety-critical
  functions.
- Free tiers are defined by the providers and can change at any time.
- Accounts must be created by the user; captcha and terms of service prevent
  automation.

## Updates

A scheduled check compares the model lists published by the providers with
the provider files and reports new or discontinued models. Updated provider
files are released through HACS. Each installation then measures new models
with its own key.

## Support

This is a community project without a support commitment. Issues and pull
requests are welcome. Adding a provider requires only a YAML file — see
[CONTRIBUTING.md](CONTRIBUTING.md). Architecture, tooling and measured
findings are described in [DEVELOPMENT.md](DEVELOPMENT.md); changes per
version in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)

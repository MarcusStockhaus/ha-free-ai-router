# Wiedereinstieg

Stand 09.09.2026. Phase 1 ist code-komplett, 110 Tests grün, drei Commits.
Was fehlt, ist die Verifikation im Home-Assistant-Devcontainer — dort war bisher
nichts lauffähig, weil HA in der Entwicklungsumgebung nicht installierbar war.

---

## 1. Zuerst: Devcontainer, sonst nichts

Alles unter „HA-seitig" ist geschrieben und kompiliert, aber **nie ausgeführt**.
Das ist die größte unverifizierte Fläche des Projekts, und jeder weitere Ausbau
darauf wäre auf Sand gebaut.

```bash
python -m pytest && python tools/validate_registry.py
```

Dann `custom_components/free_ai_router/` in `config/custom_components/` des
Devcontainers verlinken, HA starten, Integration hinzufügen.

**Konkrete Stellen, an denen es brechen wird** — dort zuerst hinsehen:

| Datei | Risiko |
|---|---|
| `config_flow.py` | `async_show_progress` + `progress_task`, Menü-Schritt `result`, `_get_reconfigure_entry()` |
| `conversation.py` | `chat_log.async_provide_llm_data(...)`-Signatur, `llm.ToolInput`-Feldnamen, `chat_log.continue_conversation` |
| `ai_task.py` | MRO von `FreeAITaskEntity(ai_task.AITaskEntity, RouterEntity)`, `conversation.AssistantContent` |
| `__init__.py` | `Platform("ai_task")` muss existieren, `single_config_entry` + Reconfigure |
| `task_adapter.py` | Feldnamen des `ai_task`-Attachments (`mime_type`, `path`) |

Diese fünf APIs habe ich einzeln gegen die HA-Quelle nachgeschlagen, aber nicht
ausgeführt. Erwartungsgemäß sind ein bis drei Einzeiler zu korrigieren.

## 2. Die vier offenen Akzeptanzkriterien abhaken

- [ ] Ein Anbieter über den Config Flow einrichten, ohne in einer Anbieter-Doku
      nachzuschlagen
- [ ] Zweiten Anbieter hinzufügen, Abschlussübersicht prüfen
- [ ] `ai_task.generate_data` mit echtem Kamerabild-Anhang
- [ ] Key von Google ungültig machen → Anfrage geht über Groq durch, Wechsel
      steht im Log (`Reserve gegriffen: …`)

Die anderen drei Kriterien sind offline abgedeckt (`tests/test_router.py`,
`tests/test_client.py`).

## 3. Danach: Bildskalierung nachziehen

Im Konzept als Phase-2-Blueprint eingeordnet — durch die Messung ist es aber
ein Phase-1-Thema geworden: Gemma hat **16.000 Token/Minute**. Ein unskaliertes
1080p-Kamerabild frisst das Minutenfenster allein auf. HA bringt Pillow mit;
die Skalierung auf 768 px gehört vor den Versand in `task_adapter.py`.

`estimate_input_tokens()` schätzt aktuell aus der Dateigröße — nach der
Skalierung wird die Schätzung genauer und die Vorbuchung im Ledger schärfer.

## 4. ~~Nachmessen~~ — erledigt am 10.09.2026

- `gemini-3.8-flash`: war ein transienter 503, jetzt 4/4. ✓
- `openrouter/nemotron-3-nano-omni`: kann Schema, die Ausfälle sind
  Kapazitätsgrenzen beim Upstream (`ResourceExhausted`, 16/16 Worker) und
  treffen bei jedem Lauf andere Prüfungen. Als verlässliche Reserve
  ungeeignet — nicht wegen des Modells, sondern wegen des Endpunkts.
- **Neu und wichtiger:** Googles Gemma kann *kein* `responseSchema`. Zweimal
  gemessen. Damit fällt der große Gemma-Puffer für die übliche Kameraanalyse
  mit Schema aus. Registry und README sind entsprechend korrigiert; der Router
  filtert korrekt.

## 5. Offene Entwurfsfrage: Reasoning ist zu dünn

Nach der Messung ist Reasoning die schwächste Stelle: zwei Google-Modelle mit je
**20 Anfragen/Tag** plus Groq mit 1.000/Tag bei 8k Token/Minute. Für
Automationsvorschläge (selten, großer Kontext) reicht das; für alles Häufigere
nicht.

Beide Kandidaten sind am 09./10.09.2026 durchgemessen worden:

- **Cerebras ist raus.** `402 Payment required` auf allen Modellen; die
  kostenlose Stufe ist keine. Die Datei wurde wieder entfernt, der Host bleibt
  in der Allowlist.
- **Mistral liefert einen Kanal:** `codestral-latest` (Structured Output,
  Werkzeuge, 0,17 s, keine Bilder). Die interessanten Modelle
  — `magistral-small-latest` für Reasoning und `mistral-small-latest` als
  vierter Vision-Kanal — melden `limit-req-minute: 0`.

**Aus Mistrals OpenAPI-Spec (10.09.2026) geklärt, warum:** Mistral führt drei
getrennte Produktlinien mit je eigenem Abonnement — `APIPlan` (`FREE` |
`PAY_AS_YOU_GO`), `ChatPlan` (Le Chat: `INDIVIDUAL` | `EDU` | `TEAM`) und
`CodePlan`. Ein neuer Schlüssel hilft nicht: nötig ist das **API-Abonnement mit
Plan `FREE`**, und das ist etwas anderes als Labs oder Le Chat. Codestral läuft,
weil es über die Code-Linie abgedeckt ist.

`x-ratelimit-limit-req-minute: 0` ist also kein Limit, sondern schlicht kein
API-Abonnement.

Nachsehen lässt sich das über `GET /v1/admin/rate-limit` (liefert
`requests_per_second` und `tokens_limits_by_model`). Das verlangt allerdings
einen **admin-scoped** Schlüssel — laut Spec werden normale Inferenz-Keys
abgewiesen. Für eine reine Diagnose ist das der falsche Aufwand: ein Admin-Key
darf Nutzer, Workspaces und Ausgabengrenzen verwalten. Die Konsole zeigt
dasselbe.

Wenn das API-Abonnement steht:

```bash
python tools/probe_cli.py --provider mistral --discover
```

Bleibt es bei Limit 0, ist Reasoning dauerhaft dünn: zwei Google-Modelle mit je
20/Tag, Groq mit 1.000/Tag bei 8k Token/Minute, Mistral-Codestral. Für
Automationsvorschläge reicht das; für Häufigeres wäre der nächste Kandidat ein
Anbieter außerhalb der bisherigen Allowlist — und das ist dann eine
Code-Änderung in `allowlist.py`, kein Datenupdate.

## 5b. Für Phase 4 vorgemerkt: Ausgabendeckel als eigener Limit-Typ

Mistrals kostenlose Stufe ist auf **10 $ API-Nutzung im Monat** gedeckelt. Das
Registry-Schema kennt nur Zeitfenster (`rpm`, `rpd`, `tpm`, `tpd`) — einen
Ausgabendeckel kann der Ledger damit nicht führen.

Gehört zusammen mit der Kostenwahrheit in Phase 4 gelöst: die `pricing`-Felder
sind im Schema schon vorgesehen, der Ledger zählt bereits Token je Modell.
Nötig wären ein Feld `monthly_budget_usd` je Anbieter und eine
Verbrauchsschätzung aus Token × Preis. Erst dann kann der Router „noch 3 $ im
Monat" als Rangkriterium behandeln.

Bis dahin gilt die Behelfslösung: `preference: 50` sorgt dafür, dass die
ungedeckelten Kanäle zuerst drankommen.

## 6. Erst danach Phase 2

Blueprints, Verbrauchssensoren, öffentliche README, `CONTRIBUTING.md`,
CI-Schemacheck (`tools/validate_registry.py` ist dafür schon fertig).

Nicht vorziehen: der Feed-Dienst (Phase 3) ist der technisch reizvollste Teil
und der einzige, den du für dich nicht brauchst.

---

## Kleinkram, notiert damit er nicht verlorengeht

- Die vier API-Keys standen im Klartext in einem Chatverlauf. **Neu ausrollen.**
- `.env` ist gitignoriert und war nie im Repo.
- Der `anthropic`-Adapter ist implementiert, aber von keinem Anbieter benutzt
  und daher ungetestet gegen einen echten Endpunkt.
- Die Auslegung „kein Kanal übrig → `HomeAssistantError`" statt stillem
  Verwerfen ist bewusst so gewählt (siehe README, Abschnitt „Was bei Limit oder
  Ausfall passiert"). Falls das anders gemeint war: kleine Änderung in
  `ai_task.py`.

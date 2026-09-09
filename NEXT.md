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

## 4. Nachmessen, was beim ersten Durchlauf an Fremdfehlern hing

```bash
python tools/probe_cli.py --provider google_ai_studio --provider openrouter --json befund.json
```

- `gemini-3.8-flash`: Structured Output scheiterte an einem 503
  („experiencing high demand"), nicht am Modell.
- `openrouter/nemotron-3-nano-omni`: Schema und Werkzeuge scheiterten an
  `ResourceExhausted` beim Upstream, nicht am Modell. Vision funktionierte.

Beides sind vermutlich falsche Negativbefunde. Solange sie so in der Registry
stehen, schaltet der Config Flow diese Fähigkeiten unnötig ab.

## 5. Offene Entwurfsfrage: Reasoning ist zu dünn

Nach der Messung ist Reasoning die schwächste Stelle: zwei Google-Modelle mit je
**20 Anfragen/Tag** plus Groq mit 1.000/Tag bei 8k Token/Minute. Für
Automationsvorschläge (selten, großer Kontext) reicht das; für alles Häufigere
nicht.

`api.cerebras.ai` und `api.mistral.ai` stehen bereits in der Host-Allowlist —
ein vierter Anbieter wäre eine reine YAML-Datei plus ein Key. Erst messen, dann
eintragen.

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

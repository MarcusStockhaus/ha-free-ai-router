# Wiedereinstieg

Stand 11.09.2026. Phase 1 läuft in einem echten Home Assistant (2026.8.3).
122 Tests grün.

---

## 1. ~~Devcontainer~~ — läuft produktiv, verifiziert

Gegen die Produktivinstallation gefahren (vom Autor ausdrücklich freigegeben,
abweichend vom Brief). Die Dateien liegen über die Samba-Freigabe unter `//homeassistant.local/config/custom_components/free_ai_router`, gesteürtree_ai_router`,
Steuerung über die REST- und WebSocket-API mit einem Long-Lived Token.

Verifiziert und ohne einen einzigen Logeintrag von uns:

| | |
|---|---|
| Config Flow, Anbieterkarten, Key-Test | ✅ |
| `async_show_progress` über 126 s Messung | ✅ |
| Menü-Schritt, zweiter Anbieter, Abschlussübersicht | ✅ |
| Entry anlegen, 4 Entities | ✅ |
| `ai_task.generate_data`, Text | ✅ |
| `ai_task.generate_data`, Kamerabild + Schema | ✅ |
| Assist mit Werkzeugaufrufen | ✅ |
| Entity-Attribute, Abdeckung, Ledger | ✅ |

**Ein echter Fehler gefunden und behoben:** `structure_to_json_schema` rief
`voluptuous_openapi.convert()` ohne `custom_serializer` auf und scheiterte an
`cannot use 'BooleanSelector' as a dict key`. Richtig ist
`llm.selector_serializer`, bzw. der Serializer der LLM-API, wenn eine vorliegt.
Diese Stelle ist lokal nicht testbar — sie braucht HAs Selector-Klassen.

## 2. Akzeptanzkriterien

- [x] Ein Anbieter über den Config Flow einrichten, ohne nachzuschlagen
- [x] Zweiten Anbieter hinzufügen, Abschlussübersicht korrekt
- [x] `ai_task.generate_data` mit echtem Kamerabild-Anhang
- [ ] **Offen:** Key von Google ungültig machen → Wechsel auf Groq im Log

Der letzte Punkt braucht einen Eingriff in `.storage/core.config_entries`
(Key verstümmeln, Neustart, testen, Sicherung zurückspielen). Offline ist der
Fall abgedeckt (`tests/test_client.py`), live noch nicht.

Die anderen drei Kriterien sind offline abgedeckt (`tests/test_router.py`,
`tests/test_client.py`).

## 2b. Aufräumen, wenn Phase 1 abgenommen ist

- `configuration.yaml`: die Zeile `custom_components.free_ai_router: debug`
  wieder entfernen. Sicherung liegt als `configuration.yaml.vor-free-ai-router`.
- Long-Lived Token widerrufen (Profil → Sicherheit).
- Sicherung `.storage/core.config_entries.vor-ausfalltest` löschen, falls der
  Ausfalltest gelaufen ist.

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

Die Daten liegen seit dem 10.09.2026 vollständig vor: `monthly_budget_usd: 10`
am Anbieter, `pricing` je Modell (von mistral.ai/pricing/api). Der Ledger zählt
Token bereits je Modell. Was fehlt, ist nur noch die Multiplikation und ein
Monatsfenster im Ledger — dann kann der Router „noch 3 $ im Monat" als
Rangkriterium behandeln.

Rechnerisch trägt der Deckel rund 2.560 Kameraanalysen am Tag auf
`ministral-3b-2512`. Der Ledger merkt davon heute nichts; bis Phase 4 schützt
allein die Rangfolge.

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

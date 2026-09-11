# Wiedereinstieg

Stand 11.09.2026. Phase 1 läuft in einem echten Home Assistant (2026.8.3).
122 Tests grün.

---

## 1. ~~Devcontainer~~ — läuft produktiv, verifiziert

Gegen die Produktivinstallation gefahren (vom Autor ausdrücklich freigegeben,
abweichend vom Brief). Die Dateien liegen über die Samba-Freigabe unter `//homeassistant.local/config/custom_components/free_ai_router`, gesteuert wird über die REST- und WebSocket-API mit einem Long-Lived Token.

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
- [x] Key von Google ungültig machen → Wechsel auf Groq, Meldung im Log

**Alle sieben Akzeptanzkriterien aus dem Brief sind erfüllt.**

Der Ausfalltest hat einen zweiten echten Fehler gefunden: Google meldet einen
ungültigen Schlüssel mit **HTTP 400 und `API_KEY_INVALID`**, nicht mit 401.
Die Auth-Erkennung griff deshalb nicht, und ein toter Google-Kanal wurde bei
jeder Anfrage neu angeklopft. Nach dem Fix:

| | Dauer | Reserve gegriffen |
|---|---:|---|
| Lauf 1 | 1,6 s | ja — Google abgelehnt, Wechsel auf Groq |
| Lauf 2 | 0,4 s | nein — Google im Ledger gesperrt, direkt zu Groq |

Nebenbei bestätigt: der Ledger überlebt den HA-Neustart. Die Sperren standen
danach noch mit 182 Sekunden Restlaufzeit in
`.storage/free_ai_router.ledger`.

## 2b. Aufräumen, wenn Phase 1 abgenommen ist

- `configuration.yaml`: die Zeile `custom_components.free_ai_router: debug`
  wieder entfernen. Sicherung liegt als `configuration.yaml.vor-free-ai-router`.
- Long-Lived Token widerrufen (Profil → Sicherheit).
- Sicherung `.storage/core.config_entries.vor-ausfalltest` löschen, falls der
  Ausfalltest gelaufen ist.

## 3. ~~Bildskalierung~~ — gemessen, Annahme widerlegt (11.09.2026)

Das Konzept sagt, Bilder vor dem Versand auf 768 Pixel zu skalieren spare
Kontingent. **Das stimmt nicht.** Gemessen mit demselben Motiv gegen
`gemini-3.5-flash-lite`:

| Kante | Groesse | KB | Prompt-Token |
|---:|---|---:|---:|
| 256 | 256x144 | 9 | 1110 |
| 768 | 768x432 | 67 | 1110 |
| 1280 | 1280x720 | 116 | 1110 |
| 2048 | 2048x1152 | 290 | 1110 |
| 4096 | 4096x2304 | 749 | 1110 |

Ueber 30-fache Pixelzahl und 80-fache Dateigroesse derselbe Preis; Google
normalisiert das Bild vor der Abrechnung. Groqs `qwen3.8-27b` verhaelt sich
genauso (792 Token, unabhaengig von der Groesse).

Zwei Folgerungen, beide in `imaging.py` umgesetzt:

1. Verkleinert wird nur noch, was **ueber 1,5 MB** liegt, und dann auf 2048 —
   das spart Uploadzeit, sonst nichts. Ein Kamerabild mit 80 KB bleibt
   unangetastet und behaelt seine Aufloesung.
2. **Ein Bild kostet rund 1.100 Token, nicht 258.** Die Kachelrechnung des
   Konzepts unterschaetzte um das Vierfache. Der Ledger bucht jetzt den
   gemessenen Wert vor; bei Groqs 8.000 Token/Minute ist das der Unterschied
   zwischen gedachten 30 und tatsaechlichen 10 Analysen pro Minute.

Live gegengeprueft: eine Kameraanalyse verbraucht 1172 Token, die Vorbuchung
liegt bei rund 1130.

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

## 6. Phase 2 — begonnen am 11.09.2026

- [x] **Blueprint Kameraanalyse** — Sperrzeit, genauer Auslöser, freie
      Zusatzbedingung als Eingabefelder. Live geprüft.
- [x] **Verbrauchssensoren** — vier Tageszähler (Anfragen, Token, Reserve
      gegriffen, verworfen) plus einer je Anbieter mit Restkontingent je
      Modell in den Attributen.
- [ ] Blueprint für die Türklingel (Auslöser ist ein Ereignis, keine Bewegung)
- [ ] Fehlerbehandlung für fremde Setups
- [ ] Öffentliche README mit Grundsatz und Erwartungsrahmen
- [ ] `CONTRIBUTING.md` mit genau einem Beispiel-Diff
- [ ] CI-Schemacheck (`tools/validate_registry.py` ist dafür schon fertig)

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

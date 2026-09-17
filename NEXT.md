# Wiedereinstieg

Stand 11.09.2026. Phase 1 läuft in einem echten Home Assistant (2026.8.3),
Phase 2 ist inhaltlich durch, Phase 3 läuft live.
212 Tests grün.

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
- Sicherung `.storage/core.config_entries.vor-ausfalltest` löschen, falls der
  Ausfalltest gelaufen ist.

Zugangsdaten, die dabei zu erneuern sind, stehen absichtlich nicht in dieser
Datei — sie liegt seit dem 11.09.2026 in einem öffentlichen Repo.

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

## 5b. ~~Für Phase 4 vorgemerkt~~ — Ausgabendeckel, gebaut am 11.09.2026

Mistrals kostenlose Stufe ist auf **10 $ API-Nutzung im Monat** gedeckelt. Das
Registry-Schema kennt nur Zeitfenster (`rpm`, `rpd`, `tpm`, `tpd`) — einen
Ausgabendeckel kann der Ledger damit nicht führen.

**Erledigt.** Der Ledger führt jetzt ein Monatsfenster je Anbieter
(`SpendState`), rechnet aus den gemessenen Token und `pricing` den Betrag hoch,
bucht schon beim Absenden vor und berichtigt nach der Antwort — dieselbe
Mechanik wie bei den Token, nur eine Stufe strenger, weil gegen einen
aufgebrauchten Deckel kein Warten hilft.

Ein paar Entscheidungen, die dabei angefallen sind:

- **Ein- und Ausgabe getrennt gerechnet.** Bei `mistral-small` kostet die
  Ausgabe das Vierfache der Eingabe; mit einem Mischpreis wäre der Deckel bei
  langen Antworten zu spät erreicht.
- **Der Deckel hängt am Konto, nicht am Modell.** Anders als die Kontingente,
  die je Modell oder je Schlüssel zählen. Zwei Modelle desselben Anbieters
  teilen sich den Betrag.
- **Monatsgrenze in der Zeitzone des Anbieters**, wie schon der Tageszähler.
- **Das Restbudget geht als `headroom` in die Rangfolge**, also genau dort
  ein, wo schon das Restkontingent steht. Der Router brauchte dafür keine
  Zeile.
- **Ein Deckel ohne Preise wird beim Einlesen abgelehnt.** Er wäre sonst
  keiner: der Ledger zählte Anfragen und Token und erreichte den Betrag nie,
  während die Datei behauptet, der Anbieter sei begrenzt.

Nachgerechnet am echten Registry-Stand: 10 $ tragen **74.627 Kameraanalysen**
auf `ministral-3b-2512` (Bild plus Schema-Antwort, 0,000134 $ je Stück) — rund
2.500 am Tag. Die Simulation über einen ganzen Monat trifft den Deckel auf den
Cent genau und meldet danach „Monatsbudget aufgebraucht (10.00 von 10.00 USD)"
mit der richtigen Wartezeit bis zum Monatsersten.

Sichtbar wird das in den Attributen des Anbieter-Sensors (`budget_usd`,
`ausgegeben_usd`, `rest_usd`) und als Reparatur-Hinweis, sobald der Betrag
erreicht ist. Bewusst kein schwerer Befund: der Router weicht aus, und zum
Monatsersten löst es sich von selbst.

`preference: 50` bleibt trotzdem richtig — die ungedeckelten Kanäle sollen
weiterhin zuerst drankommen, damit Geld erst fließt, wenn Freies aus ist.

## 6. Phase 2 — begonnen am 11.09.2026

- [x] **Blueprint Kameraanalyse** — Sperrzeit, genauer Auslöser, freie
      Zusatzbedingung als Eingabefelder. Live geprüft.
- [x] **Verbrauchssensoren** — vier Tageszähler (Anfragen, Token, Reserve
      gegriffen, verworfen) plus einer je Anbieter mit Restkontingent je
      Modell in den Attributen.
- [x] **Blueprint Türklingel** — erkennt `event`- und `binary_sensor`-Klingeln,
      kurze Sperrzeit gegen doppeltes Drücken. Die Auslöse-Bedingung ist über
      die Template-API gegen sieben Fälle geprüft, ohne etwas in HA anzulegen.
- [x] **Reparatur-Hinweise** über die `issue_registry` — kein Anbieter,
      Schlüssel abgelehnt (mit Deep-Link), Profil ohne Abdeckung. Setzen und
      Löschen live durchgespielt.
- [x] **Öffentliche README** mit Erwartungsrahmen in der Kopfzeile, dem
      Grundsatz und den gemessenen Anbieterzahlen. Die Arbeitsfassung ist
      nach `ENTWICKLUNG.md` gewandert.
- [x] **`CONTRIBUTING.md`** mit dem vollständigen Diff für einen neuen
      Anbieter (Cerebras, so wie er wirklich entstanden ist).
- [x] **CI** — Schemacheck, Linter, Tests, Manifest- und
      Übersetzungsprüfung. Alle Schritte lokal nachgefahren.

**Damit ist Phase 2 inhaltlich durch.** Was zur Veröffentlichung noch fehlt,
ist keine Programmierarbeit mehr:

- [x] Das Repo öffentlich gemacht (11.09.2026); `documentation`,
      `issue_tracker` und die `source_url` der Blueprints zeigen auf die
      echte Adresse. Die Historie ist vorher auf Schlüsselmuster durchsucht
      worden.
- [ ] Jemanden ohne Vorwissen die Türklingel-Analyse einrichten lassen —
      das ist das Abnahmekriterium des Konzepts für Phase 2

---

## 7. Phase 3 — Feed-Dienst, gebaut am 11.09.2026

Ausführlich in [FEED.md](FEED.md). Kurz:

- [x] **Dokumentformat** (`feed.py`) — `providers` in Registry-Form,
      `measurements` daneben. Trennung von Definition und Beobachtung, damit
      eine Messung keine Felder in die Definition einschleusen kann.
- [x] **Signatur** — Ed25519 über die rohen Bytes der Datei, keine
      Kanonisierung. Ohne gültige Signatur gibt es kein Dokument; einen Weg,
      an den Inhalt zu kommen, ohne vorher zu prüfen, hat das Modul nicht.
- [x] **Frische und Rückschrittsschutz** — 14 Tage Obergrenze, vom Client
      gerechnet, und kein Dokument, das älter ist als das zuletzt gesehene.
- [x] **Die Allowlist hält auch hinter gültiger Signatur** — der zugehörige
      Test arbeitet deshalb absichtlich mit einer echten Signatur.
- [x] **Prober** (`tools/prober.py`) — zwei Takte, Zustandsdatei über Läufe
      hinweg, drei Notbremsen gegen Falschmeldungen.
- [x] **Client** (`feed_client.py`) — bedingte Abrufe, 1-MB-Grenze, erneutes
      Prüfen beim Laden aus dem Zwischenspeicher, Ausfall ohne Folgen.
- [x] **Workflow** (`.github/workflows/prober.yml`) — täglich 03:07 UTC der
      volle Lauf, Ergebnis auf den Zweig `feed`.
- [x] **52 Tests** dazu (49 für Feed, Client und Prober, 3 für die
      Unterscheidung „gestört“ / „kann es nicht“), vier Liveläufe gegen die
      echten Anbieter.
- [x] **Im laufenden Home Assistant verifiziert** (11.09.2026, 08:54 UTC):
      die Integration holt den Feed, prüft die Signatur gegen den
      eincompilierten Schlüssel und legt ihn in
      `.storage/free_ai_router.feed` ab. Neun Kanäle, keiner abgeschaltet,
      alle drei Profile gedeckt, `ai_task.generate_data` antwortet.

**Der Livelauf hat wieder etwas widerlegt.** Mistral antwortete mit `429`. Ein
Ratenlimit als Ausfall zu zählen hätte gereicht, um nach drei gedrosselten
Läufen ein gesundes Modell allen Nutzern totzumelden — der Endpunkt hat ja
geantwortet. Seitdem lassen `401`, `402`, `403` und `429` die Runde
ungewertet. Dieselbe Überlegung schützt vor dem abgelaufenen Proberschlüssel.

### Was zum Einschalten noch fehlt

Beides sind Entscheidungen, keine Programmierarbeit:

- [x] **Wo der Feed liegen soll** — entschieden am 11.09.2026: GitHub Pages
      aus dem Zweig `feed`, Ordner `/docs`. `FEED_URL` steht entsprechend auf
      `https://marcusstockhaus.github.io/ha-free-ai-router/v1/providers.json`.
      Eine eigene Domain kommt später davor; bis dahin hängt die Adresse am
      Kontonamen, und ein Umzug kostet ein Release.
- [x] **Pages eingeschaltet** — Zweig `feed`, Ordner `/docs`. Live geprüft:
      `ETag` führt zu `304`, `feed-state.json` wird nicht ausgeliefert, und
      der echte Client-Code holt, prüft und übernimmt das Dokument.
- [x] **Schlüssel** — Signierpaar erzeugt, privat als Secret
      `FAR_FEED_PRIVATE_KEY`, öffentlich in `feed.py`. Zeitplan aktiv:
      **einmal täglich** um 03:07 UTC der volle Lauf. Der stündliche sparsame
      Takt ist bewusst wieder herausgeflogen — der Client sieht nur alle vier
      Stunden nach, und die Totmeldung nach drei Läufen in Folge wäre
      stündlich schon nach drei Stunden gefallen.
- [ ] **Ein eigenes Proberkonto.** Zum Start laufen die Schlüssel des Autors
      (so entschieden am 11.09.2026). Das hat zwei Folgen: der Prober
      verbraucht dessen Kontingent mit — gedeckelt auf ein Viertel, siehe
      `BUDGET_SHARE` —, und die gemessenen Limits sind die seines Kontos. Für
      andere Nutzer stimmen sie nur, solange es ein gewöhnliches Gratiskonto
      ist.
- [ ] **Eine eigene Domain** vor die `github.io`-Adresse. Sie hängt am
      Kontonamen; ohne Domain kostet ein Umzug ein Release, weil die Adresse
      im Quelltext jeder ausgelieferten Fassung steht.

### Zwei Messungen, die der erste Feed-Lauf aufgeworfen hat

- **`mistral/ministral-3b-2512` habe die Bildprüfung bestanden — falsch.**
  Ich hatte das für belastbar gehalten, weil ein bestandener Test sich
  schwerer vortäuschen lässt als ein gescheiterter. Das stimmt nur, solange
  der Test auch wirklich zu Ende läuft. Bei einem Abbruch in Runde zwei
  zählte `_check_vision` „was bis hierhin stimmte" — und damit ging **eine**
  bestandene Runde als Ergebnis durch, mit den rund fünf Prozent Ratequote,
  wegen der die Prüfung überhaupt mehrfach läuft. Behoben am 11.09.2026: eine
  abgebrochene Runde ist jetzt in keiner Richtung ein Befund.

  **Und dann war auch das noch falsch.** Die Gegenprobe schien eindeutig —
  drei Zwei-Runden-Läufe mit Antworten wie „Rot, Blau" statt orange und
  türkis. Sechs *einzeln* protokollierte Runden zeigten dann das Muster:
  vier richtig, und beide Fehlschläge betrafen Türkis, das Ministral
  verlässlich „hellblau" nennt. Da „blau" darin steckt, zählte die Antwort
  als falsch benannte Fremdfarbe.

  Türkis liegt echt zwischen Blau und Grün und war damit ein schlechter
  Testfarbton — die Prüfung soll feststellen, ob das Bild ankommt und
  ausgewertet wird, nicht wie genau ein Modell Zwischentöne benennt. Seit es
  durch Schwarz und Weiß ersetzt ist: **8B trifft 5 von 6, 3B 4 von 6.**
  Ministral sieht. `capabilities.vision` steht jetzt auf `true`.

  `profiles` bleibt trotzdem `[schnell]`, und das ist keine technische
  Aussage: Mistrals kostenlose Stufe verlangt die Zustimmung zum Training auf
  Inhalten. Wer diese Modelle für Kamerabilder freigibt, gibt die Bilder in
  die Modellentwicklung. Das ist eine Entscheidung des Betreibers, kein
  Messergebnis — wer sie treffen will, ergänzt `vision` in `profiles`.

- **`openrouter/nemotron-3-nano-omni` kann Werkzeuge.** Bleibt stehen — die
  Werkzeugprüfung kennt keine Runden und war von dem Fehler nicht betroffen.
  War als `tools: false` eingetragen.

---

## 8. Dienst zum Neuvermessen — gebaut am 11.09.2026

Die Lücke, die der Tag sichtbar gemacht hat: die Fähigkeiten im Config Entry
stammen aus dem Augenblick des Einrichtens und schlagen den Feed. Das ist
richtig — `alive` und Limits hängen am Konto, nicht am Modell — hatte aber zur
Folge, dass ein einmal falsch gemessener Wert unerreichbar war. Drei
Korrekturen am Messverfahren an einem Tag hätten den Nutzer nie erreicht.

`free_ai_router.neu_vermessen`, optional je Anbieter, optional nur die
Lebendigkeit. Die Antwort sagt, was sich geändert hat.

Dieselben Notbremsen wie im Prober, und aus demselben Grund:

- **Ein Lauf ohne jede Antwort wird verworfen** (`should_discard`), sofern
  vorher etwas lief. Sonst schaltet sich die Installation bei einem
  Netzausfall selbst die Kanäle ab.
- **Der sparsame Lauf löscht keine Fähigkeiten** (`merge_overrides`). Er prüft
  sie gar nicht; sie zu leeren wäre wieder die Gleichsetzung von „nicht
  gemessen" mit „kann es nicht".

Die Entscheidungslogik liegt in `capabilities.py`, nicht in `services.py`:
letzteres importiert Home Assistant und ist hier nicht testbar. Acht Tests,
beide Notbremsen gegen abgeschaltete Logik gegengeprüft.

**Live gefahren am 11.09.2026**, 64,7 s für alle drei Anbieter:

```
google_ai_studio: 5 von 6 lebendig
    gemini-3.8-flash: tools nein -> ja
    gemma-4-26b-a4b-it: vision ja -> nein
    gemma-4-31b-it: erreichbar ja -> nein
groq: 3 von 3 lebendig — keine Änderung
mistral: 3 von 4 lebendig
    ministral-3b-2512: vision nein -> ja
```

Die letzte Zeile ist genau der Wert, der vorher unerreichbar war. Danach alle
drei Profile weiter gedeckt, elf Kanäle aktiv, die beiden gemessen toten
abgeschaltet.

**Zu beobachten:** `gemma-4-26b-a4b-it` hat die Bildprüfung diesmal *nicht*
bestanden. Ein Abbruch wäre inzwischen „unbekannt" und würde nichts
überschreiben — hier ist die Prüfung also durchgelaufen und die Antwort war
falsch. Gemma war den ganzen Tag instabil (500er, Zeitüberschreitungen, 31B
gar nicht erreichbar). Ein zweiter Lauf an einem ruhigen Tag sollte das
klären.

---

## 9. Anbieter als Subentries — gebaut, teilweise zurückgenommen (12.09.2026)

Wunsch: eingerichtete Anbieter im Frontend sehen und ändern, statt nur über
den Config Flow hinzuzufügen. Home Assistant bringt dafür **Subentries**
mit — zwei bereits installierte Integrationen (`mqtt`, `llama_conversation`)
nutzen sie, die API wurde am Wheel zu 2026.8.3 nachgelesen statt geraten.

**Geblieben:** jeder Anbieter ist ein Subentry, Fassung 2 mit Migration von
`data["providers"]`. Eigene Zeile auf der Integrationsseite, eigenes Gerät,
eigener Verbrauchssensor, `configuration_url` auf die Key-Seite. Hinzufügen,
Schlüssel ersetzen, Entfernen sind HA-eigene Knöpfe — kein selbstgebautes
Verwaltungsmenü mehr. `_MessSchritte` teilt Schlüsseltest, Messung und
Ergebnis zwischen Einrichtungsassistent und Subentry-Flow.

**Zurückgenommen:** ein zusätzlicher Subentry nur für den Router (die
ai_task-Profile, Assist, die vier Zähler), um die HA-Überschrift „Geräte, die
nicht zu einem Untereintrag gehören" verschwinden zu lassen. Technisch sauber
(Fassung 3, live verifiziert, kein Log-Eintrag mehr) — aber ein erfundener
Eintrag für etwas, das kein Anbieter ist, sich nicht hinzufügen oder entfernen
lässt und im selben Muster steht wie die echten Anbieter. Auf Ansage
zurückgebaut: reiner `git revert`, dazu die Live-Installation über die
`.vor-fassung3`-Sicherungen von Config Entry und Geräteregister auf Fassung 2
zurückgesetzt. Die Überschrift ist wieder da — das ist der ehrlichere Zustand
als ein Untereintrag, der nur da ist, um sie zu vermeiden.

**Lehre:** eine UI-Kosmetik, die einen fiktiven Datensatz braucht, um sich
einzustellen, ist der falsche Preis für die Kosmetik.

**Der richtige Weg, gefunden am 12.09.2026:** kein Gerät für die Router-
Entities. Im Frontend-Bundle nachgesehen, was die Überschrift wirklich
auslöst — `r = [...devices, ...services]` außerhalb der Untereinträge, nicht
leer, während Untereinträge existieren. Unser gemeinsames Geräteobjekt war
`entry_type: service` und landete in `r`. Am eigenen System nachgesehen, wie
HA das bei Integrationen löst, die genau dieses Muster haben:
`llama_conversation` legt auf Config-Entry-Ebene gar kein Gerät an, nur der
`ai_task`-Subentry hat eins. Google Generative AI und OpenAI Conversation
machen es genauso.

Umgesetzt: `RouterEntity` (in `entity.py`) setzt kein `_attr_device_info` mehr.
Die drei `ai_task`-Profile, Assist und die vier Gesamtzähler haben damit gar
kein Gerät — `r` bleibt leer, die Überschrift erscheint nicht. In HA-Core
nachgelesen (`entity_registry._async_get_full_entity_name`), was das für den
Anzeigenamen bedeutet: der Geräte-Präfix entfällt, weil er nur gesetzt wird,
wenn `device_id is not None`. „Free AI Router Schnell" wird zu „Schnell".
Die Entity-IDs bleiben (`ai_task.free_ai_router_schnell` usw.) — die sind
längst vergeben und ändern sich nicht mit dem Anzeigenamen.

`RouterAnbieterSensor` setzt sein eigenes Geräteobjekt weiterhin selbst, direkt
nach dem Konstruktoraufruf — davon unberührt.

**Zwei Stolperfallen live entdeckt, keine davon vorhersehbar aus dem Quelltext
allein:**

1. **Die Registry-Datei zeigte nach dem Neustart noch die alte `device_id`.**
   Sah zunächst nach einem Fehlschlag aus — im HA-Core-Quelltext nachgelesen
   (`entity_platform.py`, `entity_registry.py`) bestätigt, dass
   `device_id=None` bei fehlendem `device_info` korrekt durchgereicht wird.
   Des Rätsels Lösung: die Entity-Registry speichert verzögert (debounced),
   die Datei auf der Freigabe war schlicht noch nicht geschrieben. Ein paar
   Minuten später (durch andere Arbeit an der Doku) war sie es.
2. **Das verwaiste Gerät verschwand nicht von selbst — auch nicht nach dem
   Neustart.** In `device_registry.py` nachgelesen: `async_cleanup()` entfernt
   nur Geräte, deren Config Entry komplett weg ist (`device.config_entry_id
   not in config_entry_ids`), nicht Geräte ohne Entities. Das Gerät blieb
   also stehen, obwohl keine Entity mehr darauf zeigte — und wäre in der
   Integrationsseite wieder als "Geräte, die nicht zu einem Untereintrag
   gehören" aufgetaucht, weil diese Gruppierung rein aus der
   Geräteregistrierung kommt, nicht aus tatsächlich vorhandenen Entities.
   Musste von Hand aus `core.device_registry` entfernt werden — Sicherung
   vorher, danach per Neustart bestätigt: keine Entity referenziert es mehr,
   und HA legt es beim Setup nicht neu an, weil keine Entity mehr danach
   fragt.

Live verifiziert: `core.device_registry` zeigt nur noch die drei
Anbieter-Geräte, alle 11 Entities stehen weiter (`device_id=None` bei den
acht Router-Entities, unverändert bei den drei Anbietersensoren), Anzeigenamen
jetzt ohne Präfix (`Schnell`, `Bildanalyse`, `Reasoning`, `Assist`,
`Anfragen heute`, …), kein Eintrag im Log, `ai_task.generate_data` antwortet.

---

## 10. UX-Durchgang für Einsteiger, gebaut am 16.09.2026

Auslöser: eine Analyse aus Sicht eines kompletten Einsteigers ("wie würde
jemand ohne Vorwissen hier scheitern") hat 13 konkrete Punkte gefunden, dazu
den ausdrücklichen Wunsch nach HACS. Das Einrichten selbst wollte der Autor
erst in der fertigen Fassung ausgiebig selbst testen — dieser Durchgang deckt
alles davor und danach ab.

**Entity-Namen ohne Gerät tragen jetzt den Integrationsnamen im Text selbst**
(`entity.py` hat seit Abschnitt 9 kein `_attr_device_info` mehr): „Free AI
Router Schnell" statt „Schnell". Ohne Gerät gibt es keinen anderen Hinweis,
welche Integration eine Entity wie `ai_task.free_ai_router_schnell` in einer
Auswahlliste neben denen anderer Integrationen überhaupt anbietet — genau das
fiel live auf, als die Sprachassistenten-Auswahl „Home Assistant" und
„Assist" unbeschriftet nebeneinander zeigte. Der Anbietersensor bleibt
unverändert: sein Gerät liefert den Präfix bereits.

**Vier Fehlerarten beim Schlüsseltest statt einer.** `quick_key_check` in
`capabilities.py` gibt jetzt `(gültig, meldung, fehlerart)` zurück,
`_fehlerart()` liest sie aus `ModelProbe.status` und `.rate_limit`:
abgelehnt, kein Abonnement (`rate_limit.limit_requests == 0` — Mistrals
`x-ratelimit-limit-req-minute: 0`, siehe Abschnitt 5), Limit erreicht,
Anbieter nicht erreichbar. Der Config Flow zeigt vier unterschiedene
`errors["base"]`-Texte statt eines pauschalen „nicht angenommen".

**Fortschrittsbalken bei der Fähigkeitsmessung.** `probe_provider` hatte
schon einen `on_progress`-Callback, nur `_async_probe` im Config Flow rief ihn
nie auf. Jetzt ruft er `self.async_update_progress(done/total)` — die Methode
existiert auf `FlowHandler` seit HA 2024.8 laut dessen eigener
Warnmeldung im Quelltext, unser Minimum liegt deutlich darüber. Zwei Minuten
„einen Moment" sahen vorher aus wie ein Absturz.

**Reparaturtexte zeigen auf echte Knöpfe.** „Kein Anbieter eingerichtet" und
„Schlüssel abgelehnt" verwiesen auf „Free AI Router → Konfigurieren" — den
gibt es seit dem Subentry-Umbau nicht mehr. Zeigen jetzt auf „Anbieter
hinzufügen" bzw. auf „Schlüssel ersetzen" in der Anbieterzeile. Ein Test
(`test_keine_verweise_auf_einen_konfigurieren_knopf`) hält das fest, indem er
das Wort „Konfigurieren" aus beiden Textdateien verbannt.

**Empfehlung in der Anbieterauswahl.** Neues optionales Registry-Feld
`onboarding.empfohlen` (`registry.py`, `registry_schema.json`), gesetzt bei
Google AI Studio und Groq — dieselben zwei, die das README ohnehin als
tragende Kombination nennt. Die Anbieterkarte zeigt „— empfohlen für den
Anfang", die Anbieterauswahl einen erklärenden Satz.

**Schlüssel-Seite als klickbarer Link** statt nackter URL in beiden
Schlüssel-Schritten (`{signup_link}` statt `{signup_url}`).

**Umlaute in jedem Text, den ein Nutzer sieht.** Betroffen waren die
Onboarding-Felder aller vier Anbieter-Dateien, drei Stellen im Config Flow,
jede `HomeAssistantError`- bzw. `NoChannelAvailable`-Meldung in `client.py`
und `task_adapter.py`, sowie die beiden `ProviderError`-Meldungen in
`adapters/base.py` — die landen über `ModelProbe.error` direkt im
`error_detail`-Platzhalter des Schlüssel-Schritts, sind also trotz des
unauffälligen Dateinamens echter Dialogtext. Interne Log-Zeilen
(`_LOGGER.warning`/`.info` in `client.py`) blieben absichtlich unangetastet
— ein Test (`test_client.py`) pinnt ihre ASCII-Schreibweise, und ein Log ist
kein Text, den ein Nutzer im Dialog sieht. Wo dieselbe Zeichenkette an
mehreren Stellen stand (`"Schlüssel abgelehnt"` als `ProviderError`-Text,
als `block_reason` in der Anbietersensor-Attribut `gesperrt`, und als
Literal in einer Log-Zeile), wurde nur die tatsächlich nutzersichtbare
Fassung korrigiert und `_fehlerart()` in `capabilities.py` auf dieselbe
Schreibweise abgestimmt; zwei Tests in `test_client.py` sind entsprechend
auf die korrekte Schreibweise umgestellt.

**Der Reserve-Sensor hat seine Diagnose-Kategorie verloren** (`sensor.py`) —
laut README „der Sensor, auf den es ankommt", vorher aber auf keinem
automatisch erzeugten Dashboard sichtbar.

**Blueprints melden einen Fehlschlag** statt lautlos nichts zu tun. Neues
Eingabefeld „Fehler melden" (Vorgabe an) in beiden Blueprints; die
`continue_on_error: true`-Vorlage endete vorher einfach, wenn
`ai_task.generate_data` keine Antwort lieferte — der Fall, der beim
Einrichten mit einer Ring-Kamera ohne funktionierendes Standbild live
auffiel (siehe `zugang.md`, Abschnitt Kameras).

**Diagnose-Export** (`diagnostics.py`, neu): `async_get_config_entry_diagnostics`
liest `configured_providers()`, `runtime.diagnostics()`, `ledger.snapshot()`
und `feed.diagnostics()` und schwärzt `api_key` über HAs eigenen
`async_redact_data`-Helfer. Lokal nicht testbar — importiert
`homeassistant.components.diagnostics`, das im Dev-Umfeld dieses Projekts
bewusst nicht installiert ist (siehe `__init__.py`-Docstring); Verifikation
folgt live, zusammen mit dem angekündigten ausgiebigen Test der ganzen
Einrichtung.

**README:** HACS als Installationsweg (Abschnitt „Installation") mit
Begründung, warum als benutzerdefiniertes statt Standard-Repository; neuer
Abschnitt „Anschließen" (Assist verbinden, KI-Aufgaben-Vorgabe, beide
Blueprints, jeweils mit My-Home-Assistant-Deep-Link); Entities-Karten-Beispiel
im Abschnitt „Sensoren"; neuer Abschnitt „Fehlersuche" (Diagnose-Export, die
vier Fehlerarten).

**HACS-Anbindung:** `hacs.json` neu (Minimum-HA-Version `2025.6.0` — recherchiert
über die Config-Subentry-Änderungen vom März 2025, mit Sicherheitsabstand
nach oben, da HA selbst kein exaktes Versionsdatum für die Subentry-Einführung
nennt), Manifest-Version auf `0.2.0`, neuer CI-Job `hacs` mit dem offiziellen
`hacs/action`. Bewusst **kein** GitHub-Release/Tag gesetzt — HACS kann ein
benutzerdefiniertes Repository auch ohne Releases vom Standard-Branch
installieren, und ein erster Release ist eine Entscheidung des Autors über
Changelog und Zeitpunkt, keine, die sich nebenbei mit erledigen lässt.

**Der erste CI-Lauf des `hacs`-Jobs war rot** — genau die drei Punkte, die
sich lokal nicht prüfen ließen: `brands` (kein Eintrag im
`home-assistant/brands`-Repo), `topics` (das GitHub-Repo hatte keine
Themen), `license` (keine `LICENSE`-Datei). Behoben:

- `brands` steht jetzt in `ignore:` des CI-Jobs — die Aufnahme dort ist ein
  eigener PR in einem fremden Repo und laut HACS-Doku nur für den
  Standardkatalog Pflicht, den dieses Projekt bewusst nicht will.
- `topics` sind gesetzt (`gh repo edit --add-topic`): `home-assistant`,
  `hacs-integration`, `home-assistant-integration`, `ai`,
  `conversation-agent`.
- `license` ließ sich nicht ignorieren (steht nicht auf der Ignore-Liste der
  Aktion) und war ohnehin eine echte Lücke — eine `LICENSE`-Datei (MIT) ist
  jetzt da. MIT ist die in der HA-Custom-Component-Szene übliche Wahl und
  passt zum geringen Anspruch des Projekts; wer eine andere Lizenz will,
  tauscht die Datei aus.

Zweiter CI-Lauf steht noch aus.

**Bewusst nicht angefasst:** die eigentliche Einrichtung — der Autor testet
sie in der fertigen Fassung selbst, ausgiebig. Englische Übersetzung bleibt
in der Minimalvariante (siehe `test_englische_uebersetzung_ist_identisch_zur_deutschen`):
`en.json` ist jetzt eine exakte Kopie von `de.json`, statt der vorher
zufälligen Mischung aus übersetzten und liegengebliebenen Zeilen. Eine echte
englische Fassung wäre die nächste, deutlich größere Baustelle — rund 60
Strings plus die Onboarding-Felder aller vier Anbieterdateien.

240 Tests vorher, 246 danach (neue Tests fürs Schlüssel-Fehlerarten-Mapping in
`test_capabilities.py`, die Entity-Namensregel, die en=de-Identität und den
verbannten Konfigurieren-Verweis in `test_texte.py`). `ruff check` und
`tools/validate_registry.py` sauber.

### Zwei echte Bugs aus dem ersten Live-Durchgang (17.09.2026)

Der Autor hat nach der HACS-Installation den Assistenten wirklich als
Einsteiger durchgespielt — genau der Test, der in Abschnitt 2 seit Phase 2
offensteht. Zwei echte Fehler dabei gefunden, beide vorher nicht sichtbar,
weil dieser Teil des Flows lokal nicht testbar ist (`config_flow.py`
importiert `homeassistant`, siehe `__init__.py`-Docstring):

1. **Menü-Titel mit Platzhaltern bleiben im Frontend leer.** Der
   Ergebnis-Schritt (Menü: weiterer Anbieter / fertig) zeigte bei jedem der
   vier Anbieter `[formatjs Error: MISSING_VALUE]` statt eines Titels. Im
   Frontend-Bundle nachgelesen (`hass_frontend`, Chunk mit `menu_options`):

   ```js
   renderMenuHeader:        (e,o) => e.localize(`...${step_id}.title`)
   renderShowFormStepHeader:(e,o) => e.localize(`...${step_id}.title`, o.description_placeholders)
   ```

   `renderMenuHeader` reicht `description_placeholders` gar nicht an
   `localize()` weiter — ein `{name}` im Titel eines Menü-Schritts bleibt
   damit strukturell fuer immer leer, unabhängig vom Python-Code. Der
   gleichnamige `result`-Schritt im Subentry-Flow ist dagegen ein echtes
   `async_show_form` und war nie betroffen. Fix: Titel des Menü-Schritts
   fest auf „Messergebnis", die dynamische Zeile wandert in die
   Beschreibung (dort funktionieren Platzhalter nachweislich — der
   `{report}`-Teil hat schon immer richtig gerendert). Test
   `test_menue_titel_haben_keine_platzhalter`, klassengetrennt über die
   bestehende `_BLOECKE`-Zuordnung, damit der zu Recht platzhalterhaltige
   Formular-Titel im Subentry-Flow nicht fälschlich angemeckert wird.

2. **Der schwerere Fund:** vier Anbieter im Assistenten eingerichtet, aber
   kein Config Entry entstanden — nichts unter Geräte, `ai_task`-Aufruf
   scheitert mit „entity not found". Ursache: der Übersichts-Schritt
   (`async_step_summary`) war ein Formular ganz ohne Felder, nur Text und
   ein Knopf mit dem generischen HA-Standardtext. "Fertig, Übersicht
   anzeigen" im Menü davor sah bereits wie die Bestätigung aus — der
   zweite, unauffällige Knopf danach wurde übersehen, der Dialog vermutlich
   geschlossen, ohne dass je `async_create_entry` lief.

   Erst mit einer eigenen Beschriftung „Einrichtung abschließen" behoben,
   dann noch einmal grundsätzlicher: der ganze zweite Bestätigungsschritt
   ist unnötig. `async_step_summary` legt den Eintrag jetzt **sofort** an,
   sobald „Fertig" gewählt wird — kein eigenes Formular mehr, keine zweite
   Gelegenheit, den Dialog versehentlich zu schließen. Die Übersicht
   (welches Profil wird bedient, wo bleibt eine Lücke, die
   „Weiter geht's"-Links zu Assist und den Blueprints) steht jetzt im Text
   des Abschluss-Bildschirms, den Home Assistant nach jedem
   `async_create_entry` ohnehin selbst zeigt (`config.create_entry.default`
   statt `config.step.summary`). Test
   `test_die_uebersicht_erstellt_sofort_ohne_eigenen_bestaetigungsschritt`
   haelt fest, dass „summary" nie wieder ein eigener sichtbarer Schritt
   wird, dazu `test_create_entry_platzhalter_werden_gefuellt` fuer die
   `{overview}`-Platzhalter im neuen Block — dieselbe Art Test, die den
   ersten Fehler oben schon haette verhindern koennen, hier von Anfang an
   mitgebaut.

Beide Funde stammen aus demselben Mechanismus: eine Fehlerklasse, die nur
im echten Frontend sichtbar wird, weil sie dort rendert, nicht im
Python-Code. Genau der Grund, warum der Einsteiger-Test in Abschnitt 2 als
eigenes Akzeptanzkriterium steht und nicht durch mehr Unit-Tests ersetzt
werden kann.

249 Tests, `ruff check` sauber.

### Dritter Fund: fehlendes Requirement, live erst nach der ersten echten HACS-Installation sichtbar (18.09.2026)

Nach dem HACS-Update auf die Fixes von oben scheiterte das Setup komplett:

```
ModuleNotFoundError: No module named 'voluptuous_openapi'
  File ".../ai_task.py", line 24, in <module>
    from .task_adapter import (...)
  File ".../task_adapter.py", line 24, in <module>
    from voluptuous_openapi import convert
```

`voluptuous_openapi` wird in `conversation.py` und `task_adapter.py`
unversion importiert (kein try/except), beide Module liegen im
Pflicht-Ladepfad (`ai_task`, `conversation` — beide immer in `PLATFORMS`).
Home Assistant installiert fuer eine Custom Component aber nur, was ihr
eigenes `manifest.json` unter `requirements` nennt — nicht, was zufaellig
schon auf dem System steht, weil eine andere Integration dasselbe Paket
mitgebracht hat. Genau das ist die ganze Zeit passiert: jeder fruehere
Testlauf lief auf einem System, das `voluptuous_openapi` schon aus anderem
Grund installiert hatte, und deshalb fiel das fehlende Requirement nie auf
— bis zur ersten Installation ueber HACS auf frischem Boden.

Fix: `manifest.json` bekommt `voluptuous-openapi>=0.0.5` (dieselbe Version
wie in `requirements-dev.txt`).

**Zwei andere Fremdimporte extra gegengeprueft, um dieselbe Fehlerklasse
nicht zweimal zu uebersehen** — beide stellten sich als bereits richtig
behandelt heraus:

- `PIL` (Pillow) in `imaging.py` — steht in einem try/except, faellt ohne
  Pillow auf unveraendertes Durchreichen des Bildes zurueck. Kommentar im
  Code sagt schon "in HA immer vorhanden".
- `cryptography` in `feed.py` (Ed25519-Signaturpruefung) — ebenso per
  try/except abgesichert, und der Codepfad laeuft ohnehin nur, wenn der
  Feed eingeschaltet ist (in dieser Fassung nicht der Fall).

Beide sind echte HA-Core-Abhaengigkeiten (Home Assistant selbst braucht
Pillow fuer Kamerabilder und cryptography fuer TLS/Tokens), deshalb
absichtlich nicht im eigenen `manifest.json` dupliziert — dieselbe
Unterscheidung, die der neue Test unten trifft.

**Neuer Test, `tests/test_manifest.py`:** prueft jeden Fremdimport im
ganzen Paket per AST. Ungeschuetzt (kein try/except) und ohne passendes
`requirements`-Feld — durchfaellt. Gegenprobe gefahren: mit dem
`voluptuous-openapi`-Eintrag testweise entfernt, findet der Test beide
betroffenen Dateien exakt. `PIL`, `cryptography`, `aiohttp` und
`voluptuous` stehen auf einer expliziten Ausnahmeliste mit Begruendung im
Testdocstring.

250 Tests, `ruff check` sauber.

---

## Kleinkram, notiert damit er nicht verlorengeht

- `.env` ist gitignoriert und war nie im Repo; die Historie ist vor der
  Veröffentlichung auf Schlüsselmuster durchsucht worden und sauber.
- Der `anthropic`-Adapter ist implementiert, aber von keinem Anbieter benutzt
  und daher ungetestet gegen einen echten Endpunkt.
- Die Auslegung „kein Kanal übrig → `HomeAssistantError`" statt stillem
  Verwerfen ist bewusst so gewählt (siehe README, Abschnitt „Was bei Limit oder
  Ausfall passiert"). Falls das anders gemeint war: kleine Änderung in
  `ai_task.py`.

# Der Feed-Dienst

Anbieter ändern Modelle und Limits schneller, als eine Integration Releases
macht. Der Feed ist die Antwort darauf: ein Prober vermisst die Anbieter
regelmäßig mit einem **eigenen** Konto und legt das Ergebnis als signierte
statische Datei ab. Die Integration holt sie, prüft die Signatur und legt sie
über die mitgelieferten Anbieterdateien.

> **Auslieferungszustand: aus.** In `feed.py` stehen `FEED_URL` und
> `FEED_PUBLIC_KEY_B64` leer. Ein Build ohne beides holt nichts, fragt nichts
> und meldet nichts — der Feed ist dann kein abgeschaltetes Feature, sondern
> gar nicht vorhanden. Das ist Absicht: solange kein Schlüssel existiert, gibt
> es auch nichts zu vertrauen.

Der Feed ist eine **Verbesserung, keine Voraussetzung**. Fällt er aus, läuft
alles mit den mitgelieferten Dateien weiter.

---

## Was der Feed darf — und was nicht

| | |
|---|---|
| **darf** | Modelle hinzufügen und entfernen, Limits korrigieren, Fähigkeiten überschreiben, Texte und Onboarding-Schritte ändern, ganze Anbieter nachliefern |
| **darf nicht** | bestimmen, **wohin Daten fließen** |

Die zweite Zeile ist die eigentliche Zusage. Jeder Anbieter aus dem Feed läuft
durch dieselbe `Provider.parse` wie eine mitgelieferte YAML-Datei — und damit
durch die **fest eincompilierte Host-Allowlist** in `allowlist.py`. Ein
übernommener Feed-Dienst könnte Nutzer in ein Limit laufen lassen oder ihnen
ein Modell wegnehmen. Er könnte keine Kamerabilder umleiten.

Der Test dazu heißt `test_der_feed_darf_den_endpunkt_nicht_umbiegen` und
arbeitet mit einer *gültigen* Signatur: geprüft wird, dass die Allowlist auch
dahinter noch hält.

## Vier Linien, von außen nach innen

1. **Signatur.** Ed25519 über die rohen Bytes der heruntergeladenen Datei.
   `parse_feed` nimmt Bytes und Signatur zusammen entgegen; einen Weg, an den
   Inhalt zu kommen, ohne vorher zu prüfen, gibt es nicht. Signiert wird die
   Datei *wie sie ausgeliefert wird* — keine Kanonisierung, und damit auch
   nicht die Fehlerklasse „signiert wurde etwas anderes als gelesen".
2. **Frische.** Der Client rechnet das Alter aus `generated_at` gegen seine
   eigene Grenze (`MAX_AGE`, 14 Tage). Ein vom Feed mitgeliefertes
   Ablaufdatum wird bewusst **nicht** ausgewertet — ein stehengebliebener
   Dienst könnte sich sonst selbst für gültig erklären.
3. **Kein Rückschritt.** Ein Dokument, das älter ist als das zuletzt gesehene,
   wird abgelehnt. Sonst ließe sich ein altes, korrekt signiertes Dokument
   erneut einspielen.
4. **Die Allowlist.** Siehe oben.

Dazu zwei Kleinigkeiten, die eine Abrufschleife gegen einen fremden Server
braucht: eine Größengrenze (`MAX_FEED_BYTES`, 1 MB) und bedingte Abrufe über
`ETag` und `Last-Modified`. Ein unveränderter Feed kostet damit eine
304-Antwort und keine Übertragung.

## Struktur, keine Kontoangaben

Eine Messung darf ausschließlich die Felder aus `feed.MEASUREMENT_FIELDS`
führen — eine Whitelist, keine Verbotsliste. Was der Prober sonst noch weiß,
bleibt in seiner Zustandsdatei: Fehlertexte der Anbieter etwa können durchaus
Kontoangaben enthalten. Der Test dazu heißt
`test_fehlertext_bleibt_im_zustand_und_nicht_im_feed`.

Das Konto des Probers ist ein eigenes. Nicht aus Ordnungsliebe: die
Restkontingente, die er beobachtet, sind seine eigenen und sagen über die des
Nutzers nichts.

---

## Das Dokument

Eine Datei, `/v1/providers.json`, daneben `/v1/providers.json.sig` mit der
Signatur als eine Zeile Base64.

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-11T07:55:07.104775+00:00",
  "providers": [ ... ],
  "measurements": {
    "groq/qwen/qwen3.8-27b": {
      "alive": true,
      "consecutive_failures": 0,
      "capabilities": { "vision": true },
      "limits": { "rpm": 1000, "tpm": 8000 },
      "latency_total_s": 0.13,
      "ttft_s": null,
      "checked_at": "2026-09-11T07:55:07.104775+00:00"
    }
  },
  "notes_de": ""
}
```

**Definition und Beobachtung sind getrennt.** `providers` hat exakt die Form
der Registry-Dateien — deshalb braucht sie keinen eigenen Parser und erbt die
gesamte strenge Prüfung. `measurements` liegt daneben und kann damit keine
Felder in die Definition einschleusen.

Beim Anwenden (`apply_feed`):

* Ein Anbieter aus dem Feed **ersetzt** den gleichnamigen mitgelieferten.
* Ein mitgelieferter Anbieter, den der Feed nicht nennt, **bleibt stehen**.
  Löschen durch Weglassen gibt es nicht: ein halb übertragenes Dokument würde
  sonst stillschweigend Kanäle abschalten.
* Messungen überschreiben Fähigkeiten und Limits beider Quellen.
* Ein Modell fällt erst heraus, wenn es `DEAD_AFTER_FAILURES` Läufe
  hintereinander nicht geantwortet hat.

Dazu `/v1/index.json` — unsigniert und nur zum Nachsehen, mit der Liste der
Historie unter `/v1/history/`. Ein Client liest ausschließlich
`providers.json` samt Signatur. Was nicht geprüft wird, soll auch nicht so
aussehen, als würde es geprüft.

---

## Den Prober betreiben

### 1. Schlüsselpaar

```bash
python tools/feed_keys.py neu --out feed-private.pem
```

Gibt den öffentlichen Teil als eine Zeile aus. Der private Teil gehört nicht
ins Repo (`.gitignore` kennt ihn). **Verloren heißt: neuer Schlüssel, neues
Integrations-Release** — alte Clients nehmen den Feed sonst nicht mehr an.

### 2. Secrets setzen

Im Repository unter *Settings → Secrets and variables → Actions*:

| Secret | Inhalt |
|---|---|
| `FAR_FEED_PRIVATE_KEY` | der **Inhalt** von `feed-private.pem`, nicht der Pfad |
| `FAR_KEY_GOOGLE_AI_STUDIO` | Schlüssel des **Proberkontos**, nicht der eigene |
| `FAR_KEY_GROQ` | dito |
| `FAR_KEY_MISTRAL` | dito |
| `FAR_KEY_OPENROUTER` | dito |

Ein Anbieter ohne Schlüssel wird übersprungen. Das ist kein Fehler und
**kein Ausfall** — seine Modelle bleiben im Dokument unverändert stehen.

### 3. Takte

`.github/workflows/prober.yml` kennt zwei — **der Zeitplan ist zunächst
auskommentiert.** Solange kein Proberkonto und kein Signierschlüssel
eingerichtet sind, wäre ein stündlicher Lauf eine stündlich rote Meldung.
Zum Einschalten die beiden `cron`-Zeilen wieder aktivieren:

```
stündlich   --cheap   ein Request je Modell: lebt es, welche Header kommen
täglich     --full    Bild, Schema, Werkzeuge — die teuren Prüfungen
```

**Der Takt ist nur die Obergrenze.** Je Modell rechnet der Prober zusätzlich
aus dem bekannten Tageskontingent (`limits.rpd`) aus, wie oft er es sich
leisten kann, und beide Takte zusammen bleiben bei `BUDGET_SHARE` — einem
Viertel. Sonst wäre er selbst der größte Verbraucher des Kontingents, das er
vermisst:

| Modell | RPD | sparsam | voll | Requests/Tag |
|---|---:|---|---|---|
| `gemini-3.5-flash-lite` | 500 | stündlich | täglich | 29 (6 %) |
| `gemini-3.5-flash` | **20** | alle 9,6 h | alle 48 h | 5 (25 %) |
| `groq/qwen3.8-27b` | 1.000 | stündlich | täglich | 29 (3 %) |
| `nemotron-3.5-lightning:free` | 50 | alle 3,8 h | täglich | 11 (22 %) |
| `mistral/ministral-3b` | — | stündlich | täglich | 29 |

Die Zeile mit den 20 Anfragen ist der Grund für die Rechnerei: Googles große
Flash-Modelle wären von einem stündlichen Lebenszeichen allein erschöpft, bevor
die Installation, die den Feed liest, eine einzige Anfrage stellt. Ein Modell
ohne veröffentlichtes Tageslimit wird nicht gedrosselt — raten hilft da nicht.

Von Hand:

```bash
python tools/prober.py --cheap --key feed-private.pem
python tools/prober.py --full --dry-run --key feed-private.pem
```

### 4. Die drei Notbremsen

Sie sind der eigentliche Inhalt von `tools/prober.py`; das Messen selbst steht
in `capabilities.py`.

| Lage | Was passiert |
|---|---|
| **Kein einziges Modell antwortet** | Der Lauf wird verworfen (Rückgabewert 3). Nichts wird geschrieben, nichts als Ausfall gezählt. Das ist fast immer das Netz des Probers und nicht das Ende aller Anbieter gleichzeitig. |
| **Schlüssel abgelehnt** (401/402/403) | Nicht gewertet, laut gemeldet, Rückgabewert 4 — der Feed wird trotzdem veröffentlicht. Ein abgelaufener Proberschlüssel würde sonst allen Nutzern funktionierende Kanäle als tot melden. |
| **Ratenlimit** (429) | Nicht gewertet. Der Endpunkt hat geantwortet; über das Modell sagt das nichts. |

Die letzte Zeile stammt aus dem ersten Livelauf am 11.09.2026: Mistral
antwortete mit 429, und drei gedrosselte Läufe hintereinander hätten gereicht,
um ein gesundes Modell totzumelden.

### 5. Ausliefern

Der Workflow legt `docs/` und `feed-state.json` auf den Zweig `feed`.
Eingestellt wird das unter *Settings → Pages*: Quelle **Deploy from a branch**,
Zweig `feed`, Ordner **`/docs`**.

Der Ordner ist nicht frei wählbar — Pages kennt bei der Auslieferung aus einem
Zweig nur die Wurzel und `/docs`. Genau deshalb liegt die Zustandsdatei eine
Ebene darüber: sie gehört zur Buchführung und nicht zur Auslieferung. Im
öffentlichen Repo ist sie trotzdem lesbar, und darum hält sie von einem
Fehlschlag nur den Statuscode fest, nicht den Antworttext.

Pages liefert `ETag` und `Last-Modified` von selbst; mehr braucht der Client
nicht. Ein eigener Webspace per `rsync` täte es genauso.

**Die Adresse muss dauerhaft dieselbe bleiben** — sie steht danach im
Quelltext jeder ausgelieferten Fassung. Die `github.io`-Adresse hängt am
Kontonamen; eine eigene Domain davor macht einen späteren Umzug zu einer
DNS-Änderung statt zu einem Release.

### 6. Einschalten

Zwei Zeilen in `custom_components/free_ai_router/feed.py`:

```python
FEED_PUBLIC_KEY_B64 = "qegjySfhs9BV9T+ngghPIIOiCX2SR+o6twOHCbiLufc="
FEED_URL = "https://marcusstockhaus.github.io/ha-free-ai-router/v1/providers.json"
```

`FEED_URL` steht bereits; es fehlt nur der Schlüssel. Solange einer von beiden
leer ist, holt die Integration nichts.

Beides im Quelltext und nicht in einer Einstellung — wer die Quelle wählen
darf, wählt auch den Schlüssel. Eine Allowlist wie für die Anbieter-Endpunkte
braucht es hier deshalb nicht: diese Adresse kann gar nicht aus einer
Datendatei kommen.

Danach holt die Integration den Feed alle `FEED_INTERVAL_HOURS` Stunden im
Hintergrund. Beim Start wird nur der Zwischenspeicher übernommen, nie ein
Abruf abgewartet.

---

## Wenn etwas schiefgeht

| Lage | Folge |
|---|---|
| Feed nicht erreichbar | Nichts. Es gilt weiter, was im Zwischenspeicher liegt, sonst die mitgelieferte Registry. |
| Signatur passt nicht | Abgelehnt, der bisherige Stand bleibt unberührt, eine Warnung im Log. |
| Dokument älter als der bekannte Stand | Abgelehnt. |
| Dokument älter als 14 Tage | Abgelehnt, auch aus dem Zwischenspeicher — der wird dann geleert. |
| Unbekannte `schema_version` | Abgelehnt. Lieber ohne Feed laufen als halb verstandene Daten übernehmen. |
| Anbieter mit fremdem Host | Das ganze Dokument wird abgelehnt. |
| `cryptography` fehlt | Kein Feed. In Home Assistant ist die Bibliothek immer vorhanden. |

Nichts davon hält den Start auf, und nichts davon schaltet einen Kanal ab.

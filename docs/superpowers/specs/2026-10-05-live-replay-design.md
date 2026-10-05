# Live-Karte als Replay der laufenden Runde – Design

- **Datum:** 2026-10-05
- **Branch:** `live-moderation`, nach dem Merge von `master` (53fa720). Geht später nach `master`.
- **Baut auf:** `2026-09-29-live-moderation-design.md`. Ersetzt dort Abschnitt 8 (Live-Stream per SSE) vollständig und die SSE-Teile der Abschnitte 3, 9, 10, 13 und 14. Login, Sitzungen, Bremse und SIGHUP-Widerruf bleiben, mit den Änderungen aus Abschnitt 7 und 8 unten.
- **Status:** zur Durchsicht

## 1. Ziel

Die Live-Karte soll sich bedienen wie ein YouTube-Livestream: Man schaut live, springt jederzeit an jeden früheren Punkt der laufenden Runde und mit einem Klick zurück zu „LIVE“. Dazu wird die Live-Ansicht ein reguläres Replay der laufenden Runde, das mitwächst. Der eigene SSE-Live-Stream entfällt.

Außerdem:
- ein Button für den Moderator-Login auf der Startseite statt nur `?mode=live`;
- das Passwort optional als scrypt-Hash in der Env (`SQREADER_LIVE_PASSWORD_HASH`) oder in der Config;
- keine Mindestlänge mehr für das Passwort;
- die Funde aus dem Ponytail-Review des Branches.

## 2. Anforderungen

### Muss

1. Die Live-Karte ist der vorhandene Replay-Player auf der laufenden Runde: Timeline vom Rundenstart bis jetzt, Seek per Klick, ±10 s, Einzelbild, Geschwindigkeiten, Kill-Feed, Marker, Ticket-Analyse.
2. Live läuft der Abspielkopf `LIVE_DELAY_MS` = 8 s hinter dem neuesten Frame. Ein „● LIVE“-Knopf zeigt, ob man live ist, und springt sonst zurück an den Live-Rand.
3. Wer mit mehr als 1× aufholt, landet am Live-Rand automatisch bei 1×, ohne Standbild und ohne Rücksprung.
4. Am Rundenende wechselt die Ansicht zur nächsten Runde, sobald die Wiedergabe das Ende erreicht hat. Bis dahin steht „Warte auf die nächste Runde…“.
5. Die Daten der laufenden Runde gibt es nur mit gültiger Sitzung. Die öffentlichen Gates (`/api/recording/*` liefert nur finalisierte Aufnahmen, die Stats-API verbirgt die laufende Runde) bleiben unverändert, auch für eingeloggte Moderatoren.
6. Ohne Live-Karte (kein gültiges Passwort) sind die Server-Antworten wie heute byte-gleich zum öffentlichen Build. `tests/test_public_no_live.py` bleibt unverändert grün.
7. Widerruf (Logout, SIGHUP, Ablauf nach 12 h) beendet laufende Runden-Streams spätestens nach `POLL_SEC` = 250 ms.
8. Nur Standardbibliothek in Python, nur vorhandene npm-Pakete.

### Nicht im Umfang

- Ein Live-Bild zwischen zwei Runden (Scoreboard danach, Kartenwechsel). Der Recorder zeichnet nur `InProgress` auf, die Staging-Phase gehört dazu.
- Eine Live-Karte ohne `--recordings-dir`.
- Zurückspulen in frühere Runden. Dafür gibt es die normalen Replays, sobald die Runde finalisiert ist.
- Ein Live-Bild mit weniger als 8 s Verzögerung.

### Erfolgskriterien

- Ein Moderator öffnet die Karte, sieht nach höchstens 2 s die laufende Runde und kann auf der Timeline bis zum Rundenstart springen.
- „● LIVE“ führt aus jeder Position zurück an den Live-Rand. Aufholen mit 8× endet ohne Standbild bei 1× live.
- Ein Logout in einem zweiten Tab beendet den Stream des ersten, und dort erscheint der Login.
- Alle Python- und Frontend-Tests sind grün. Ausgenommen sind die 4 bekannten Fehler in `tests/test_custom_capzones.py`, die schon auf `master` rot sind.

## 3. Architektur

### Datenfluss

```
Reader-Tick ──► recorder._handle_snap / write_position_frame ──► <round>.sqrx  (flush pro Frame)
                                                                    │
GET /api/live/round              ◄── record_state_box["current"] ───┤  (Pfad, Start, neuester Frame)
GET /api/live/round/<id>?from=ms ◄── SqrxReader + Tail-Wrapper ─────┘  (ab from, dann folgen)
        │ NDJSON (gzip)
        ▼
fetchRecordingFrames ─► useReplayLoader ─► replay.frames ─► useReplayPlayback ─► MapCanvas
                                                   ▲
                                       useLiveEdge (LiveAccess.tsx): Live-Rand, LIVE-Knopf,
                                       Nachpuffern, Rundenwechsel, Reconnect
```

Die Datei ist die einzige Quelle. Der Reader schreibt in die `.sqrx` genau die Zeilen, die er bisher auch an den Hub gab (`cli.py`: dieselbe `line` an `live.publish` und `_rec_step`).

### Was entfällt

- **`sqreader/live.py`:** `Hub`, `pick`, `LiveMap.publish`, `LiveMap._stream`, die Route `/api/live/stream`, die Konstanten `RING_SIZE`, `KEEPALIVE_SEC` und `RETRY_MS`, alle `hub.kick()`.
- **`sqreader/cli.py`:** die beiden `live.publish(...)`-Hooks.
- **`frontend/src/live/client.ts`:** `createLiveFeed`, `LiveFeed`.
- **`frontend/src/live/LiveAccess.tsx`:** `useLiveStream`, `EMPTY_FRAMES`.
- **Tests:** `tests/test_live_hub.py`, `tests/test_live_stream.py` und die Decoder-Tests in `client.test.mts`.

`/api/live/stream` antwortet danach 404 wie jeder unbekannte Pfad unter `/api/live/`.

## 4. Server: Runden-Endpunkte (`sqreader/live.py`)

### Zugriff auf die laufende Runde

`LiveMap.recording` ist ein Callable, das den aktuellen `RecordingState` liefert oder `None`. Voreinstellung ist `lambda: None`. `cli.py` setzt es direkt nach dem Anlegen von `record_state_box`:

```python
if live is not None:
    live.recording = lambda: record_state_box["current"]
```

Ist `live_password` gültig, aber `recordings_dir` fehlt, gibt es keine Live-Karte. Dann steht im Log `live map disabled: it needs --recordings-dir`, und `live` bleibt `None`.

### HTTP

Jeder Pfad unter `/api/live/round` prüft zuerst die Sitzung und antwortet ohne gültige Sitzung mit 401 `{"error": "not logged in"}`. Ein Fremder erfährt also nicht, ob gerade eine Runde läuft. Alle Antworten tragen `Cache-Control: no-store` und keine CORS-Header.

| Pfad | Antwort mit Sitzung |
|---|---|
| `GET /api/live/round` | 200 Meta der laufenden Runde, oder 404 `{"error": "no round in progress"}` |
| `GET /api/live/round/<id>/meta` | 200 Meta, wenn `<id>` die laufende Runde ist, sonst 404 |
| `GET /api/live/round/<id>?from=<ms>` | Stream, wenn `<id>` die laufende Runde ist, sonst 404. 503 `{"error": "too many live viewers"}` mit `Retry-After: 30` bei `MAX_STREAMS` = 10 offenen Streams |
| alles andere unter `/api/live/round` | 404 |

**`<id>`:** Das ist der Dateiname der Aufnahme ohne `.sqrx` (`state.path.stem`). Der Server vergleicht ihn nach `urllib.parse.unquote` nur als String mit der laufenden Runde und öffnet nie eine Datei anhand der Eingabe. Die ID verhindert, dass bei einem Rundenwechsel zwischen Meta-Abfrage und Stream die falsche Runde unter der falschen Achse landet.

**Meta:**

```json
{"id": "<stem>", "startedAtUtc": "<ISO>", "latestUtc": "<ISO>", "durationSec": 0}
```

- `startedAtUtc` ist `first_snap_ts` und fällt auf `started_at.isoformat()` zurück.
- `latestUtc` ist `last_snap_ts` (der neueste volle Frame) und fällt auf `startedAtUtc` zurück.
- `durationSec` ist 0, damit die Timeline-Achse mit den Frames wächst. So liest es `fetchReplayTiming` schon heute.

### Der Stream

1. `from` kommt aus der Query (Ganzzahl, Voreinstellung 0, Unsinn wird zu 0). Ein Platz aus `threading.BoundedSemaphore(MAX_STREAMS)` wird ohne Warten belegt, sonst gibt es 503. Die Ablehnung wird über das vorhandene `_log_refused` höchstens einmal pro Minute geloggt.
2. Die Kodierung wählt `h._negotiate_encoding(allow_zstd=False)`: gzip oder identity. Bei gzip folgt nach jeder Zeile `flush(zlib.Z_SYNC_FLUSH)`, damit jeder Frame sofort beim Browser ist. Kein zstd-Passthrough, weil die Datei noch wächst.
3. Kopfzeilen: 200, `Content-Type: application/x-ndjson`, `Cache-Control: no-store`, `X-Accel-Buffering: no`, `Connection: close`, ggf. `Content-Encoding: gzip`. Die Antwort ist HTTP/1.0 und endet mit dem Schließen der Verbindung, wie beim bisherigen SSE-Stream.
4. Die Quelle ist `SqrxReader(state.path)`. Dessen Datei-Objekt ersetzt ein Tail-Wrapper:
   ```python
   def read(self, n=-1):
       while True:
           data = self._f.read(n)
           if data:
               return data
           if not self._alive():           # setzt self.reason
               return self._f.read(n)      # letzter Frame vor dem Schließen
           time.sleep(POLL_SEC)
   ```
   - `_alive()` ist wahr, solange `access.valid(token)` gilt und `live.recording() is state`.
   - Ein halb geschriebener zstd-Frame am Dateiende wird einfach zu Ende gelesen, weil `read` erst zurückkehrt, wenn wieder Bytes da sind.
   - Ist `from` > 0, laufen die Zeilen durch das vorhandene `httpsrv._replay_from(lines, from_ms)`.
5. Jede Zeile wird mit `\n` geschrieben, vorher prüft der Stream `access.valid(token)`. Damit greift ein Widerruf auch mitten in einem großen Rückstand, nicht erst am Dateiende. `h.connection.settimeout(WRITE_TIMEOUT_SEC)` mit 20 s.
6. Endgründe für das Log:
   - `round ended`: Die Aufnahme ist nicht mehr die laufende.
   - `session ended`: Logout, SIGHUP oder Ablauf.
   - `client gone`: `OSError` beim Schreiben.
   - `write timeout`
   - `bad data`: `zstd.ZstdError` beim Lesen.

   Danach wird der Semaphor-Platz in `finally` freigegeben.
7. Log:
   - `live: round stream opened from <ip> (<n>/10)`
   - `live: round stream closed from <ip> after <m>m<ss>s (<reason>)`
   - `<n>` ist die Zahl der offenen Streams.

### Konstanten

| Name | Wert | Zweck |
|---|---|---|
| `POLL_SEC` | 0.25 | Nachsehen am Dateiende, zugleich Höchstdauer bis zum Widerruf |
| `MAX_STREAMS` | 10 | gleichzeitige Runden-Streams (bleibt) |
| `WRITE_TIMEOUT_SEC` | 20.0 | bleibt |
| `REFUSED_LOG_INTERVAL_SEC` | 60.0 | bleibt (für `_log_refused`) |

## 5. Client: Live-Player

### Kennung und URLs (`frontend/src/api/recordings.ts`)

- Eine Live-Aufnahme hat im Store die ID `LIVE_ID_PREFIX + <stem>` mit `LIVE_ID_PREFIX = "@live:"`. Eine echte Aufnahme kann nie so heißen.
- `recordingUrl(id)` liefert `./api/live/round/<stem>` für Live-IDs und sonst `./api/recording/<id>`, jeweils URL-kodiert.
- `fetchRecordingFrames` und `fetchReplayTiming` (`…/meta`) nutzen nur noch `recordingUrl`. Das ist die einzige Weiche zwischen Live und Replay.
- `LIVE_DELAY_MS = 8000` steht ebenfalls dort, weil `TimelineBar` es braucht.

### `frontend/src/live/client.ts`

`fetchLiveRound()` fragt `./api/live/round` ab:
- 200 → `{ id, startedMs, latestMs }`
- 401 → `"anon"`
- alles andere → `null`

`probeSession`, `login`, `logout` und `retryAfterMinutes` bleiben.

### `goLive()` (`LiveAccess.tsx`)

Eine einzige Funktion für Einstieg, „● LIVE“-Klick und Rundenwechsel:

1. `fetchLiveRound()` aufrufen.
   - Bei `"anon"`: Live verlassen, Login-Dialog mit „Sitzung abgelaufen – bitte neu anmelden.“
   - Bei `null`: Wartezustand mit Banner „Warte auf die nächste Runde…“ und neuer Versuch nach 5 s, solange der Wartezustand gilt.
2. `id = "@live:" + round.id`, `from = round.latestMs − LIVE_DELAY_MS`.
3. Je nach Stand:
   - **Andere oder keine Live-Aufnahme offen:** `@live:<id>` ab `from` öffnen, `playing: true`, Modus `replay`, URL `?mode=live`. Das passiert in einem Store-Update, damit der Loader nicht erst ab Rundenstart lädt.
   - **Dieselbe Runde, Live-Rand geladen** (`bufferedMs ≥ latestMs − 3000`): lokal auf `bufferedMs − LIVE_DELAY_MS` springen, `speed: 1`, `playing: true`. Kein Neuladen.
   - **Dieselbe Runde, weit zurück:** `restartReplayAt(from)` mit `speed: 1` und `playing: true`.

### `useLiveEdge()` (`LiveAccess.tsx`)

Läuft nur, solange `replay.id` eine Live-ID ist. Ein 250-ms-Intervall liest den Store und `replayClock`. `lag = bufferedMs − replayClock.ms`. Die Regeln stecken in einer reinen Funktion `edgeStep(state)`, die eine Store-Änderung oder nichts liefert. So lassen sie sich testen.

| Lage | Aktion |
|---|---|
| `speed > 1` und `lag ≤ LIVE_DELAY_MS` | `speed: 1` (eingeholt, jetzt live) |
| `stalled` und `playing` | `playing: false`, `stalled` bleibt wahr, der Hook merkt sich das Pausieren (Nachpuffern, das vorhandene Banner bleibt sichtbar) |
| `stalled`, nicht `playing`, `lag ≥ LIVE_DELAY_MS`, und der Hook hat selbst pausiert | `playing: true`, `stalled: false`, Anker auf 0 (weiter mit 8 s Vorlauf) |

`atLive` = `playing && lag ≤ LIVE_DELAY_MS + 3000` steuert den Knopf: rot und nicht klickbar, wenn live, sonst grau und klickbar (`goLive()`). Wer pausiert, fällt zurück wie bei YouTube.

**Stream-Ende** (`loading` wird bei einer Live-ID falsch): `fetchLiveRound()` aufrufen.
- `"anon"`: Login wie oben.
- Gleiche Runden-ID, die Verbindung war also weg: nach `RECONNECT_MS` = 3 s `restartReplayAt(replayClock.ms)`. Der Abspielkopf bleibt, wo er war, und die Daten davor werden bei Bedarf neu geladen. Die Pause verhindert eine Schleife, wenn der Server mit 503 ablehnt.
- `null` oder eine andere ID, die Runde ist also vorbei: nichts tun, bis die Wiedergabe am Ende steht (`!playing`, `currentIdx = frameCount − 1`). Dann `goLive()`.

### Weitere Eingriffe im Frontend

- **`TimelineBar.tsx`:** In `seekToMs` wird bei einer Live-ID ein Ziel jenseits von `bufferedMs − LIVE_DELAY_MS` auf genau diesen Wert begrenzt. „+10 s“ oder Ziehen an den rechten Rand heißt dann „live“, ohne die Aufnahme neu zu laden. Eine Zeile.
- **`TopBar.tsx`:**
  - Bei einer Live-ID zeigt die Pille „live“ statt „recording“.
  - Das „← Back“ des Replays ist ausgeblendet.
  - `LiveControls` zeigt „● LIVE“, „← Zurück“ (zur Startseite) und „Abmelden“. Das gilt bei einer Live-ID oder im Wartezustand.
- **„Past Matches“:** Wird darüber eine fertige Aufnahme geöffnet, ist man nicht mehr live. `LiveControls` verschwindet, weil die ID keine Live-ID mehr ist, und der Wartezustand endet.

## 6. Login-Button (`LiveEntry`, Startseite)

| Zugang (`probeSession`) | Button in der Navigation |
|---|---|
| `anon` | „Moderator-Login“, öffnet den vorhandenen Login-Dialog |
| `ok` | „Live-Karte“, ruft `goLive()` auf |
| `off` (404), `unknown` | keiner, öffentliche Seite unverändert |

Nach einem erfolgreichen Login folgt `goLive()`. `?mode=live` funktioniert weiter wie bisher.

## 7. Passwort: Hash, Quellen, keine Mindestlänge

### Regeln für einen Wert

`validate_password(value, *, hash_only=False) -> (secret | None, reason | None)`:

| Wert | Ergebnis |
|---|---|
| `None` oder `""` | nicht gesetzt (kein Grund) |
| kein String | `live_password must be a string` |
| Leerraum am Rand | wie bisher `… has leading or trailing whitespace` |
| beginnt mit `scrypt:`, aber nicht parsebar | `… is not a valid scrypt hash` |
| `hash_only` und kein Hash | `SQREADER_LIVE_PASSWORD_HASH must be a hash from python3 -m sqreader.live hash` |
| sonst | gültig: ein Hash oder ein Klartext beliebiger Länge |

`MIN_PASSWORD_LEN` und `_TOO_SHORT` entfallen. Kein Grund nennt je den Wert.

### Hash-Format

```
scrypt:<n>:<r>:<p>:<salt>:<key>
```

- **Kodierung:** Salt (16 Byte) und Key (32 Byte) sind base64url ohne Padding. Das Format enthält kein `$`, das Compose in einer `.env` ersetzen würde.
- **Erzeugen:** `n=16384`, `r=8`, `p=1`.
- **Parsen:** `n` muss eine Zweierpotenz mit `2 ≤ n ≤ 2**20` sein, außerdem `1 ≤ r ≤ 16` und `1 ≤ p ≤ 4`. So kann ein Tippfehler keine Gigabytes anfordern.
- **Speicher:** `maxmem = 128 * r * (n + p) + 2**20`.

### Prüfung (`Access`)

- **Was `Access` speichert:** den gültigen `secret`-String. `reset()` vergleicht `sha256(secret)` mit dem bisherigen, um „UNCHANGED“ zu erkennen.
- **Bei einem Hash:** `hmac.compare_digest(hashlib.scrypt(given, salt=…, n=…, r=…, p=…, maxmem=…, dklen=32), key)`.
- **Bei Klartext:** wie bisher `compare_digest` der SHA-256-Digests.
- **Kosten:** scrypt läuft unter dem `Access`-Lock und kostet etwa 50 ms. Das serialisiert Logins und begrenzt die CPU-Last zusammen mit der vorhandenen Bremse.

### Quellen und Vorrang

1. `SQREADER_LIVE_PASSWORD_HASH` in der Env, wenn nicht leer. Gültig nur mit `hash_only=True`.
2. Sonst `live_password` aus der Config.

Beim Start meldet das Log die Quelle: `live map enabled for moderators (password from SQREADER_LIVE_PASSWORD_HASH)` bzw. `(password from live_password)`.

### SIGHUP

Der Widerruf aller Sitzungen bleibt. `reload()` löst die Quelle neu auf:

- **Env gesetzt:** Der Wert ist zur Laufzeit derselbe. Das Log meldet `live: SIGHUP: all sessions revoked; password from SQREADER_LIVE_PASSWORD_HASH is UNCHANGED (environment, change it with a restart between rounds)`.
- **Config:** wie bisher (`password reloaded` / `UNCHANGED` / `NO valid live_password … logins disabled until fixed`).

### Hash erzeugen

`python3 -m sqreader.live hash`:
- fragt per `getpass` zweimal nach dem Passwort;
- prüft auf Gleichheit und mit `validate_password`;
- gibt den Hash auf stdout aus;
- endet bei Abweichung oder ungültigem Wert mit Exit-Code 1 und einer Meldung ohne den Wert.

Unter Docker:

```
docker compose run --rm --no-deps --entrypoint python3 sqreader -m sqreader.live hash
```

### Docker

- **`docker-compose.yml`:** `SQREADER_LIVE_PASSWORD_HASH: ${SQREADER_LIVE_PASSWORD_HASH:-}` unter `environment`.
- **`.env.example`:** ein auskommentierter Block mit dem Befehl. Er warnt, dass die Env per `docker inspect` lesbar ist, weshalb dort nur ein Hash stehen darf.

## 8. Aufräumen (Ponytail-Review)

- **Plan löschen:** `docs/superpowers/plans/2026-09-29-live-moderation.md`, er ist abgearbeitet. Der Umsetzungsplan zu dieser Spec wird nach der Umsetzung ebenfalls gelöscht.
- **`config_path()`:** wandert nach `sqreader/config.py`. `config._load` und `live.reload` nutzen dieselbe Funktion. `tests/test_live_reload.py::test_config_path_follows_config_py` entfällt.
- **Sitzungen:** `Access._sessions` wird `dict[str, float]` und speichert nur noch den Ablauf. `_new_session(now)`, und das Aufräumen wird eine Dict-Comprehension.
- **`_fmt_duration`:** entfällt. Das Log schreibt `"%dm%02ds", *divmod(int(elapsed), 60)`.
- **Ohnehin weg mit SSE:** `Hub.close` und `LiveFeed`.

## 9. Eingriffe in bestehende Dateien

| Datei | Änderung |
|---|---|
| `sqreader/cli.py` | Die zwei `publish`-Hooks raus. `live.recording = …` nach `record_state_box`. Live aus ohne `recordings_dir`. |
| `sqreader/config.py` | `config_path()` |
| `sqreader/httpsrv.py` | keine (Routing unter `/api/live/` und `do_POST` bleiben) |
| `frontend/src/api/recordings.ts` | `LIVE_ID_PREFIX`, `LIVE_DELAY_MS`, `recordingUrl` |
| `frontend/src/ui/TimelineBar.tsx` | Begrenzung in `seekToMs` |
| `frontend/src/ui/TopBar.tsx` | Pille, „← Back“ ausblenden |
| `frontend/src/ui/Home.tsx`, `App.tsx` | unverändert (`LiveEntry`, `LiveAccess` sind schon eingehängt) |
| `docker-compose.yml`, `.env.example`, `sqreader.config.example.json` | Env-Variable, Kommentare (ohne 20 Zeichen) |
| `scripts/live_dev_server.py` | schreibt synthetische Frames in eine temporäre `.sqrx` und setzt `live.recording`. `--round-sec N` beendet die Runde nach N s und startet eine neue, um den Rundenwechsel zu prüfen. `--sqrx` spielt eine Aufnahme in die temporäre Datei ab. |

## 10. Fehlerbehandlung im Überblick

| Fall | Verhalten |
|---|---|
| keine Runde beim Einstieg | Wartebanner, alle 5 s neu |
| Runde endet | fertiges Replay bis zum Ende, dann nächste Runde |
| Verbindung bricht ab | Reconnect nach 3 s ab Abspielkopf |
| Sitzung endet | Server beendet den Stream in ≤ 250 ms, Client zeigt den Login |
| 11. Stream | 503, Fehlerkarte des Replays, automatischer neuer Versuch alle 3 s |
| Reader stockt | Player puffert 8 s nach, Banner „Puffern“ |
| beschädigter Frame | Stream endet mit `bad data`, Client verbindet neu ab Abspielkopf |
| Hash ungültig | Live-Karte aus, Log nennt die Regel, nie den Wert |

## 11. Sicherheit im Überblick

- **Gates:** Runden-Daten gibt es nur nach Sitzungsprüfung. 401 kommt vor 404, der Rundenstatus bleibt also verborgen. Die öffentlichen Gates bleiben unberührt (`test_the_recording_and_stats_gates_hold_for_a_logged_in_moderator` bleibt).
- **Pfad-Eingabe:** Kein Dateizugriff aus Benutzereingaben. Der Server liefert immer nur `record_state_box["current"].path`.
- **Widerruf:** Er greift spätestens nach 250 ms, weil jeder Stream vor jeder Zeile und in jedem Leerlauf-Durchgang die Sitzung prüft.
- **Env:** Dort steht nur ein Hash, Klartext wird abgewiesen.
- **Kurze Passwörter:** Online bremst die vorhandene Drosselung (5 pro Adresse, 50 gesamt pro 10 min). Offline gegen einen geleakten Hash bremst nur scrypt. Die Doku empfiehlt ein langes Passwort, verlangt es aber nicht.

## 12. Tests

### Python

**`tests/test_live_round.py`** (neu, ersetzt `test_live_hub.py` und `test_live_stream.py`):
- 401 auf allen drei Pfaden ohne Sitzung, auch wenn keine Runde läuft;
- 404 ohne Runde und bei fremder `<id>`;
- Meta-Felder;
- vorhandene Frames ab Anfang, `from` beginnt am ersten vollen Frame;
- angehängte Frames kommen nach;
- ein in zwei Teilen geschriebener Frame kommt ganz an;
- Ende nach Rundenwechsel inklusive des letzten Frames;
- Ende in ≤ 1 s nach Logout und nach `access.reset`;
- 503 beim 11. Stream;
- gzip ergibt dieselben Zeilen;
- Log-Zeilen ohne Token.

**`tests/test_live_access.py`:** Statt der Längentests:
- ein 1-Zeichen-Passwort gilt;
- Hash-Rundreise und falsches Passwort;
- kaputte Hashes in mehreren Formen werden ohne den Wert abgelehnt;
- zu große Parameter werden abgelehnt;
- `reset` erkennt „unverändert“ bei einem Hash.

**`tests/test_live_reload.py`:**
- Env-Vorrang;
- Klartext in der Env wird abgewiesen;
- SIGHUP mit Env → `UNCHANGED (environment)`;
- die Merge-Guard-Pins auf die neuen Hooks (`live.recording = lambda: record_state_box["current"]`, der SIGHUP-Hook, `live_from_config`, die Prüfung auf `recordings_dir`);
- `test_reload_ends_open_streams` ersetzt den alten Test gegen den Runden-Stream.

**`tests/test_live_http.py`:** `/api/live/stream` ist jetzt 404, Live ohne `recordings_dir` ist aus.

**Hash-Befehl:** `main(["hash"])` mit gepatchtem `getpass` gibt einen gültigen Hash aus. Abweichende Eingaben enden mit Exit 1.

### Frontend (`frontend/src/live/client.test.mts`)

- `retryAfterMinutes` (bleibt);
- `recordingUrl` für Live- und normale IDs inklusive Kodierung;
- `edgeStep`: Einholen → 1×, Stall → Pause, Vorlauf erreicht → weiter, live/nicht live.

### Ende-zu-Ende

Mit `scripts/live_dev_server.py --round-sec 120` im Browser:
- Login über den Button;
- live laufen lassen;
- auf den Rundenanfang springen;
- mit 8× aufholen (endet bei 1× live);
- „+10 s“ am Rand;
- „● LIVE“ aus der Mitte;
- Rundenwechsel abwarten;
- Logout im zweiten Tab.

## 13. Betrieb und Doku

**`docs/live-map.md`:**
- Einschalten auf zwei Wegen: Env-Hash für Docker, kein Config-Mount nötig; `live_password` als Hash oder Klartext.
- Der Button.
- Bedienung wie ein Livestream.
- Voraussetzung `--recordings-dir`.
- Kein Bild zwischen den Runden.
- Passwortwechsel: Env → Neustart zwischen zwei Runden, Config → SIGHUP.
- Proxies:
  - Der Stream ist NDJSON statt SSE. Für Browser ist er schon gzip-komprimiert.
  - `/api/live/` muss weiter ohne Puffer und ohne Kompressions-Middleware laufen.
  - Der Traefik-Hinweis zu `text/event-stream` entfällt.
- Prüfen mit `curl --compressed` gegen `/api/live/round` und `/api/live/round/<id>`.
- Die Log-Tabelle mit den neuen Zeilen.

**`2026-09-29-live-moderation-design.md`:** bekommt oben einen Verweis auf diese Spec. Die Code-Header in `live.py`, `LiveAccess.tsx` und `client.ts` verweisen auf beide.

## 14. Auslieferung

1. Umsetzung auf `live-moderation`. `frontend/dist` neu bauen und mitcommitten.
2. Pushen macht der Nutzer, die Shell hat keine Rechte auf den Fork.
3. Später Merge `live-moderation` → `master`.
4. Die Notiz zur Live-Karte im Gedächtnis wird aktualisiert (kein SSE mehr, Env-Hash).

## 15. Bekannte Grenzen

- Kein Live-Bild zwischen zwei Runden und keines ohne `--recordings-dir`.
- Live heißt 8 s hinter dem neuesten Frame. Bisher waren es 6 s.
- Ein Sprung weit zurück lädt die Runde ab dort bis jetzt. Bei einer langen Runde sind das einige zehn MB, wie bei einem Replay.
- Der Kill-Feed kennt nur den geladenen Bereich.
- Nach einem Reconnect sind die Frames vor dem Abspielkopf aus dem Speicher und werden beim Zurückspringen neu geladen.
- Wer am Ende einer Runde gerade ältere Szenen prüft, wird erst beim Erreichen des Endes zur nächsten Runde gebracht.
- Ein neuer Env-Hash braucht einen Neustart des Readers zwischen zwei Runden. Ein Neustart mitten in der Runde macht deren bisherige Aufnahme unbrauchbar.
- Ein Stream-Thread bemerkt einen weggegangenen Client erst beim nächsten Frame. Solange der Reader stockt, hält er seinen Platz.

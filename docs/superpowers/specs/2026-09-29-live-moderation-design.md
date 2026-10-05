# Live-Karte für die Moderation – Design

- **Datum:** 2026-09-29
- **Branch:** `live-moderation` (von `main` = Upstream v1.4.8). Die Änderung gibt es nur im Fork: Sie wird in `dcn-branding` gemergt und nie Upstream vorgeschlagen.
- **Status:** zur Durchsicht
- **Abgelöst in Teilen:** Abschnitt 8 (Live-Stream per SSE) und die SSE-Teile von 3, 9, 10, 13 und 14 ersetzt `2026-10-05-live-replay-design.md`: Die Live-Karte ist seitdem ein Replay der laufenden Runde.

## 1. Ziel

Moderatoren sollen eine laufende Runde live auf der Karte verfolgen können, statt auf das Rundenende und die fertige Aufnahme zu warten. Den Zugang schützt eine Login-Maske mit einem gemeinsamen Passwort. Der Betreiber trägt es in die `sqreader.config.json` des Hosts ein.

**Warum das heikel ist:** Eine Live-Karte zeigt alle Spieler beider Teams in Echtzeit. Wer mitspielt und die Karte nebenbei offen hat, kann „ghosten“. Upstream hat die Live-Ansicht deshalb aus dem öffentlichen Build entfernt (`tests/test_public_no_live.py`, `sqreader/httpsrv.py:5-9`). Der private `live`-Branch des Upstream-Entwicklers steht nicht zur Verfügung. Dieses Design baut die Funktion neu. Dabei nutzt es den Live-Renderpfad, der im Frontend noch vorhanden, aber ungenutzt ist.

## 2. Anforderungen

### Muss

1. Eine Live-Karte der laufenden Runde in Echtzeit im bestehenden Viewer. Sie läuft etwa 6 s hinterher, weil der vorhandene Render-Puffer so lange vorhält.
2. Zugang nur nach Login in einer Login-Maske der Web-UI, mit einem gemeinsamen Passwort `live_password` in der `sqreader.config.json`.
3. Ohne gültiges `live_password` fehlt das Feature vollständig. Die Server-Antworten sind dann byte-gleich zu heute (abgesehen vom `Date`-Header), und `tests/test_public_no_live.py` bleibt unverändert grün.
4. Ohne gültige Sitzung gibt es keinen Weg an Live-Daten, weder über den Stream noch über andere Endpunkte, Fehlermeldungen oder Caches. Die bestehenden Gates bleiben unverändert, auch für eingeloggte Moderatoren:
   - Es werden nur finalisierte Aufnahmen ausgeliefert.
   - Die Stats-API verbirgt die laufende Runde.
5. Zuschauer können die Tick-Schleife (Aufnahme, Stats, Plugins) nicht blockieren. Fehler im Live-Code stören sie nicht.
6. Missbrauch wird sichtbar: Logins und Streams erzeugen Logzeilen mit IP. Er lässt sich außerdem ohne Neustart per SIGHUP widerrufen.
7. In Python nur die Standardbibliothek, im Frontend nur vorhandene npm-Pakete. Keine neuen Abhängigkeiten.
8. So wenige Eingriffe wie möglich in geteilte Dateien (`httpsrv.py`, `cli.py`, `App.tsx`, `TopBar.tsx`, `Home.tsx`), damit Merges mit Upstream und den Fork-Branches einfach bleiben.

### Nicht im Umfang

- Zurückspulen innerhalb der laufenden Runde. Laufende Aufnahmen werden weiterhin nicht ausgeliefert.
- Einzelne Accounts pro Moderator.
- Ein gehashtes Passwort in der Config und Sitzungen, die einen Neustart überdauern.
- Automatisches Neu-Einpassen der Karte beim Mapwechsel.
- Ein Alerts-Endpunkt.
- Änderungen an `docker-deployment`, denn das ist der Upstream-PR.

### Erfolgskriterien

- Eingeloggt aktualisieren sich Karte, Killfeed und Status laufend. Abmelden oder SIGHUP beenden den Stream sofort.
- Ohne Login oder ohne Passwort in der Config sind keinerlei Live-Daten erreichbar.
- Alle bestehenden Tests bleiben grün (`pytest` auf 3.10 und 3.13, `npm run build`, `npm test`). Die neuen Tests sind grün, und der Ende-zu-Ende-Test ist bestanden.

## 3. Architektur

### Komponenten

| Einheit | Datei | Aufgabe | Abhängigkeiten |
|---|---|---|---|
| `Hub` | `sqreader/live.py` | Nimmt Frames aus der Tick-Schleife an und verteilt sie an die Stream-Threads. Blockiert nie. | `threading`, `collections` |
| `Access` | `sqreader/live.py` | Passwortprüfung, Sitzungen, Login-Bremse, Widerruf | `hashlib`, `hmac`, `secrets`, `ipaddress` |
| `LiveMap` | `sqreader/live.py` | Endpunkte unter `/api/live/*` und die SSE-Schleife. Hält Hub und Access. Einstieg ist `live_from_config()`. | `Hub`, `Access` |
| Hooks | `httpsrv.py`, `cli.py` | Routing, `do_POST`, Frames veröffentlichen, SIGHUP | `LiveMap` |
| `client.ts` | `frontend/src/live/` | Aufrufe für Session, Login und Logout; `createLiveFeed()` | `ReplayReconstructor` |
| `LiveAccess.tsx` | `frontend/src/live/` | Login-Dialog, Stream-Hook, Buttons | `client.ts`, `viewerStore` |

### Datenfluss

```
Tick-Schleife (Hauptthread)
  _consume_full: line ──────┐
  Positionsframe: pos_line ─┴─► LiveMap.publish() ─► Hub (Ring, notify_all)
                                                          │
HTTP-Thread pro Zuschauer ◄───────────────────────────────┘
  GET /api/live/stream (Cookie) ── SSE ──► Browser: EventSource
                                            → createLiveFeed (JSON.parse + ReplayReconstructor)
                                            → viewerStore.ingestLive → vorhandener Live-Pfad
```

## 4. Konfiguration

`live_password` steht im Klartext in der `sqreader.config.json`.

- Es gibt keinen Eintrag in `config.DEFAULTS`. `config.get()` liefert für fehlende Keys ohnehin `None`, und ein zusätzlicher Eintrag wäre nur ein weiterer Hunk, der bei Merges kollidieren kann.
- Es gibt kein CLI-Flag und keine Umgebungsvariable. So taucht das Passwort weder in `ps` noch in Compose-Dateien auf.

Beim Start wertet `live_from_config(value)` den Wert so aus:

| Wert | Ergebnis |
|---|---|
| fehlt, `null` oder `""` | Live aus, keine Ausgabe |
| kein String | Live aus, WARNING `live map disabled: live_password must be a string of at least 20 characters` |
| Leerzeichen am Anfang oder Ende | Live aus, WARNING `live map disabled: live_password has leading or trailing whitespace` |
| kürzer als 20 Zeichen | Live aus, dieselbe WARNING wie bei „kein String“ |
| gültig | Live an, INFO `live map enabled for moderators (login via ?mode=live)` |

- **Logging:** Der Wert selbst erscheint nie im Log. Alle Meldungen laufen über den Logger `sqreader.live`, den `cmd_serve` bereits konfiguriert.
- **Speicherung:** Im Speicher liegt nur `sha256(value.encode("utf-8", "surrogatepass"))`.
- **Änderungen:** Die Config wird wie bisher einmal beim Start gelesen. Eine Änderung wird per SIGHUP (Abschnitt 7) oder durch einen Neustart wirksam.

Dieser Block kommt ans Ende von `sqreader.config.example.json`; das bisher letzte Element `push_backlog_dir` bekommt dafür ein Komma:

```json
  "_live_comment": [
    "Moderator-only LIVE map. OFF unless live_password is set.",
    "Whoever knows it sees BOTH teams in real time: give it to moderators only.",
    "One shared password, at least 20 characters, e.g.",
    "  python3 -c 'import secrets; print(secrets.token_urlsafe(24))'",
    "It is a CREDENTIAL: chmod 600 this file.",
    "Moderators log in at https://<site>/?mode=live (HTTPS required).",
    "Change it or kick everyone out without a restart: edit it, then send SIGHUP.",
    "See docs/live-map.md."
  ],
  "live_password": null
```

## 5. HTTP-Schnittstelle

**Für alle Antworten der Live-Endpunkte (`session`, `login`, `logout`, `stream`):**
- Sie tragen `Cache-Control: no-store`.
- Sie tragen nie CORS-Header. `_send_cors_headers` wird dort nicht aufgerufen, auch nicht mit `--cors-origin`.
- JSON-Antworten haben `Content-Type: application/json` und ein `Content-Length`.

Die 404- und 501-Antworten in der Tabelle bleiben dagegen genau die der Standardbibliothek wie heute.

| Methode und Pfad | Auth | Antworten |
|---|---|---|
| `GET /api/live/session` | Cookie optional | 200 `{"authenticated": true\|false}` |
| `POST /api/live/login` | Passwort im Body | 200 `{"authenticated": true}` mit `Set-Cookie`; sonst 400, 401, 411, 413, 415 oder 429 (Abschnitt 6) |
| `POST /api/live/logout` | Cookie optional | 200 `{"authenticated": false}` mit löschendem `Set-Cookie`; 400, 411, 413 und 415 wie beim Login |
| `GET /api/live/stream` | gültige Sitzung | 200 SSE (Abschnitt 8); 401 `{"error": "not logged in"}`; 503 `{"error": "too many live viewers"}` mit `Retry-After: 30` |
| `GET /api/live/<anderer Pfad>` | – | `send_error(404, "no such endpoint")`, genau wie heute |
| `POST` auf jeden anderen Pfad | – | `send_error(501, "Unsupported method ('POST')")`, derselbe Text wie in der Stdlib |
| `HEAD`, `OPTIONS`, `PUT` usw. | – | unverändert das 501 der Stdlib. Deshalb scheitert jeder CORS-Preflight. |

**Wenn Live aus ist** (`live is None`):
- Es gibt keine Route unter `/api/live/`, dort greift das vorhandene 404.
- Es gibt kein `do_POST`-Attribut, also antwortet die Stdlib mit 501.
- Es gibt keinen Signal-Handler.

Die Pfade `/stream`, `/latest` und `/api/alerts` bleiben in beiden Modi 404.

## 6. Login, Sitzungen und Bremse

### Login-Verarbeitung (`POST /api/live/login`)

1. **Länge prüfen:**
   - `Content-Length` fehlt, ist keine Zahl oder negativ → 411.
   - Größer als 1024 → 413. Der Body wird dann nicht gelesen, und die Verbindung wird geschlossen.
2. **Body lesen:** vollständig, mit einer **Gesamtfrist von 10 s**. Vor jedem Lesen wird `settimeout(Restzeit)` gesetzt, gelesen wird mit `rfile.read1`. Läuft die Frist ab, wird die Verbindung ohne Antwort geschlossen.
3. **Content-Type prüfen:** ohne Parameter und kleingeschrieben muss er `application/json` sein, sonst 415. Der Body wird schon vorher gelesen, damit ungelesene Bytes keinen TCP-Reset auslösen.
4. **JSON parsen** in `try/except Exception`. Das fängt auch den `RecursionError`, den tief verschachteltes JSON unter Python 3.10/3.11 auslöst. 400 `{"error": "bad request"}` gibt es bei:
   - einem Fehler beim Parsen,
   - einem JSON-Wert, der kein Objekt ist,
   - einem fehlenden oder leeren String-Feld `password`.
5. **Bremse:** Ist eine Grenze erreicht (siehe unten), antwortet der Server mit 429 `{"error": "too many attempts"}` und `Retry-After: <Sekunden>`.
6. **Falsches Passwort** → 401 `{"error": "wrong password"}`, immer dieselbe Antwort. Der Fehlversuch wird gezählt und als WARNING `live: login failed from <client>` geloggt.
7. **Richtiges Passwort:**
   - Die Sitzungen aller mitgeschickten `sqr_live`-Cookies werden verworfen.
   - Eine neue Sitzung wird angelegt.
   - Die Fehlversuche dieses Clients werden gelöscht.
   - Antwort 200 mit Cookie, dazu INFO `live: login ok from <client> (session <id>)`.

- **Sperre:** Die Schritte 5 bis 7 laufen unter der Sperre von `Access`. SHA-256 dauert Mikrosekunden, und so gibt es keine Race-Condition zwischen Prüfen und Zählen.
- **Session-ID im Log:** `<id>` sind die ersten 8 Hex-Zeichen von `sha256(token)`.

**Logout (`POST /api/live/logout`):**
1. Die Schritte 1 bis 4 wie beim Login. Der Body darf `{}` sein, ein `password` wird nicht verlangt.
2. Alle mitgeschickten Sitzungen werden gelöscht.
3. Der Hub wird geweckt, damit Streams sofort nachprüfen.
4. INFO `live: logout from <client>`, aber nur, wenn eine Sitzung bestand.

### Passwortprüfung

Geprüft wird mit `hmac.compare_digest(sha256(eingabe.encode("utf-8", "surrogatepass")).digest(), gespeicherter_digest)`. Nach einem fehlgeschlagenen SIGHUP-Reload ist kein gültiges Passwort geladen; dann schlägt jede Prüfung fehl.

### Sitzungen

- **Token:** `secrets.token_urlsafe(32)`, nur im Speicher als `dict token → (Ablauf als monotonic-Zeit, client)`.
- **Laufzeit:** 12 h absolut, ohne Verlängerung. Abgelaufene Einträge werden bei jedem Login entfernt.
- **Anzahl:** höchstens 32 Sitzungen. Kommt eine weitere hinzu, wird die älteste verdrängt.
- **Neustart:** Ein Neustart des Readers löscht alle Sitzungen.
- **Cookies lesen:** Alle `Cookie`-Header werden ausgewertet (`get_all("Cookie")`) und darin die ersten 8 `sqr_live`-Werte (`MAX_COOKIE_VALUES`). Die Grenze verhindert, dass ein Header voller Duplikate für jeden Wert eine Sperre und eine Abfrage kostet. Der erste gültige Wert zählt.

Cookie beim Login:
`sqr_live=<token>; Max-Age=43200; HttpOnly; Secure; SameSite=Strict`

Cookie beim Logout:
`sqr_live=; Max-Age=0; HttpOnly; Secure; SameSite=Strict`

- **Pfad:** Es gibt keinen `Path`. Der Browser nimmt dann das Verzeichnis der Login-URL, also `/api/live` oder `/<präfix>/api/live`. Das deckt alle Live-Endpunkte ab, auch hinter einem Pfad-Präfix.
- **Weitere Attribute:** keine `Domain` und kein `__Host-`-Präfix.
- **`Secure`:** ist immer gesetzt. Das funktioniert über HTTPS und in Chrome und Firefox auch auf `localhost`/`127.0.0.1`. Über plain HTTP im LAN funktioniert es nicht; dort lässt die UI gar keine Eingabe zu.

### Login-Bremse

- **Was zählt:** nur **Fehlversuche**, in einem gleitenden Fenster von 10 min.
- **Grenzen:** höchstens 5 pro Client und 50 insgesamt.
  - Erreicht ein Client seine Grenze, bekommt dieser Client 429.
  - Ist die globale Grenze erreicht, bekommen alle 429.
  - In beiden Fällen gilt das **auch mit richtigem Passwort**.
- **`Retry-After`:** die Sekunden, bis der älteste relevante Fehlversuch aus dem Fenster fällt, aufgerundet und mindestens 1.
  - Bei der Client-Grenze ist das der älteste Fehlversuch dieses Clients, bei der globalen der älteste insgesamt.
  - Greifen beide, gilt der größere Wert.
- **Log:** Beim Erreichen einer Grenze einmal WARNING `live: login limit reached for <client>` bzw. `live: global login limit reached`.
- **Kein Warten, keine Dauersperre:** Es gibt kein `sleep` und keine Dauersperre. Laufende Sitzungen sind nicht betroffen.
- **Speicher:** Die Fehlversuche liegen in einer `deque` mit höchstens 50 Einträgen. Ist die globale Grenze erreicht, kommt nichts mehr dazu.

**Client-Schlüssel:**
1. Genommen wird der letzte `X-Forwarded-For`-Header (`get_all(...)[-1]`) und daraus der letzte kommagetrennte Eintrag.
2. `ipaddress.ip_address` prüft ihn. Ist er ungültig, zum Beispiel wegen eines Zeilenumbruchs, gilt `client_address[0]`. Damit ist keine Log-Injection möglich.
3. Eine IPv6-Adresse wird auf ihr `/64`-Netz gekürzt, eine IPv4-mapped-Adresse wird als IPv4 behandelt.

Hinter Traefik, Caddy oder nginx ist dieser Eintrag die echte Client-IP. Bei direktem Zugriff auf `:8080` lässt er sich fälschen; dagegen hilft die globale Grenze.

## 7. Notfall-Widerruf per SIGHUP

- **Registrierung:** `cmd_serve` registriert den Handler nur, wenn Live aktiv ist.
- **Keine Sperren im Signal-Kontext:** Der Handler startet nur einen kurzen Thread (`sqreader-live-reload`), der die eigentliche Arbeit macht.
  - **Ausnahme, wenn kein Thread startet:** Wirft `Thread.start()`, widerruft der Handler selbst (`reset(None)` und `kick()`, ohne Log). So läuft keine Exception in die Tick-Schleife des Readers.
  - Das ist im Signal-Kontext sicher, weil der Haupt-Thread `Access._lock` nie hält und die Condition des Hubs re-entrant ist.
- **Was der Thread tut:**
  1. `live_password` direkt aus der Config-Datei lesen: `$SQREADER_CONFIG`, sonst `./sqreader.config.json`, genau wie `config._load`. Der globale Config-Cache bleibt dabei unberührt.
  2. Den Wert nach Abschnitt 4 prüfen.
  3. Den gespeicherten Digest ersetzen.
  4. **Alle Sitzungen** löschen.
  5. Den Hub wecken. Dadurch enden alle Streams sofort.
- **Fehlerfall:** Ist die Datei unlesbar, das JSON kaputt oder der Wert ungültig, ist kein gültiges Passwort mehr geladen.
  - Dann scheitert jeder Login mit 401, bis die Datei korrigiert und erneut SIGHUP gesendet ist.
  - Log: WARNING `live: SIGHUP: all sessions revoked; NO valid live_password in <pfad> (<grund>), logins disabled until fixed`.
  - `<grund>` bei einem Lesefehler ist nur der Typname der Exception, etwa `FileNotFoundError` oder `JSONDecodeError`. Meldungstext und Dateiinhalt stehen nie im Log, denn beides kann das Passwort zitieren.
  - `<grund>` bei einem gelesenen, aber abgelehnten Wert ist die Regel aus Abschnitt 4 ohne das Präfix `live map disabled: `. Bei einem fehlenden Wert, auch bei JSON, das kein Objekt ist, steht `live_password is not set`.
  - Ließ sich der Pfad nicht auflösen, etwa weil das Arbeitsverzeichnis gelöscht wurde, steht `None` statt `<pfad>`. Die Sitzungen sind trotzdem weg.
- **Erfolgsfall:** WARNING. Der Text hängt davon ab, ob sich das Passwort geändert hat; `Access.reset()` gibt zurück, ob der gespeicherte Digest ein anderer wurde, und `None` zählt dabei als Wert.
  - anderer Digest: `live: SIGHUP: all sessions revoked, password reloaded`
  - gleicher Digest: `live: SIGHUP: all sessions revoked; live_password in <pfad> is UNCHANGED`
  - Die Zeile UNCHANGED deckt den Docker-Fall auf: Ersetzt ein Editor die Datei, liest der Container über den Single-File-Bind-Mount weiter die alte Inode, und das Log würde sonst „reloaded“ melden, obwohl noch das alte Passwort gilt.
- **Fehlversuche:** Die Zählung der Fehlversuche bleibt erhalten.
- **Aufruf:** `docker compose kill -s HUP sqreader` bzw. `systemctl kill -s HUP --kill-whom=main <unit>`. Ohne `--kill-whom=main` bekommt auch der Build-Worker der Zwei-Ebenen-Aufnahme das Signal.
- **Ohne Live:** Es gibt keinen Handler. Unter systemd oder im Terminal beendet SIGHUP den Prozess wie heute; als PID 1 eines Containers ignoriert der Kernel das Signal. Wer Live nachträglich einschaltet, muss neu starten.

## 8. Live-Stream

### Hub

- **`publish(line, full)`:**
  - Kodiert den Frame einmal zu `b"data: " + line.rstrip("\n").encode("utf-8") + b"\n\n"`. `json.dumps` erzeugt keine Zeilenumbrüche, deshalb ergibt jede Zeile genau ein Ereignis.
  - Erhöht `seq` und merkt sich volle Frames als `last_full`.
  - Hängt das Ereignis an den Ring (`deque(maxlen=128)`), aber **nur, wenn Zuschauer verbunden sind**.
  - Weckt alle wartenden Threads.
  - Macht keine Socket-Arbeit. Jede Exception wird abgefangen; geloggt wird nur die erste.
- **Ohne Zuschauer** wird der Ring geleert. Gehalten wird dann nur `last_full`.
- **`subscribe()`** gibt die aktuelle `seq` als Cursor und die Payload von `last_full` zurück. Dazu gibt es `unsubscribe()`.
- **`kick()`** weckt alle Streams (bei Logout und SIGHUP). **`close()`** beendet sie (für Tests).
- **Sperre:** Der Hub hat eine eigene `threading.Condition`, getrennt von der Sperre in `Access`.

### Stream-Handler (`GET /api/live/stream`)

1. **Ohne gültige Sitzung** → 401, bevor ein Platz belegt wird.
2. **Bei 10 aktiven Streams** → 503 mit `Retry-After: 30`. Dazu höchstens einmal pro Minute WARNING `live: stream refused from <client> (10/10 in use)`.
3. **Sonst:**
   - `subscribe()`
   - INFO `live: stream opened from <client> (<n>/10)`
   - Socket-Timeout 20 s
4. **Header:**
   - Statuszeile HTTP/1.0: der Standard des Handlers, `protocol_version` wird nicht angefasst.
   - `Content-Type: text/event-stream; charset=utf-8`
   - `Cache-Control: no-store`
   - `X-Accel-Buffering: no`
   - `Connection: close`
   - Nicht gesendet werden `Content-Length`, `Transfer-Encoding`, Kompression und CORS-Header. Das Ende des Bodys markiert das Schließen der Verbindung.
5. **Erster Schreibvorgang:** `retry: 3000\n\n` und dahinter `last_full`, falls vorhanden.
6. **Schleife:** Der Thread wartet, bis ein neues Ereignis kommt, der Hub geweckt wird oder seit dem letzten Schreiben 15 s vergangen sind. Danach prüft er **vor jedem Schreiben** die Sitzung.
   - Gibt es neue Ereignisse, gehen die von `pick()` ausgewählten in einem einzigen Schreibvorgang raus.
   - Kam seit 15 s nichts Neues, geht `: ka\n\n` raus.
7. **`pick(events)`** ist eine reine Funktion. Sie liefert alle vollen Frames in Reihenfolge und dazu den neuesten Positionsframe nach dem letzten vollen Frame. Die übrigen Positionsframes entfallen.
8. **Ende:** INFO `live: stream closed from <client> after <dauer> (<grund>)`. Mögliche Gründe:
   - `session ended`: Die Sitzung wurde abgemeldet, ist abgelaufen oder per SIGHUP widerrufen.
   - `too slow`: Der Ring hat den Cursor überholt, es fehlen also Ereignisse.
   - `write timeout`: Ein Schreibvorgang hing 20 s.
   - `client gone`: Der Client hat die Verbindung beendet.
   - `server stopping`: `close()` wurde aufgerufen.

Nach einem Stream, der mit 200 lief, verbindet der Browser selbst neu und beginnt wieder beim neuesten vollen Frame. Frames laufen auch zwischen den Runden weiter, also während der Lobby und beim Mapwechsel.

### Konstanten

Das sind Modul-Konstanten in `live.py`. Tests dürfen sie per Monkeypatch verkleinern.

| Name | Wert |
|---|---|
| `MIN_PASSWORD_LEN` | 20 |
| `SESSION_TTL_SEC` | 43200 (12 h) |
| `MAX_SESSIONS` | 32 |
| `BODY_MAX` | 1024 Bytes |
| `BODY_DEADLINE_SEC` | 10 |
| `FAIL_WINDOW_SEC` | 600 |
| `FAILS_PER_CLIENT` | 5 |
| `FAILS_GLOBAL` | 50 |
| `MAX_STREAMS` | 10 |
| `RING_SIZE` | 128 |
| `KEEPALIVE_SEC` | 15 |
| `RETRY_MS` | 3000 |
| `WRITE_TIMEOUT_SEC` | 20 |
| `REFUSED_LOG_INTERVAL_SEC` | 60 |
| `MAX_COOKIE_VALUES` | 8 |

## 9. Eingriffe in bestehende Dateien

### `sqreader/httpsrv.py`

Es kommen keine neuen Imports dazu, und die Docstrings bleiben, wie sie sind.

1. `_make_handler(..., stats_db: Optional[Path] = None, live: Any = None)`.
2. In `do_GET` direkt vor dem abschließenden `else`:
   ```python
   elif live is not None and path.startswith("/api/live/"):
       live.handle_get(self, path)
   ```
3. Vor `return _H`:
   ```python
   if live is not None:
       # Fork-only moderator live map (sqreader/live.py). POST exists only while
       # it is enabled, so a build without live_password keeps stdlib's 501.
       _H.do_POST = lambda self: live.handle_post(self, self.path.split("?", 1)[0])
   ```
4. `serve_in_background(..., stats_db=None, live: Any = None)` bekommt den Parameter und reicht `live` an `_make_handler` durch.

### `sqreader/cli.py`

Alle Änderungen liegen in `cmd_serve`.

1. Vor `srv = serve_in_background(...)`:
   ```python
   from .live import live_from_config
   live = live_from_config(config.get("live_password"))
   ```
2. `live=live` im Aufruf von `serve_in_background`.
3. Nach `beat.mark()` in `_consume_full`: `if live is not None: live.publish(line, full=True)`.
4. Nach `beat.mark()` beim Positionsframe: `if live is not None: live.publish(pos_line, full=False)`.
5. Nach den Handlern für SIGTERM und SIGINT: `if live is not None: signal.signal(signal.SIGHUP, live.on_sighup)`.

Einen Shutdown-Hook braucht es nicht. Die Stream-Threads sind Daemon-Threads und enden mit dem Prozess, der Browser verbindet danach neu.

### Frontend

- **`App.tsx`:** ein Import und `{!statsRoute && <LiveAccess />}` neben den übrigen Dialogen, außerhalb des Modus-Zweigs. Auf der `/stats`-Route (apple-Theme) läuft nichts, was mit Live zu tun hat.
- **`TopBar.tsx`:** ein Import nach `SettingsMenu` und `<LiveControls />` direkt vor `<ClipRecorder />`.
- **`Home.tsx`:** ein Import und `<LiveEntry />` als erstes Kind von `.hm-nav`.
- **Unverändert:**
  - `viewerStore.ts`, `MapCanvas.tsx`, `style.css`
  - `vite.config.ts`, denn `/api` wird schon weitergeleitet
  - `canLive` bleibt `false`
- **CSS:** Das CSS liegt in `frontend/src/live/live.css`. `LiveAccess.tsx` importiert es, damit es durch den Vite-Build läuft; der Server liefert nur `/` und `/assets/*` aus.

## 10. Frontend-Verhalten

### Dateien in `frontend/src/live/`

- **`client.ts`:**
  - `probeSession(): Promise<"off" | "anon" | "ok" | "unknown">`: 404 wird zu `off`, 200 zu `anon` oder `ok`, alles andere zu `unknown`.
  - `login(password)` liefert `{ ok: true }` oder `{ ok: false, message }`. Dazu gibt es `logout()`. Beide schicken JSON: `{"password": ...}` bzw. `{}`.
  - `createLiveFeed()` liefert `{ push(data: string): Snapshot | null }`:
    - Es parst mit `JSON.parse`, nimmt nur Objekte und reicht sie an `ReplayReconstructor.push` weiter.
    - Positionsframes vor dem ersten vollen Frame ergeben `null`.
  - `retryAfterMinutes(header)` rechnet `Retry-After` in Minuten um, aufgerundet und mindestens 1.
- **`LiveAccess.tsx`:**
  - ein kleiner zustand-Store `{ access, loginOpen, notice }`
  - die Funktionen `enterLive()`, `exitLive()` und den Hook `useLiveStream()`
  - die Komponenten `LiveAccess`, `LiveEntry` und `LiveControls`
  - importiert `live.css`
- **`live.css`:** Der Dialog sieht aus wie `#recording-picker`; verwendet werden nur vorhandene Tokens. Dazu kommt eine Klasse, die Elemente visuell versteckt.
- **`client.test.mts`**

### Ablauf

1. **Beim Laden** ruft `LiveAccess` einmal `probeSession()` auf. Bei `off` und `unknown` rendert es nichts.
2. **Mit `?mode=live` in der URL:**
   - bei `ok` → `enterLive()`
   - bei `anon` → der Login-Dialog öffnet sich
   - bei `off` → der Parameter wird entfernt
   - bei `unknown` → nichts passiert, der Parameter bleibt stehen
3. **Login-Dialog** `<dialog id="live-login">`, nur bei `anon` und `ok` gerendert:
   - **Texte:** Überschrift „Moderator-Login“, darunter „Live-Karte der laufenden Runde – nur für das Moderationsteam.“
   - **Felder:**
     - ein unsichtbares, schreibgeschütztes Feld `username` mit dem Wert „moderator“ (`autocomplete="username"`, `aria-hidden`, `tabIndex=-1`, per CSS versteckt), damit Passwort-Manager den Eintrag zuordnen können
     - das Passwortfeld mit dem Label „Passwort“ (`autocomplete="current-password"`, `required`)
     - die Buttons „Anmelden“ und „Abbrechen“
   - **Ohne sicheren Kontext:** Ist `window.isSecureContext` falsch, steht statt der Felder nur „Anmeldung nur über HTTPS möglich.“
   - **Fehlermeldungen** in einem Element mit `role="alert"`:
     - „Falsches Passwort.“ bei 401
     - „Zu viele Fehlversuche – bitte in N Min. erneut versuchen.“ bei 429
     - „Server nicht erreichbar.“ bei einem Netzwerkfehler
     - „Anmeldung fehlgeschlagen (HTTP x).“ in allen anderen Fällen
   - **Bedienung:**
     - Während der Anfrage ist der Button deaktiviert, und nach dem Absenden wird das Eingabefeld geleert.
     - Nach `showModal()` bekommt das Passwortfeld den Fokus.
     - `onKeyDown` stoppt die Weitergabe von Tasten, damit die App-Kürzel (Tab, Leertaste, F) im Dialog nicht auslösen.
   - **Bei Erfolg** wird `access = ok` gesetzt, der Dialog geschlossen und `enterLive()` aufgerufen.
4. **`LiveEntry`** zeigt auf der Startseite den Button „Live-Karte“, aber nur bei `access === "ok"`.
5. **`enterLive()`:**
   1. Setzt in einem einzigen `setState` die Felder `curSnap`, `prevSnap` und `lastInProgressTeams` auf `null`, `curArrivalMs` auf 0 und `status` auf `connecting`. So kann der Killfeed nicht gegen einen alten Replay-Frame rechnen und Kills erfinden.
   2. Ruft danach `setMode("live")` auf.
   3. Setzt `?mode=live` (ohne `id`) per `replaceState` in die URL.

   Einstieg gibt es nur von der Startseite oder beim Laden der Seite. Die Karte wird dadurch neu eingepasst.
6. **`useLiveStream()`** ist aktiv, solange `mode === "live"` gilt:
   - **Verbindung:** `new EventSource("./api/live/stream")`, dazu pro Verbindung ein neuer `createLiveFeed()`.
   - **Ereignisse:**
     - `onopen` → `status = live`
     - `onmessage` → Feed → `ingestLive`, aber nur, solange noch `mode === "live"` gilt
     - `onerror` bei `CONNECTING` → `status = reconnecting`. Der Browser verbindet nach 3 s selbst neu.
   - **`onerror` bei `CLOSED`:** Die Verbindung wird geschlossen, `status = reconnecting` gesetzt und `probeSession()` aufgerufen.
     - Bei `anon` folgt `exitLive()`, und der Dialog öffnet sich mit „Sitzung abgelaufen – bitte neu anmelden.“
     - In allen anderen Fällen folgt nach 5 s ein neuer Versuch. Das gilt für `ok` und `unknown`, aber auch für `off`: Ein 404 mitten in der Sitzung ist die Neustart-Lücke von Traefik.
   - **Aufräumen** bei Moduswechsel, Unmount und StrictMode: `close()`, den Timer löschen und dieselben Frame-Felder leeren wie in `enterLive()`.
7. **`LiveControls`** in der TopBar, nur bei `mode === "live"`:
   - „← Zurück“ ruft `exitLive()` auf.
   - „Abmelden“ ruft `logout()` auf (Fehler werden ignoriert), setzt dann `access = anon` und ruft `exitLive()` auf.
8. **`exitLive()`** ruft `setMode("home")` auf und entfernt `mode` und `id` aus der URL. Nie `window.location.href = "/"`, weil das Pfad-Präfixe bricht.
9. **Aufnahme öffnen:** Wer im Live-Modus eine alte Aufnahme öffnet, wechselt in den Replay-Modus. Der Stream schließt dabei über das Aufräumen.

Der vorhandene Live-Pfad bleibt unverändert:
- der 6-s-Puffer und das Carry-over
- der Live-Killfeed
- `BufferOverlay` mit Aufwärmen, Stillstand und „Reconnecting…“
- Statusanzeige und „data Ns“ in der TopBar
- `MatchOverlay`

## 11. Fehlerbehandlung im Überblick

| Situation | Verhalten |
|---|---|
| Fehler in `publish()` | wird abgefangen und einmal geloggt; die Aufnahme läuft weiter |
| Client trennt die Verbindung | `OSError` im Stream wird abgefangen, der Platz wird frei, eine Logzeile entsteht |
| Client liest nicht mehr | Nach 20 s Schreib-Timeout endet der Stream. |
| Client ist zu weit zurück | Der Ring hat ihn überholt: Der Stream endet, der Browser verbindet neu. |
| Login-Body kaputt, zu groß oder tröpfelnd | 400, 411, 413 oder 415, beziehungsweise die Verbindung schließt nach 10 s; kein Traceback |
| Ungültige Config beim Start | Live bleibt aus, eine WARNING ohne den Wert |
| Ungültige Config bei SIGHUP | Alle Sitzungen sind weg, kein Login ist möglich, bis die Datei korrigiert ist. |
| Traefik antwortet beim Container-Neustart mit 404 oder 502 | Das Frontend verbindet alle 5 s neu, solange die Session-Abfrage nicht „abgemeldet“ meldet. |
| Sitzung abgelaufen oder widerrufen | Der Stream endet sofort, das Frontend zeigt den Login mit Hinweis. |

## 12. Sicherheit im Überblick

| Bedrohung | Maßnahme |
|---|---|
| Unbefugter Zugriff auf Live-Daten | Ohne Sitzung geht kein einziges Stream-Byte raus, und vor jedem Schreiben wird die Sitzung geprüft. Die bestehenden Gates bleiben unverändert. |
| Passwort raten | Mindestens 20 Zeichen, ein generiertes Passwort wird empfohlen. Die Bremse greift vor der Prüfung: 5 Fehlversuche pro Client und 50 insgesamt, je 10 min. |
| Moderatoren durch Fehlversuche aussperren | Kurze Fenster, keine Dauersperre, laufende Sitzungen bleiben unberührt. Als Ausweg ist eine IP-Allowlist am Proxy dokumentiert. |
| Sitzung stehlen oder fixieren | 256-Bit-Token; HttpOnly, Secure, SameSite=Strict; neues Token bei jedem Login; Ablauf und Abmelden serverseitig |
| CSRF | SameSite=Strict und nur `application/json`. Cross-Site-JSON braucht einen Preflight, und der scheitert am 501. |
| Lecks über Logs, URLs oder Caches | Passwort und Token stehen nie in einer URL oder im Log; dazu `no-store` und keine CORS-Header. |
| Passwort im Klartext übers Netz | Die UI sendet es nur im sicheren Kontext (HTTPS oder localhost); das Cookie ist `Secure`. |
| Überlastung des Gameserver-Hosts | Body höchstens 1 KiB mit Frist, höchstens 10 Streams und 32 Sitzungen, begrenzter Ring. Das Passwort wird ohne teure Hash-Funktion (KDF) geprüft, und der Producer wartet nie. |
| Geleaktes Passwort | Jeder Login und jeder Stream erzeugt eine Logzeile mit IP. SIGHUP widerruft sofort, ohne die laufende Aufnahme zu zerstören. |
| Log-Injection | Der Client-Schlüssel wird als IP-Adresse geprüft. |
| Gemeinsame Origin mit anderen Apps (nginx-Präfix) | Die Doku empfiehlt einen eigenen Hostnamen für die Live-Karte. Die Setups mit Traefik und Caddy haben ihn bereits. |

## 13. Tests

### Python: `tests/test_live.py` (neu)

So werden die Tests aufgebaut:
- Den Server startet `serve_in_background("127.0.0.1", 0, _TickBeat(), ..., live=...)`.
- Streams werden über einen Raw-Socket mit Timeout gelesen.
- Konstanten werden per Monkeypatch verkürzt.
- Im `finally` steht `hub.close()`.

1. **Live aus und Abgrenzung:**
   - Ohne Live liefern `/api/live/session`, `/api/live/stream` und `/api/live/login` dieselbe Antwort wie ein beliebiger unbekannter Pfad (ohne `Date`).
   - Ohne Live liefern POST, HEAD und OPTIONS exakt das 501 der Stdlib.
   - Mit Live ist ein POST auf einen Pfad außerhalb von `/api/live/` byte-gleich zur Antwort ohne Live (ohne `Date`).
2. **Ungültige Config-Werte** (Zahl, zu kurz, Leerzeichen) schalten Live ab und erzeugen eine WARNING ohne den Wert.
3. **Login:**
   - falsches und richtiges Passwort
   - Cookie-Attribute exakt: HttpOnly, Secure, SameSite=Strict, Max-Age=43200, kein Path, keine Domain
   - Session-Abfrage
   - Logout löscht Cookie und Sitzung
4. **Eingaberegeln:**
   - 415, 411 und 413
   - 400 bei kaputtem JSON, einem Wert, der kein Objekt ist, fehlendem Passwort und 1024-mal `[`
   - Ein tröpfelnder Body wird nach der Frist geschlossen.
5. **Bremse:**
   - Der 6. Fehlversuch bekommt 429 mit `Retry-After`, auch mit richtigem Passwort.
   - Ein anderer Client kann sich weiterhin anmelden.
   - Die globale Grenze greift.
   - Ein Erfolg setzt den Zähler des Clients zurück.
6. **Client-Schlüssel:**
   - Es zählt der rechte Eintrag von `X-Forwarded-For`, auch bei mehreren Headern.
   - Ungültige Werte, auch mit CR/LF, fallen auf die Socket-Adresse zurück.
   - IPv6 wird zu /64.
7. **Sitzungen:**
   - Ablauf (mit gepatchter Uhr)
   - die Grenze von 32, bei der die älteste wegfällt
   - ein neues Token pro Login
   - bei doppeltem Cookie zählt der erste gültige Wert, gelesen werden aber höchstens die ersten 8
8. **CORS und Caching:** Mit `cors_origin="*"` fehlt `Access-Control-Allow-Origin` auf `/api/live/*`, und überall steht `no-store`.
9. **Stream:**
   - 401 ohne Sitzung oder mit ungültiger Sitzung, ohne dass ein Platz belegt wird
   - 503 ab 10 Streams
   - Statuszeile `HTTP/1.0 200`, exakte Header, kein `Content-Length`, `Transfer-Encoding` oder `Content-Encoding`
   - zuerst `retry: 3000` und der neueste volle Frame, danach volle Frames und Positionsframes in Reihenfolge
10. **`pick()`** (reine Funktion): alle vollen Frames und nur der neueste Positionsframe nach dem letzten vollen.
11. **Keepalive und Ende:**
    - Keepalive nach Leerlauf
    - Ende bei Logout, SIGHUP-Reload, `close()`, hängendem Leser (Schreib-Timeout) und überholtem Ring
12. **`publish()`** wirft nie, auch nicht bei kaputter Eingabe, und bleibt schnell, wenn ein Client hängt.
13. **SIGHUP-Reload:**
    - Das neue Passwort gilt, alte Sitzungen sind ungültig.
    - Bei unlesbarer Datei oder ungültigem Wert ist kein Login möglich.
    - Das Log nennt den Grund eines Fehlschlags, nur als Typname oder Regel und nie mit Meldungstext oder Inhalt, und meldet `UNCHANGED`, wenn die Datei noch das alte Passwort enthält.
    - Kann kein Thread starten, widerruft der Handler selbst und wirft nichts.
14. **Gates mit aktivem Live unverändert:** Eine aktive Aufnahme liefert 404, und die laufende Runde ist in `/api/matches` unsichtbar.

`tests/test_public_no_live.py` und `tests/test_http_versions.py` bleiben unverändert.

### Frontend: `frontend/src/live/client.test.mts`

**Feed:**
- Kaputte Zeilen und Werte, die keine Objekte sind, ergeben `null`.
- Ein Positionsframe vor dem ersten vollen Frame ergibt `null`.
- Ein voller Frame kommt unverändert heraus.
- Ein Positionsframe danach bringt neue Positionen und leere `damageEvents`.
- Ein neuer Feed beginnt leer.

Außerdem wird `retryAfterMinutes` getestet.

### Ende-zu-Ende

1. **Aufbau:** Ein Server mit `live_password` und ein Test-Einspeiser, der synthetische Frames liefert: volle Frames mit 0,5 Hz und Positionsframes mit 4 Hz. Ein Squad-Server ist dafür nicht nötig.
2. **Echter Browser** (Chrome) auf `http://localhost`:
   1. `/?mode=live` öffnen, der Dialog erscheint.
   2. Ein falsches Passwort zeigt die Fehlermeldung.
   3. Mit dem richtigen Passwort bewegt sich die Karte, und der Status zeigt „live“.
   4. „Abmelden“ führt zur Startseite.

   Ein zweiter Durchlauf prüft SIGHUP: Danach muss der Login-Dialog mit Hinweis erscheinen.
3. **Bandbreite** pro Zuschauer messen.
4. **Traefik:** Ein lokaler Traefik v3 mit aktivierter Kompression läuft vor dem Server. Mit `curl -N` muss jedes Ereignis ohne Verzögerung ankommen.
5. **Optional:** eine echte `.sqrx` vom Server als Live-Quelle abspielen, für realistische Frame-Größen.
6. **Beim ersten Einsatz auf dem echten Server:** den möglichen Rücksprung mit `RECORD_HZ=4` ansehen.

## 14. Betrieb und Doku

`docs/live-map.md` ist neu. Sie ist auf Englisch wie die übrigen Docs und hat etwa 40 Zeilen:
- **Aktivieren:**
  1. Passwort generieren und in die `sqreader.config.json` eintragen.
  2. `chmod 600` auf die Datei.
  3. In Docker den vorhandenen Config-Mount einkommentieren.
  4. Neu starten.
- **Einloggen:** über `https://<site>/?mode=live`; HTTPS ist nötig.
- **Passwort wechseln oder alle rauswerfen:** die Datei ändern, dann `docker compose kill -s HUP sqreader`.
- **Abschalten:** den Key entfernen und zwischen zwei Runden neu starten. Ein Neustart mitten in der Runde macht den bisherigen Teil der Aufnahme unabspielbar.
- **Proxys:**
  - Traefik: keine Buffering-Middleware, `writeTimeout` auf 0; bei v3.0 bis 3.3.4 `text/event-stream` von der Kompression ausnehmen.
  - Caddy: `/api/live/*` vom `encode` ausnehmen.
  - nginx: nichts nötig.
- **Hostname:** ein Hostname pro Instanz, denn Cookies unterscheiden keine Ports. Die Live-Karte gehört nicht als Pfad auf eine Origin mit fremden Apps.
- **Wenn Logins blockiert sind:** die Logzeilen der beiden Grenzen, dass laufende Streams weiterlaufen, und die IP-Allowlist am Proxy als Ausweg (Traefik-Labels, Caddy, nginx). Dazu, dass die Bremse auf den rechten `X-Forwarded-For`-Eintrag schaut und hinter einem CDN nur die Allowlist hilft.
- **Logs:** welche Logzeilen es gibt und was sie bedeuten; dazu die Prüfung mit `curl -N`.

## 15. Auslieferung

1. **Branch:** `live-moderation` von `main`, diese Spec ist der erste Commit. Commits enthalten keine KI-Trailer.
2. **Umsetzung:** nach dem Implementierungsplan und testgetrieben. Danach `frontend/dist` neu bauen und committen.
3. **Merge in `dcn-branding`:**
   - In `Home.tsx` kommt `<LiveEntry />` in die `.hm-nav` von `dcn-branding`. Das ist ein einmaliger Konflikt.
   - `frontend/dist` wird auf dem zusammengeführten Stand neu gebaut und committet.
   - In `deploy/Caddyfile` wird `/api/live/*` vom `encode` ausgenommen.
   - In `docker-compose.yml` bekommt der Kommentar am Config-Mount einen Hinweis auf `live_password`.
   - Nichts davon landet in `docker-deployment`.
4. **Push** nur auf Ansage.
5. **Aktivieren** auf dem Server nach `docs/live-map.md`. Dabei die Traefik-Version und die Kompressions-Einstellung prüfen.
6. **Rückbau:** `live_password` entfernen und neu starten. Der Server ist dann wieder byte-gleich zu heute.

## 16. Bekannte Grenzen

- Nach einem Mapwechsel bleibt der Kartenausschnitt der alten Map stehen. Die Taste F passt ihn neu ein.
- Der Killfeed wird bei einer neuen Runde nicht geleert; er hält höchstens 60 Einträge.
- Rundenende-Overlay und Team-Leiste zeigen den neuesten Frame, also etwa 6 s vor der Karte.
- Mit `RECORD_HZ=4` kann die Karte bei jedem vollen Frame kurz zurückspringen, wie heute schon in Aufnahmen.
- Jeder Neustart des Readers, zum Beispiel bei einem Squad-Neustart, meldet alle Moderatoren ab.
- Bei direktem Zugriff auf `:8080` lässt sich `X-Forwarded-For` fälschen. Dann schützt nur die globale Grenze.
- Safari kann sich über `http://localhost` nicht anmelden. Das betrifft nur die Entwicklung.
- Ab 10 gleichzeitigen Streams, zum Beispiel bei vielen offenen Tabs, zeigen weitere Tabs nur „Reconnecting…“.

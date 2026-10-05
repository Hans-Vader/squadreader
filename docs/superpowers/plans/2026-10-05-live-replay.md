# Live-Karte als Replay der laufenden Runde – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Moderatoren-Live-Karte wird der Replay-Player der laufenden Runde (Timeline vom Rundenstart bis jetzt, „● LIVE“ wie bei einem YouTube-Livestream). Dazu kommen ein Login-Button, ein optionaler scrypt-Hash des Passworts in Env oder Config, und der SSE-Stream entfällt.

**Architecture:** Der Server liest die `.sqrx` der laufenden Runde, die der Recorder ohnehin schreibt, ab einem Zeitpunkt und folgt ihr wie `tail -f` (`GET /api/live/round/<id>?from=…`). Der Browser behandelt das als Replay, dessen Download erst mit dem Rundenende fertig ist. Ein kleiner Hook hält den Abspielkopf 8 s hinter dem neuesten Frame. Login, Sitzungen, Bremse und SIGHUP bleiben aus der Live-Moderation.

**Tech Stack:** Python ≥ 3.10 nur mit Standardbibliothek und dem schon installierten `zstandard`, pytest. React 18, TypeScript, zustand, Vite, Node-Tests über `frontend/scripts/run-tests.mjs`.

**Spec:** `docs/superpowers/specs/2026-10-05-live-replay-design.md` (baut auf `docs/superpowers/specs/2026-09-29-live-moderation-design.md` auf)

## Global Constraints

- **Branch und Commits:**
  - Gearbeitet wird auf `live-moderation`.
  - Commit-Nachrichten sind schlichtes Englisch im Stil des Repos (z. B. „Stream the running round to logged-in moderators“), **ohne** `Co-Authored-By`-Trailer und ohne jede andere KI-Attribution.
  - Pushen macht der Nutzer.
- **Python:** ≥ 3.10, die CI prüft 3.10 und 3.13. Nur Standardbibliothek und das vorhandene `zstandard`, keine neuen Pakete.
- **Frontend:** keine neuen npm-Pakete.
- **Sprache:**
  - Code und Bezeichner sind Englisch, `docs/live-map.md` ist Englisch.
  - UI-Texte sind Deutsch wie bisher: „Moderator-Login“, „Live-Karte“, „● LIVE“, „← Zurück“, „Abmelden“, „Warte auf die nächste Runde…“, „Sitzung abgelaufen – bitte neu anmelden.“
- **`frontend/dist`:** Nach jeder Änderung unter `frontend/src` neu bauen und mitcommitten (`cd frontend && npm run build`).
- **Ohne gültiges Passwort** ist der Server byte-gleich zum öffentlichen Build. `tests/test_public_no_live.py` wird nicht angefasst.
- **Bekannte rote Tests:** genau 4, in `tests/test_custom_capzones.py`. Sie sind schon auf `master` rot.
- **Prüfbefehle:**
  - `.venv/bin/python -m pytest -q -p no:cacheprovider`
  - `.venv/bin/python -m ruff check sqreader tests scripts/live_dev_server.py`
  - `.venv/bin/python -m mypy --python-version 3.12 sqreader` (lokal immer mit `--python-version 3.12`)
  - `cd frontend && npm test`
- **Konstanten:**
  - Server: `POLL_SEC = 0.25`, `MAX_STREAMS = 10`, `WRITE_TIMEOUT_SEC = 20.0`.
  - Client: `LIVE_DELAY_MS = 8000`, `RETRY_MS = 5000`, `RECONNECT_MS = 3000`.
  - Hash: `scrypt:<n>:<r>:<p>:<salt>:<key>` mit base64url ohne Padding. Erzeugt mit n=16384, r=8, p=1, Salt 16 Byte, Key 32 Byte. Akzeptiert werden n als Zweierpotenz mit 2 ≤ n ≤ 2^20, 1 ≤ r ≤ 16, 1 ≤ p ≤ 4 und 128·n·r ≤ 2^28.
- **Env-Variable:** `SQREADER_LIVE_PASSWORD_HASH`. Sie hat Vorrang vor `live_password` und nimmt nur einen Hash.

## Review Focus

1. **Rundenwechsel zwischen `/api/live/round` und dem Stream-Request:** Der Stream antwortet 404, es kommt kein einziger Frame. Der Viewer muss dann die neue Runde öffnen und darf nicht auf der Fehlerkarte hängen bleiben. → Task 4 (`shouldAdvanceRound(0, …)`), Task 5 (Code).
2. **Logout in einem zweiten Tab, während ein langer Rückstand streamt:** Der Stream endet mitten im Rückstand und nicht erst am Dateiende. → Task 3 (`test_a_revoked_session_ends_the_stream_mid_backlog`).
3. **Unsinniges `from`** (`abc`, `-5`, leer): Es wird ab Rundenstart gestreamt, nie mit 500 geantwortet. → Task 3 (`test_a_bogus_from_streams_from_the_start`).
4. **Der Nutzer springt zurück oder pausiert während des Nachpufferns:** Seine Wahl gilt. Kein erzwungenes Pausieren normaler Wiedergabe, kein festhängendes „Puffern“-Banner. → Task 4 (`edgeStep`: „unstall“, „resume“ von weit hinten).
5. **Hash mit Leerzeichen oder Zeilenumbruch am Ende in die `.env` kopiert, oder `""` aus Compose' Voreinstellung:** abgewiesen mit der Leerraum-Regel bzw. als „nicht gesetzt“ behandelt. → Task 2 (`test_the_environment_hash_wins_over_live_password`, Leerraum-Parameter).

## Dateien

| Datei | Verantwortung | Tasks |
|---|---|---|
| `sqreader/config.py` | `config_path()` als einzige Pfadregel | 1 |
| `sqreader/live.py` | Passwort/Hash, Sitzungen, Bremse, SIGHUP, Runden-Endpunkte, Hash-Befehl. SSE fliegt raus. | 1, 2, 3, 6 |
| `sqreader/cli.py` | `live_from_config(…, recordings_dir)`, `live.recording`, ohne `publish`-Hooks | 3, 6 |
| `scripts/live_dev_server.py` | schreibt eine synthetische Runde in eine `.sqrx` | 3, 6 |
| `tests/live_helpers.py` | `Stream` für beliebige Pfade, mit `line()` | 3, 6 |
| `tests/test_live_round.py` (neu) | Runden-Endpunkte | 3 |
| `tests/test_live_access.py`, `tests/test_live_reload.py`, `tests/test_live_http.py` | angepasst | 1, 2, 3, 6 |
| `tests/test_live_hub.py`, `tests/test_live_stream.py` | gelöscht | 6 |
| `frontend/src/api/recordings.ts` | `LIVE_ID_PREFIX`, `LIVE_DELAY_MS`, `isLiveId`, `recordingUrl` | 4 |
| `frontend/src/live/client.ts` | `fetchLiveRound`, `edgeStep`, `isAtLive`, `shouldAdvanceRound`, ohne SSE-Decoder | 4 |
| `frontend/src/live/client.test.mts` | Tests dazu | 4 |
| `frontend/src/live/LiveAccess.tsx` | `goLive`, `useLiveEdge`, Buttons, Wartebanner, Login | 5 |
| `frontend/src/live/live.css` | LIVE-Knopf, Wartebanner | 5 |
| `frontend/src/ui/TimelineBar.tsx`, `frontend/src/ui/TopBar.tsx` | kleine Live-Weichen | 5 |
| `docker-compose.yml`, `.env.example`, `sqreader.config.example.json` | Env-Variable, Kommentare | 2 |
| `docs/live-map.md`, alte Spec, Plan der Live-Moderation | Doku, Aufräumen | 1, 7 |

---

### Task 1: Aufräumen aus dem Ponytail-Review

**Files:**
- Modify: `sqreader/config.py` (`_load`, neue `config_path`)
- Modify: `sqreader/live.py` (Imports, `Access`, `_fmt_duration`, `config_path`, Log-Zeile in `_stream`)
- Modify: `tests/test_live_reload.py` (Test `test_config_path_follows_config_py` raus)
- Delete: `docs/superpowers/plans/2026-09-29-live-moderation.md`

**Interfaces:**
- Produces: `sqreader.config.config_path() -> pathlib.Path`. `live.py` importiert sie als `from .config import config_path`, damit `monkeypatch.setattr(live, "config_path", …)` weiter greift. `Access._sessions: dict[str, float]` speichert Token → monotoner Ablauf. `Access._new_session(now: float) -> str`.

- [ ] **Step 1: `config_path` nach `config.py`**

In `sqreader/config.py` direkt über `def _load()` einfügen:

```python
def config_path() -> Path:
    """Where the config is read from: $SQREADER_CONFIG, else ./sqreader.config.json."""
    env = os.environ.get("SQREADER_CONFIG")
    return Path(env) if env else Path.cwd() / "sqreader.config.json"
```

In `_load()` diese zwei Zeilen

```python
    env = os.environ.get("SQREADER_CONFIG")
    path = Path(env) if env else Path.cwd() / "sqreader.config.json"
```

ersetzen durch

```python
    path = config_path()
```

- [ ] **Step 2: `live.py` nutzt sie**

In `sqreader/live.py` die Funktion `config_path()` samt Docstring löschen (der Block `def config_path() -> Path:` … `return Path(env) if env else Path.cwd() / "sqreader.config.json"`). Dann die Imports `import os` und `from pathlib import Path` entfernen; beide werden sonst nirgends gebraucht. Nach `from typing import Any, Optional` einfügen:

```python
from .config import config_path
```

- [ ] **Step 3: Sitzungen ohne Client-Schlüssel**

In `Access.__init__`:

```python
        # token -> (monotonic expiry, client key)
        self._sessions: dict[str, tuple[float, str]] = {}
```

wird zu

```python
        # token -> monotonic expiry
        self._sessions: dict[str, float] = {}
```

In `Access.login` wird `result = ("ok", self._new_session(client, now))` zu `result = ("ok", self._new_session(now))`.

`Access.valid` wird zu:

```python
    def valid(self, token: str) -> bool:
        now = time.monotonic()
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None:
                return False
            if expires <= now:
                del self._sessions[token]
                return False
            return True
```

`Access._new_session` wird zu:

```python
    def _new_session(self, now: float) -> str:               # caller holds the lock
        self._sessions = {t: e for t, e in self._sessions.items() if e > now}
        while len(self._sessions) >= MAX_SESSIONS:
            del self._sessions[min(self._sessions, key=self._sessions.__getitem__)]
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_TTL_SEC
        return token
```

- [ ] **Step 4: `_fmt_duration` inline**

`def _fmt_duration(...)` samt Körper löschen. In `LiveMap._stream` wird

```python
            log.info("live: stream closed from %s after %s (%s)", client,
                     _fmt_duration(time.monotonic() - started), reason)
```

zu

```python
            log.info("live: stream closed from %s after %dm%02ds (%s)", client,
                     *divmod(int(time.monotonic() - started), 60), reason)
```

- [ ] **Step 5: Drift-Test und alten Plan löschen**

In `tests/test_live_reload.py` die Funktion `test_config_path_follows_config_py` löschen. Es gibt jetzt nur noch eine Pfadregel. Danach ist `from pathlib import Path` dort weiter nötig, `test_cli_wires_the_live_map_into_serve` nutzt es.

```bash
git rm docs/superpowers/plans/2026-09-29-live-moderation.md
```

- [ ] **Step 6: Prüfen**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: genau 4 Fehler, alle in `tests/test_custom_capzones.py`. Die Config-Tests decken `_load` mit ab.

Run: `.venv/bin/python -m ruff check sqreader/live.py sqreader/config.py tests/test_live_reload.py && .venv/bin/python -m mypy --python-version 3.12 sqreader`
Expected: keine Funde.

- [ ] **Step 7: Commit**

```bash
git add sqreader/config.py sqreader/live.py tests/test_live_reload.py
git commit -m "Keep one config path rule, and drop what the live map never reads"
```

---

### Task 2: Passwort als scrypt-Hash, aus der Env, ohne Mindestlänge

**Files:**
- Modify: `sqreader/live.py` (Konstanten, `validate_password`, neue `hash_password`/`parse_hash`/`password_from`/`main`, `Access`, `reload`, `live_from_config`)
- Modify: `tests/test_live_access.py`, `tests/test_live_reload.py`, `tests/test_live_http.py`
- Modify: `docker-compose.yml`, `.env.example`, `sqreader.config.example.json`

**Interfaces:**
- Produces (alle in `sqreader.live`):
  - `ENV_HASH = "SQREADER_LIVE_PASSWORD_HASH"`
  - `hash_password(password: str, salt: Optional[bytes] = None) -> str`
  - `parse_hash(value: str) -> Optional[tuple[int, int, int, bytes, bytes]]`
  - `validate_password(value: Any, *, hash_only: bool = False) -> tuple[Optional[str], Optional[str]]`
  - `password_from(config_value: Any) -> tuple[Optional[str], Optional[str], str]` liefert (Secret, Grund, Quelle), die Quelle ist `ENV_HASH` oder `"live_password"`
  - `main(argv: Optional[list[str]] = None) -> int`
  - Grund-Konstanten ohne Präfix: `_NOT_STR = "must be a string"`, `_PADDED = "has leading or trailing whitespace"`, `_BAD_HASH = "is not a valid scrypt hash"`, `_NOT_HASH = "must be a hash from `python3 -m sqreader.live hash`"`
  - `Access(secret: Optional[str])` und `Access.reset(secret: Optional[str]) -> bool`
  - Log beim Start: `live map enabled for moderators (password from <Quelle>)` bzw. `live map disabled: <Quelle> <Grund>`
- `MIN_PASSWORD_LEN` und `_TOO_SHORT` gibt es danach nicht mehr.

- [ ] **Step 1: Failing tests in `tests/test_live_access.py`**

Den Parametrize-Block und `test_a_long_enough_password_is_accepted` (Zeilen 15–35) ersetzen durch:

```python
@pytest.mark.parametrize("value, reason", [
    (None, None),
    ("", None),
    (12345, live._NOT_STR),
    (" " + PW, live._PADDED),
    (PW + "\n", live._PADDED),
    ("   ", live._PADDED),
    ("scrypt:16384:8:1:short:key", live._BAD_HASH),
])
def test_unusable_passwords_are_rejected_without_echoing_them(value, reason):
    password, why = live.validate_password(value)
    assert password is None
    assert why == reason
    if why is not None and isinstance(value, str) and value.strip():
        assert value.strip() not in why


def test_a_password_of_any_length_is_accepted():
    assert live.validate_password("x") == ("x", None)
    assert live.validate_password(PW) == (PW, None)
    assert live.Access("x").login("x", "c", [])[0] == "ok"


def test_plain_text_is_refused_where_only_a_hash_may_stand():
    assert live.validate_password(PW, hash_only=True) == (None, live._NOT_HASH)
    h = live.hash_password(PW)
    assert live.validate_password(h, hash_only=True) == (h, None)


def test_a_hash_logs_in_with_its_password_only():
    h = live.hash_password(PW)
    assert h.startswith("scrypt:16384:8:1:")
    assert "$" not in h and "=" not in h          # Compose expands `$` in an .env
    assert live.validate_password(h) == (h, None)
    a = live.Access(h)
    assert a.login(WRONG, "c", []) == ("wrong", None)
    assert a.login(PW, "c", [])[0] == "ok"


def test_hashes_are_salted():
    one, two = live.hash_password(PW), live.hash_password(PW)
    assert one != two
    assert live.Access(two).login(PW, "c", [])[0] == "ok"


GOOD = live.hash_password(PW, salt=b"s" * 16)


@pytest.mark.parametrize("value", [
    "scrypt:",
    GOOD.rsplit(":", 1)[0],                            # no key
    GOOD.replace(":16384:", ":16383:"),                # n not a power of two
    GOOD.replace(":16384:", f":{2**21}:"),             # n too large
    GOOD.replace(":16384:8:", f":{2**20}:8:"),         # 128*n*r beyond 256 MiB
    GOOD.replace(":8:1:", ":8:5:"),                    # p too large
    GOOD.replace(":8:1:", ":x:1:"),                    # not a number
    GOOD[:-4],                                         # key too short
])
def test_broken_or_costly_hashes_are_rejected(value):
    assert live.validate_password(value) == (None, live._BAD_HASH)


def test_reset_with_the_same_hash_says_unchanged():
    h = live.hash_password(PW)
    a = live.Access(h)
    assert a.reset(h) is False
    assert a.reset(live.hash_password(PW)) is True     # same password, new salt: new value


def test_the_environment_hash_wins_over_live_password(monkeypatch):
    h = live.hash_password(PW)
    monkeypatch.setenv(live.ENV_HASH, h)
    assert live.password_from("some-other-password") == (h, None, live.ENV_HASH)
    monkeypatch.setenv(live.ENV_HASH, PW)               # plain text there is refused
    assert live.password_from(PW) == (None, live._NOT_HASH, live.ENV_HASH)
    monkeypatch.setenv(live.ENV_HASH, h + "\n")         # pasted with its newline
    assert live.password_from(PW) == (None, live._PADDED, live.ENV_HASH)
    monkeypatch.setenv(live.ENV_HASH, "")               # Compose's ${…:-} when unset
    assert live.password_from(PW) == (PW, None, "live_password")


def test_the_hash_command_prints_a_working_hash(monkeypatch, capsys):
    answers = iter([PW, PW])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert live.main(["hash"]) == 0
    printed = capsys.readouterr().out.strip()
    assert PW not in printed
    assert live.Access(printed).login(PW, "c", [])[0] == "ok"


@pytest.mark.parametrize("first, second", [
    (PW, PW + "x"), ("", ""), (" " + PW, " " + PW), (GOOD, GOOD),
])
def test_the_hash_command_refuses_mismatches_and_unusable_input(monkeypatch, capsys,
                                                                 first, second):
    answers = iter([first, second])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert live.main(["hash"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert PW not in out.err


def test_the_hash_command_wants_its_word(capsys):
    assert live.main([]) == 2
    assert "usage: python3 -m sqreader.live hash" in capsys.readouterr().err
```

- [ ] **Step 2: Failing tests in `tests/test_live_reload.py` und `tests/test_live_http.py`**

In `tests/test_live_reload.py` wird der Parametrize-Block von `test_a_broken_config_fails_closed_and_says_why` zu:

```python
@pytest.mark.parametrize("content, why", [
    ("{not json", "JSONDecodeError"),
    (json.dumps({"live_password": " padded "}),
     "live_password has leading or trailing whitespace"),
    (json.dumps({"live_password": "scrypt:nope"}), "live_password is not a valid scrypt hash"),
    (json.dumps({}), "live_password is not set"),
    (json.dumps(["x"]), "live_password is not set"),
])
```

Dazu ein neuer Test direkt danach:

```python
def test_sighup_with_an_environment_hash_revokes_and_keeps_it(monkeypatch, caplog):
    h = live.hash_password(PW)
    monkeypatch.setenv(live.ENV_HASH, h)
    monkeypatch.setenv("SQREADER_CONFIG", "/nonexistent/never-read.json")
    lm = live.LiveMap(h)
    _, token = lm.access.login(PW, "c", [])
    lm.reload()
    assert not lm.access.valid(token)
    assert lm.access.login(PW, "c", [])[0] == "ok"
    assert _warning(f"live: SIGHUP: all sessions revoked; password from {live.ENV_HASH} is "
                    "UNCHANGED (environment: change it with a restart between rounds)"
                    ) in caplog.record_tuples
```

In `tests/test_live_http.py` wird `test_invalid_config_disables_live_and_never_logs_the_value` zu:

```python
def test_invalid_config_disables_live_and_never_logs_the_value(caplog, monkeypatch):
    monkeypatch.delenv(live.ENV_HASH, raising=False)
    caplog.set_level("INFO", logger="sqreader.live")
    assert live.live_from_config(None) is None
    assert caplog.text == ""
    assert live.live_from_config(" padded-secret ") is None
    assert "live map disabled: live_password has leading or trailing whitespace" in caplog.text
    assert "padded-secret" not in caplog.text
    assert isinstance(live.live_from_config(PW), live.LiveMap)
    assert "live map enabled for moderators (password from live_password)" in caplog.text
    monkeypatch.setenv(live.ENV_HASH, "plain-secret-in-the-env")
    assert live.live_from_config(PW) is None
    assert f"live map disabled: {live.ENV_HASH} must be a hash" in caplog.text
    assert "plain-secret-in-the-env" not in caplog.text
```

- [ ] **Step 3: Tests laufen lassen, sie schlagen fehl**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_access.py tests/test_live_reload.py tests/test_live_http.py`
Expected: FAIL. Schon beim Sammeln gibt es `AttributeError: module 'sqreader.live' has no attribute 'hash_password'`.

- [ ] **Step 4: Implementierung in `sqreader/live.py`**

Imports ergänzen (alphabetisch einsortieren): `import base64`, `import os`, `import sys`.

`MIN_PASSWORD_LEN = 20` löschen. Die Blöcke `_TOO_SHORT = …` / `_PADDED = …` und die ganze Funktion `validate_password` ersetzen durch:

```python
ENV_HASH = "SQREADER_LIVE_PASSWORD_HASH"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

# Reasons a value is unusable. They never contain the value, which is a
# credential; the caller puts the name of where it came from in front.
_NOT_STR = "must be a string"
_PADDED = "has leading or trailing whitespace"
_BAD_HASH = "is not a valid scrypt hash"
_NOT_HASH = "must be a hash from `python3 -m sqreader.live hash`"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """scrypt:<n>:<r>:<p>:<salt>:<key>, base64url without padding.

    No `$` anywhere: Compose expands it in an .env file.
    """
    salt = secrets.token_bytes(16) if salt is None else salt
    key = hashlib.scrypt(password.encode("utf-8", "surrogatepass"), salt=salt,
                         n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{_b64(salt)}:{_b64(key)}"


def parse_hash(value: str) -> Optional[tuple[int, int, int, bytes, bytes]]:
    """(n, r, p, salt, key) of a well-formed hash at a sane cost, else None.

    The cost caps keep a typo from asking scrypt for gigabytes at every login.
    """
    parts = value.split(":")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = (int(x) for x in parts[1:4])
        salt, key = _unb64(parts[4]), _unb64(parts[5])
    except ValueError:                    # binascii.Error is a ValueError
        return None
    if not (2 <= n <= 2**20 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4
            and 128 * n * r <= 2**28 and len(salt) >= 16 and len(key) == 32):
        return None
    return n, r, p, salt, key


def validate_password(value: Any, *,
                      hash_only: bool = False) -> tuple[Optional[str], Optional[str]]:
    """(secret, None) if usable, (None, reason) if not, (None, None) if unset.

    The secret is a scrypt hash, or a plain password of any length unless
    `hash_only`. The reason never contains the value: it is a credential.
    """
    if value is None or value == "":
        return None, None
    if not isinstance(value, str):
        return None, _NOT_STR
    if value != value.strip():
        return None, _PADDED
    if value.startswith("scrypt:"):
        return (value, None) if parse_hash(value) else (None, _BAD_HASH)
    if hash_only:
        return None, _NOT_HASH
    return value, None


def password_from(config_value: Any) -> tuple[Optional[str], Optional[str], str]:
    """(secret, reason, source): a non-empty SQREADER_LIVE_PASSWORD_HASH wins over
    live_password, and only a hash may stand there."""
    env = os.environ.get(ENV_HASH)
    if env:
        secret, reason = validate_password(env, hash_only=True)
        return secret, reason, ENV_HASH
    secret, reason = validate_password(config_value)
    return secret, reason, "live_password"
```

In `class Access` wird der Docstring zu `"""The shared password (or its hash) and the sessions it grants. One lock guards both."""`. `__init__` beginnt mit

```python
    def __init__(self, secret: Optional[str]) -> None:
        self._lock = threading.Lock()
        self._secret = secret
```

statt der `_digest`-Zeile. Den Rest von `__init__` unverändert lassen.

`reset` wird zu:

```python
    def reset(self, secret: Optional[str]) -> bool:
        """A new secret (None: nobody can log in) and no sessions at all.

        True if that changed the stored secret, "no password" counting as a
        value of its own. The sessions are gone either way.
        """
        with self._lock:
            changed = secret != self._secret
            self._secret = secret
            self._sessions.clear()
        return changed
```

`_check` wird zu:

```python
    def _check(self, given: str) -> bool:                     # caller holds the lock
        # scrypt under the lock (~50 ms) serialises logins, which together with
        # the throttle also bounds what a flood of them costs.
        if self._secret is None:
            return False
        parsed = parse_hash(self._secret) if self._secret.startswith("scrypt:") else None
        if parsed is None:
            return hmac.compare_digest(_digest(given), _digest(self._secret))
        n, r, p, salt, key = parsed
        got = hashlib.scrypt(given.encode("utf-8", "surrogatepass"), salt=salt,
                             n=n, r=r, p=p, maxmem=2**29, dklen=len(key))
        return hmac.compare_digest(got, key)
```

In `LiveMap.reload` den Docstring und den Anfang bis zur Zeile `changed = self.access.reset(password)` ersetzen durch:

```python
    def reload(self) -> None:
        """Revoke every session and re-read the password. Fails closed: a file
        that cannot be read, or holds no valid password, disables logins.

        A hash in SQREADER_LIVE_PASSWORD_HASH wins, as at startup. The
        environment of a running process never changes, so then this only
        revokes, and says so. Otherwise the log says which of three things
        happened. A file that still holds the old password (a Docker
        single-file bind mount keeps serving the old inode after an editor
        replaced the file) is reported as UNCHANGED, and a failure names its
        reason: an error TYPE or a rule, never a message, which could quote the
        file, and never the value.
        """
        env = os.environ.get(ENV_HASH)
        if env:
            self.access.reset(validate_password(env, hash_only=True)[0])
            self.hub.kick()
            log.warning("live: SIGHUP: all sessions revoked; password from %s is UNCHANGED "
                        "(environment: change it with a restart between rounds)", ENV_HASH)
            return
        path = None
        why = None
        try:
            # Inside the try: Path.cwd() raises when the process's cwd was deleted
            # (a directory-swap deploy), and that must still revoke everyone.
            path = config_path()
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("live_password") if isinstance(data, dict) else None
        except Exception as exc:
            value = None
            why = type(exc).__name__
        password, reason = validate_password(value)
        if password is None and why is None:
            why = f"live_password {reason}" if reason else "live_password is not set"
        changed = self.access.reset(password)
```

Der Rest von `reload` (`self.hub.kick()` und die drei `log.warning`-Zweige) bleibt.

`live_from_config` wird zu:

```python
def live_from_config(value: Any) -> Optional[LiveMap]:
    """The live map for this config value (or the environment's hash), or None,
    and then it does not exist."""
    secret, reason, source = password_from(value)
    if secret is None:
        if reason:
            log.warning("live map disabled: %s %s", source, reason)
        return None
    log.info("live map enabled for moderators (password from %s)", source)
    return LiveMap(secret)
```

Ans Dateiende:

```python
def main(argv: Optional[list[str]] = None) -> int:
    """`python3 -m sqreader.live hash`: print the hash for SQREADER_LIVE_PASSWORD_HASH
    (or live_password). Asks twice, never echoes, never prints the password."""
    import getpass
    args = sys.argv[1:] if argv is None else argv
    if args != ["hash"]:
        print("usage: python3 -m sqreader.live hash", file=sys.stderr)
        return 2
    first = getpass.getpass("live map password: ")
    if getpass.getpass("again: ") != first:
        print("the two entries differ", file=sys.stderr)
        return 1
    if not first or first != first.strip() or first.startswith("scrypt:"):
        print("unusable: empty, padded with whitespace, or a hash already", file=sys.stderr)
        return 1
    print(hash_password(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Tests laufen lassen, sie sind grün**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_access.py tests/test_live_reload.py tests/test_live_http.py tests/test_live_stream.py tests/test_live_hub.py`
Expected: PASS.

- [ ] **Step 6: Docker und Beispiel-Config**

In `docker-compose.yml` unter `environment:` direkt nach `SQREADER_CONFIG: ${SQREADER_CONFIG:-}` einfügen:

```yaml
      # Moderator live map: a HASH from `python3 -m sqreader.live hash` (docs/live-map.md).
      SQREADER_LIVE_PASSWORD_HASH: ${SQREADER_LIVE_PASSWORD_HASH:-}
```

In `.env.example` direkt nach dem Block, der mit `# SQREADER_CONFIG=/app/sqreader.config.json` endet, einfügen:

```
# Moderator live map (docs/live-map.md). OFF unless a password is set. Put the
# HASH here, never the password: anyone who can run `docker inspect` reads the
# environment. Make one with
#     docker compose run --rm --no-deps --entrypoint python3 sqreader -m sqreader.live hash
# A new value takes `docker compose up -d`, between two rounds.
# SQREADER_LIVE_PASSWORD_HASH=scrypt:16384:8:1:...
```

In `sqreader.config.example.json` wird `_live_comment` zu:

```json
  "_live_comment": [
    "Moderator-only LIVE map. OFF unless live_password (or SQREADER_LIVE_PASSWORD_HASH) is set.",
    "Whoever knows it sees BOTH teams in real time: give it to moderators only.",
    "Best a hash from `python3 -m sqreader.live hash` (a line starting with scrypt:);",
    "a plain-text password works too. Either way it is a CREDENTIAL: chmod 600 this file.",
    "Moderators log in from the start page, or at https://<site>/?mode=live (HTTPS required).",
    "Change it or kick everyone out without a restart: edit it, then send SIGHUP.",
    "Under Docker, edit this file IN PLACE (a replaced file is not seen); see docs/live-map.md.",
    "Needs --recordings-dir: the live map plays the round being recorded."
  ],
```

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_reload.py::test_the_example_config_documents_live_password && python3 -c "import json; json.load(open('sqreader.config.example.json'))"`
Expected: PASS und keine Ausgabe.

- [ ] **Step 7: Lint, Typen, Commit**

Run: `.venv/bin/python -m ruff check sqreader/live.py tests/test_live_access.py tests/test_live_reload.py tests/test_live_http.py && .venv/bin/python -m mypy --python-version 3.12 sqreader`
Expected: keine Funde.

```bash
git add sqreader/live.py tests/test_live_access.py tests/test_live_reload.py tests/test_live_http.py docker-compose.yml .env.example sqreader.config.example.json
git commit -m "Accept a scrypt hash of the live password, from the environment too"
```

---

### Task 3: Die laufende Runde als Stream (Server, Dev-Server)

**Files:**
- Modify: `sqreader/live.py` (Imports, `POLL_SEC`, `round_meta`, `_Tail`, `LiveMap.__init__`, `handle_get`, `_round`, `_follow`, `_admit`, `_release`, `live_from_config`)
- Modify: `sqreader/cli.py:937-939` (Live-Erzeugung) und direkt nach dem `record_state_box`-Dict (≈ Zeile 1052)
- Modify: `tests/live_helpers.py` (`Stream` mit Pfad und `line()`)
- Create: `tests/test_live_round.py`
- Modify: `tests/test_live_reload.py` (Merge-Guard), `tests/test_live_http.py` (Signatur, Pfadliste)
- Modify: `scripts/live_dev_server.py`

**Interfaces:**
- Consumes: `password_from`, `ENV_HASH` aus Task 2.
- Produces:
  - `LiveMap.recording: Callable[[], Any]`, Voreinstellung `lambda: None`. Liefert einen `sqreader.recorder.RecordingState` (genutzt: `.path`, `.first_snap_ts`, `.last_snap_ts`, `.started_at`) oder None.
  - `live_from_config(value: Any, recordings_dir: Any) -> Optional[LiveMap]`, `recordings_dir` ist Pflicht.
  - `round_meta(state) -> dict` mit den Schlüsseln `id`, `startedAtUtc`, `latestUtc`, `durationSec`.
  - Endpunkte `GET /api/live/round`, `GET /api/live/round/<id>/meta`, `GET /api/live/round/<id>?from=<ms>`.
  - Log-Zeilen `live: round stream opened from <ip> (<n>/10)` und `live: round stream closed from <ip> after <m>m<ss>s (<reason>)`, mit `<reason>` ∈ {`round ended`, `session ended`, `client gone`, `write timeout`, `bad data`}.
  - In `tests/live_helpers.py`: `Stream(port, token, path="/api/live/stream", rcvbuf=None, headers="")` mit `.head()`, `.line(timeout=5)`, `.event()`, `.closed_within(s)`, `.close()`, `.buf`, `.s`.

- [ ] **Step 1: `Stream` in `tests/live_helpers.py` verallgemeinern**

`class Stream` wird im Kopf zu:

```python
class Stream:
    """One open GET on a streaming endpoint, read incrementally from a raw socket."""

    def __init__(self, port, token, path="/api/live/stream", rcvbuf=None, headers=""):
        self.s = socket.socket()
        if rcvbuf:
            self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.s.settimeout(5)
        self.s.connect(("127.0.0.1", port))
        hdr = f"Cookie: {live.COOKIE}={token}\r\n" if token else ""
        self.s.sendall(f"GET {path} HTTP/1.1\r\nHost: t\r\n{hdr}{headers}\r\n".encode())
        self.buf = b""
```

Nach `def event(...)` einfügen:

```python
    def line(self, timeout=5):
        return self._until(b"\n", timeout)
```

- [ ] **Step 2: Failing tests `tests/test_live_round.py`**

```python
"""The running round, streamed from its growing .sqrx to logged-in moderators:
GET /api/live/round[/<id>[/meta]] (sqreader/live.py)."""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone

import pytest
import zstandard as zstd

from live_helpers import PW, Stream, cookie, login, request, running, wait_for
from sqreader import live
from sqreader.recorder import RecordingState
from sqreader.sqrx import SqrxWriter

STEM = "2026-10-05_120000_Gorodok_RAAS_v1_abcd1234"


def frame(i, pad=0):
    return json.dumps({"timestamp": f"2026-10-05T12:00:{i:02d}+00:00", "tick": i,
                       "players": [], "pad": "x" * pad})


def pos(i):
    return json.dumps({"t": "pos", "tick": i, "timestamp": f"2026-10-05T12:00:{i:02d}.5+00:00",
                       "players": [], "vehicles": []})


class Round:
    """A recording in progress, written and announced the way recorder.py does."""

    def __init__(self, tmp_path):
        path = tmp_path / f"{STEM}.sqrx"
        self.state = RecordingState(match_id="m1", writer=SqrxWriter(path, "srv"), path=path,
                                    started_at=datetime(2026, 10, 5, 11, 59, tzinfo=timezone.utc))
        self.box = {"current": self.state}

    def write(self, line):
        self.state.writer.write_line(line)
        if not line.startswith('{"t": "pos"'):
            ts = json.loads(line)["timestamp"]
            self.state.first_snap_ts = self.state.first_snap_ts or ts
            self.state.last_snap_ts = ts

    def end(self):
        self.box["current"] = None
        self.state.writer.close()


@pytest.fixture
def setup(tmp_path):
    rnd = Round(tmp_path)
    lm = live.LiveMap(PW)
    lm.recording = lambda: rnd.box["current"]
    with running(lm) as port:
        _, token, _ = login(port)
        try:
            yield port, token, rnd, lm
        finally:
            rnd.end()            # every open stream ends within POLL_SEC


def _open(port, token, query=""):
    return Stream(port, token, f"/api/live/round/{STEM}{query}")


# -- who may ask, and what about ------------------------------------------------

@pytest.mark.parametrize("path", ["/api/live/round", f"/api/live/round/{STEM}",
                                  f"/api/live/round/{STEM}/meta", "/api/live/round/x/y"])
def test_every_round_path_asks_for_a_session_first(path):
    with running(live.LiveMap(PW)) as port:          # no round either: 401 must not tell
        st, hdrs, body = request(port, "GET", path)
    assert (st, json.loads(body)) == (401, {"error": "not logged in"})
    assert hdrs["Cache-Control"] == "no-store"


def test_without_a_round_everything_is_404():
    with running(live.LiveMap(PW)) as port:
        _, token, _ = login(port)
        for path in ("/api/live/round", f"/api/live/round/{STEM}",
                     f"/api/live/round/{STEM}/meta"):
            assert request(port, "GET", path, headers=cookie(token))[0] == 404, path


def test_meta_describes_the_running_round(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    rnd.write(pos(1))
    rnd.write(frame(3))
    want = {"id": STEM, "startedAtUtc": "2026-10-05T12:00:01+00:00",
            "latestUtc": "2026-10-05T12:00:03+00:00", "durationSec": 0}
    for path in ("/api/live/round", f"/api/live/round/{STEM}/meta"):
        st, hdrs, body = request(port, "GET", path, headers=cookie(token))
        assert (st, json.loads(body)) == (200, want), path
        assert hdrs["Cache-Control"] == "no-store"
        assert hdrs.get("Access-Control-Allow-Origin") is None
    for path in ("/api/live/round/other/meta", "/api/live/round/other",
                 f"/api/live/round/{STEM}/x", "/api/live/roundabout"):
        assert request(port, "GET", path, headers=cookie(token))[0] == 404, path


def test_meta_before_the_first_frame_falls_back_to_the_start(setup):
    port, token, _, _ = setup
    _, _, body = request(port, "GET", "/api/live/round", headers=cookie(token))
    m = json.loads(body)
    assert m["startedAtUtc"] == m["latestUtc"] == "2026-10-05T11:59:00+00:00"


def test_live_needs_recordings(caplog, tmp_path, monkeypatch):
    monkeypatch.delenv(live.ENV_HASH, raising=False)
    assert live.live_from_config(PW, None) is None
    assert "live map disabled: it needs --recordings-dir" in caplog.text
    assert isinstance(live.live_from_config(PW, tmp_path), live.LiveMap)


# -- the stream -----------------------------------------------------------------

def test_the_stream_sends_what_exists_then_follows(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    rnd.write(pos(1))
    st = _open(port, token)
    try:
        head = st.head()
        assert head.startswith(b"HTTP/1.0 200 ")
        for h in (b"Content-Type: application/x-ndjson", b"Cache-Control: no-store",
                  b"X-Accel-Buffering: no"):
            assert h in head, h
        assert [json.loads(st.line())["tick"] for _ in range(2)] == [1, 1]
        rnd.write(frame(2))
        assert json.loads(st.line())["tick"] == 2
    finally:
        st.close()


def test_a_frame_written_in_two_halves_arrives_whole(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        raw = zstd.ZstdCompressor().compress((frame(2) + "\n").encode())
        with open(rnd.state.path, "ab") as f:
            f.write(raw[: len(raw) // 2])
            f.flush()
            with pytest.raises(TimeoutError):
                st.line(timeout=0.6)
            f.write(raw[len(raw) // 2:])
        assert json.loads(st.line())["tick"] == 2
    finally:
        st.close()


def test_from_starts_at_the_first_full_frame_at_or_after_it(setup):
    port, token, rnd, _ = setup
    for i in range(1, 6):
        rnd.write(frame(i))
        rnd.write(pos(i))
    from_ms = int(datetime(2026, 10, 5, 12, 0, 2, 500000, tzinfo=timezone.utc).timestamp() * 1000)
    st = _open(port, token, f"?from={from_ms}")
    try:
        st.head()
        first = json.loads(st.line())
        assert (first["tick"], first.get("t")) == (3, None)
    finally:
        st.close()


@pytest.mark.parametrize("query", ["?from=abc", "?from=-5", "?from="])
def test_a_bogus_from_streams_from_the_start(setup, query):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token, query)
    try:
        assert st.head().startswith(b"HTTP/1.0 200 ")
        assert json.loads(st.line())["tick"] == 1
    finally:
        st.close()


def test_the_stream_ends_with_the_round_and_keeps_its_last_frame(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        rnd.write(frame(2))
        rnd.end()
        assert json.loads(st.line())["tick"] == 2
        assert st.closed_within(2)
    finally:
        st.close()


@pytest.mark.parametrize("revoke", ["logout", "reset"])
def test_a_revoked_session_ends_the_stream(setup, revoke):
    port, token, rnd, lm = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        if revoke == "logout":
            request(port, "POST", "/api/live/logout", {}, cookie(token))
        else:
            lm.access.reset(PW)
        assert st.closed_within(1)
    finally:
        st.close()


def test_a_revoked_session_ends_the_stream_mid_backlog(setup):
    """The backlog of a long round streams without ever reaching the end of the
    file, so the session is checked per line, not only while waiting there."""
    port, token, rnd, _ = setup
    for _ in range(400):
        rnd.write(frame(1, pad=20_000))           # ~8 MB, beyond any socket buffer
    st = Stream(port, token, f"/api/live/round/{STEM}", rcvbuf=4096)
    try:
        st.head()
        st.line()
        request(port, "POST", "/api/live/logout", {}, cookie(token))
        assert st.closed_within(5)
        assert st.buf.count(b"\n") < 399          # cut short, not the whole backlog
    finally:
        st.close()


def test_the_stream_limit_answers_503_and_gives_slots_back(setup, monkeypatch):
    port, token, rnd, lm = setup
    monkeypatch.setattr(live, "MAX_STREAMS", 2)
    rnd.write(frame(1))
    streams = [_open(port, token) for _ in range(2)]
    try:
        for st in streams:
            st.head()
        code, hdrs, body = request(port, "GET", f"/api/live/round/{STEM}", headers=cookie(token))
        assert (code, hdrs["Retry-After"], json.loads(body)) == (
            503, "30", {"error": "too many live viewers"})
    finally:
        for st in streams:
            st.close()
    # A gone viewer is noticed at the next frame written to it.
    assert wait_for(lambda: (rnd.write(frame(2)), lm._streams == 0)[1], timeout=5)


def test_gzip_carries_the_same_lines_and_flushes_each(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    rnd.write(pos(1))
    st = Stream(port, token, f"/api/live/round/{STEM}", headers="Accept-Encoding: gzip\r\n")
    try:
        assert b"Content-Encoding: gzip" in st.head()
        d = zlib.decompressobj(31)
        out = d.decompress(st.buf)
        st.s.settimeout(2)
        while out.count(b"\n") < 2:               # before the round ends: flushed per line
            out += d.decompress(st.s.recv(65536))
        rnd.end()
        st.buf = b""
        assert st.closed_within(2)
        out += d.decompress(st.buf) + d.flush()
        assert d.eof                               # a complete gzip stream
        assert [json.loads(x)["tick"] for x in out.decode().splitlines()] == [1, 1]
    finally:
        st.close()


def test_the_stream_is_logged_without_the_token(setup, caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        rnd.end()
        assert st.closed_within(2)
    finally:
        st.close()
    assert wait_for(lambda: "live: round stream closed from 127.0.0.1 after 0m00s "
                            "(round ended)" in caplog.text)
    assert "live: round stream opened from 127.0.0.1 (1/10)" in caplog.text
    assert token not in caplog.text
```

- [ ] **Step 3: Tests laufen lassen, sie schlagen fehl**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_round.py`
Expected: FAIL. Mit Sitzung kommen 404 (Route fehlt) statt 200, und `live_from_config(PW, None)` gibt `TypeError`.

- [ ] **Step 4: Implementierung in `sqreader/live.py`**

Imports ergänzen und einsortieren: `import urllib.parse`, `import zlib`, `from collections.abc import Callable`, `import zstandard as zstd`. Unter `from .config import config_path` kommen:

```python
from .httpsrv import _replay_from
from .sqrx import SqrxReader
```

Bei den Konstanten nach `WRITE_TIMEOUT_SEC` einfügen:

```python
POLL_SEC = 0.25                   # at the end of a round's file: look again after this
```

Vor `class LiveMap` einfügen:

```python
def round_meta(state: Any) -> dict:
    """What the viewer needs to draw the timeline of the round being recorded."""
    started = state.first_snap_ts or state.started_at.isoformat()
    return {"id": state.path.stem, "startedAtUtc": started,
            "latestUtc": state.last_snap_ts or started, "durationSec": 0}


class _Tail:
    """The round's recording, read the way `tail -f` reads a file.

    zstd's stream reader pulls its bytes from here. At the end of the file this
    waits for the recorder's next frame instead of reporting the end, so a
    frame the recorder is halfway through writing is simply finished on a
    later poll. Only once `alive()` says the round is over, or the viewer may
    no longer watch, does it report the end, after one last read for a frame
    written in between.
    """

    def __init__(self, f: Any, alive: Callable[[], bool]) -> None:
        self._f = f
        self._alive = alive

    def read(self, n: int = -1) -> bytes:
        while True:
            data = self._f.read(n)
            if data:
                return data
            if not self._alive():
                return self._f.read(n)
            time.sleep(POLL_SEC)

    def close(self) -> None:
        self._f.close()
```

`LiveMap.__init__` wird zu (die `hub`-Zeile bleibt bis Task 6):

```python
    def __init__(self, password: str) -> None:
        self.hub = Hub()
        self.access = Access(password)
        # The round being recorded (recorder.RecordingState) or None; cli.py
        # points this at record_state_box["current"].
        self.recording: Callable[[], Any] = lambda: None
        self._streams_lock = threading.Lock()
        self._streams = 0
        self._refused_lock = threading.Lock()
        self._refused_at = float("-inf")      # monotonic time of the last refusal log
```

In `handle_get` vor dem `else:` einfügen:

```python
        elif path == "/api/live/round" or path.startswith("/api/live/round/"):
            self._round(h, path[len("/api/live/round/"):])
```

In `LiveMap` nach `_logout` einfügen:

```python
    def _round(self, h: Any, rest: str) -> None:
        """GET /api/live/round[/<id>[/meta]]. The session comes first, so a
        stranger cannot even tell whether a round is being recorded."""
        token = self.access.first_valid(cookie_values(h.headers))
        if token is None:
            _json(h, 401, {"error": "not logged in"})
            return
        state = self.recording()
        rid, _, tail = rest.partition("/")
        # The id is only ever compared, never used to find a file: there is one
        # round to serve, and it is the recorder's.
        if state is None or (rest and (urllib.parse.unquote(rid) != state.path.stem
                                       or tail not in ("", "meta"))):
            _json(h, 404, {"error": "no round in progress"})
        elif not rest or tail == "meta":
            _json(h, 200, round_meta(state))
        else:
            self._follow(h, token, state)

    def _admit(self) -> Optional[int]:
        """Take a stream slot: how many are open now, or None if all are taken."""
        with self._streams_lock:
            if self._streams >= MAX_STREAMS:
                return None
            self._streams += 1
            return self._streams

    def _release(self) -> None:
        with self._streams_lock:
            self._streams -= 1

    def _follow(self, h: Any, token: str, state: Any) -> None:
        """The round's frames from `from` on, then each new one as the recorder
        appends it, until the round ends or the session does."""
        client = client_key(h.headers, h.client_address[0])
        try:
            reader = SqrxReader(state.path)
        except (OSError, ValueError):             # gone between the lookup and here
            _json(h, 404, {"error": "no round in progress"})
            return
        n = self._admit()
        if n is None:
            reader.close()
            self._log_refused(client)
            _json(h, 503, {"error": "too many live viewers"}, {"Retry-After": "30"})
            return
        try:
            from_ms = int((h._query().get("from") or ["0"])[0])
        except ValueError:
            from_ms = 0
        # Never zstd passthrough: the file is still growing, and `from` drops lines.
        gz = (zlib.compressobj(6, zlib.DEFLATED, 31)
              if h._negotiate_encoding(allow_zstd=False) == "gzip" else None)
        reason = "round ended"

        def alive() -> bool:
            nonlocal reason
            if not self.access.valid(token):
                reason = "session ended"
                return False
            return self.recording() is state

        log.info("live: round stream opened from %s (%d/%d)", client, n, MAX_STREAMS)
        started = time.monotonic()
        try:
            h.connection.settimeout(WRITE_TIMEOUT_SEC)
            # HTTP/1.0 (the handler's default, never switched here): the body is
            # delimited by the close, so neither Content-Length nor chunking.
            h.send_response(200)
            h.send_header("Content-Type", "application/x-ndjson")
            h.send_header("Cache-Control", "no-store")
            h.send_header("X-Accel-Buffering", "no")
            h.send_header("Connection", "close")
            if gz:
                h.send_header("Content-Encoding", "gzip")
            h.end_headers()
            reader._f = _Tail(reader._f, alive)  # type: ignore[assignment]  # read/close suffice
            lines = reader.lines()
            for line in _replay_from(lines, from_ms) if from_ms > 0 else lines:
                # Per line too: a long backlog never reaches the end of the file,
                # where _Tail looks, and a logout must not wait for it.
                if not self.access.valid(token):
                    reason = "session ended"
                    break
                data = line.encode("utf-8") + b"\n"
                h.wfile.write(gz.compress(data) + gz.flush(zlib.Z_SYNC_FLUSH) if gz else data)
            if gz:
                h.wfile.write(gz.flush())
        except TimeoutError:
            reason = "write timeout"
        except OSError:
            reason = "client gone"
        except zstd.ZstdError:
            reason = "bad data"
        finally:
            reader.close()
            self._release()
            h.close_connection = True
            log.info("live: round stream closed from %s after %dm%02ds (%s)", client,
                     *divmod(int(time.monotonic() - started), 60), reason)
```

`live_from_config` wird zu:

```python
def live_from_config(value: Any, recordings_dir: Any) -> Optional[LiveMap]:
    """The live map for this config value (or the environment's hash), or None,
    and then it does not exist. It plays the round being recorded, so without
    recordings there is nothing to show."""
    secret, reason, source = password_from(value)
    if secret is None:
        if reason:
            log.warning("live map disabled: %s %s", source, reason)
        return None
    if recordings_dir is None:
        log.warning("live map disabled: it needs --recordings-dir")
        return None
    log.info("live map enabled for moderators (password from %s)", source)
    return LiveMap(secret)
```

- [ ] **Step 5: `cli.py` verdrahten**

In `sqreader/cli.py`:

```python
    # Fork-only moderator live map: None (absent) unless live_password is set.
    from .live import live_from_config
    live = live_from_config(config.get("live_password"))
```

wird zu

```python
    # Fork-only moderator live map: None (absent) unless a live password is set
    # and rounds are recorded: it plays the round being recorded.
    from .live import live_from_config
    live = live_from_config(config.get("live_password"), recordings_dir)
```

Direkt nach dem schließenden `}` des `record_state_box: dict = {…}` und vor `record_filename_buffer: list = []` einfügen:

```python
    if live is not None:
        live.recording = lambda: record_state_box["current"]
```

- [ ] **Step 6: Bestehende Tests nachziehen**

In `tests/test_live_http.py`, `test_invalid_config_disables_live_and_never_logs_the_value`: Die Signatur bekommt `tmp_path`, also `def test_invalid_config_disables_live_and_never_logs_the_value(caplog, monkeypatch, tmp_path):`. Alle vier `live.live_from_config(X)` werden zu `live.live_from_config(X, tmp_path)`.

In `test_without_a_password_the_live_paths_are_any_unknown_path` wird die Pfadliste zu `("/api/live/session", "/api/live/stream", "/api/live/login", "/api/live/round")`.

In `tests/test_live_reload.py` wird `test_cli_wires_the_live_map_into_serve` zu:

```python
def test_cli_wires_the_live_map_into_serve():
    """Merge guard: upstream edits cmd_serve often, and a merge that drops one
    of these lines leaves the live map silently dead or silently frozen."""
    src = (Path(sqreader.__file__).parent / "cli.py").read_text(encoding="utf-8")
    uses = ("live.publish(line, full=True)",
            "live.publish(pos_line, full=False)",
            'live.recording = lambda: record_state_box["current"]',
            "signal.signal(signal.SIGHUP, live.on_sighup)")
    for needle in ('live = live_from_config(config.get("live_password"), recordings_dir)',
                   "live=live", *uses):
        assert needle in src, needle
    # A bare `live.<x>` would raise AttributeError in the public build, where
    # live is None: each use must stay behind its guard.
    for use in uses:
        assert re.search(rf"if live is not None:\s*{re.escape(use)}", src), \
            f"{use} lost its `if live is not None:` guard"
```

- [ ] **Step 7: Tests laufen lassen, sie sind grün**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_round.py tests/test_live_http.py tests/test_live_reload.py tests/test_live_stream.py tests/test_live_hub.py tests/test_live_access.py tests/test_public_no_live.py`
Expected: PASS.

- [ ] **Step 8: Dev-Server schreibt eine Runde**

`scripts/live_dev_server.py` komplett ersetzen durch:

```python
"""Serve the viewer with a fake live round; no Squad server needed.

    .venv/bin/python scripts/live_dev_server.py                  # synthetic players
    .venv/bin/python scripts/live_dev_server.py --sqrx R         # replay a recording as live
    .venv/bin/python scripts/live_dev_server.py --round-sec 120  # a new round every 2 min
    .venv/bin/python scripts/live_dev_server.py --no-live        # the public build, to compare

Then open http://localhost:8090/, click Moderator-Login and log in with the
printed password. It serves frontend/dist, icons and sqmaps the way
`sqreader serve` does, and writes the round into a temporary .sqrx the way the
recorder does: a full frame every 2 s plus 4 Hz position frames. With
--round-sec the round ends after that many seconds and the next one starts
10 s later. SIGHUP exercises the revoke path: the password is re-read from
$SQREADER_CONFIG, which this script points at a temporary file holding
--password unless it is set already.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqreader.httpsrv import _TickBeat, serve_in_background   # noqa: E402
from sqreader.live import LiveMap                             # noqa: E402
from sqreader.recorder import RecordingState                  # noqa: E402
from sqreader.sqrx import SqrxReader, SqrxWriter              # noqa: E402

DEV_PASSWORD = "dev-password-for-the-live-map"
PLAYERS = 20
RADIUS = 150_000.0      # UE units; Gorodok spans about +-203 000
BETWEEN_ROUNDS_SEC = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positions(t: float) -> list[tuple[str, float, float]]:
    out = []
    for i in range(PLAYERS):
        a = t / 20.0 + i * math.tau / PLAYERS
        r = RADIUS * (0.3 + 0.035 * i)
        out.append((f"eos-{i:02d}", r * math.cos(a), r * math.sin(a)))
    return out


def full_frame(tick: int, t: float) -> dict:
    players = [{
        "name": f"Player{i:02d}", "eosId": eos, "playerId": i, "teamId": 1 + i % 2,
        "roleId": None, "score": 0, "ping": 30, "isBot": False, "clanTag": None,
        "squadStateAddr": None, "teamStateAddr": None, "stats": {"kills": 0, "deaths": 0},
        "soldier": {"addr": f"0x{i + 1:x}", "classShort": None, "health": 100.0,
                    "breathHoldStamina": None, "stance": "standing", "yaw": 0.0,
                    "attached": False, "position": {"x": x, "y": y, "z": 0.0}},
    } for i, (eos, x, y) in enumerate(_positions(t))]
    return {
        "timestamp": _now(), "server": "live-dev", "schemaVersion": "1", "tick": tick,
        "counts": {"playerStatesNonCDO": PLAYERS, "soldiersLive": PLAYERS,
                   "vehicleSeatsLive": 0, "totalUObjects": 0},
        "gameState": {"serverName": "live dev server", "mapName": "Gorodok",
                      "gameModeName": "RAAS", "matchState": "InProgress",
                      "matchId": "live-dev"},
        "teams": [{"id": 1, "factionId": "USA", "tickets": 300},
                  {"id": 2, "factionId": "RUS", "tickets": 300}],
        "squads": [], "players": players, "vehicles": [], "captureZones": [],
        "markers": [], "deployables": [], "vehicleSpawners": [], "rallyPoints": [],
        "projectiles": [], "damageEvents": [],
    }


def pos_frame(tick: int, t: float) -> dict:
    return {"t": "pos", "tick": tick, "timestamp": _now(), "vehicles": [],
            "players": [{"id": eos, "x": x, "y": y} for eos, x, y in _positions(t)]}


def synthetic_lines(stop: threading.Event) -> Iterator[tuple[str, bool]]:
    tick = 0
    while not stop.wait(0.25):
        tick += 1
        t = tick * 0.25
        if tick % 8 == 1:
            yield json.dumps(full_frame(tick, t)), True
        else:
            yield json.dumps(pos_frame(tick, t)), False


def recording_lines(stop: threading.Event, path: Path) -> Iterator[tuple[str, bool]]:
    """A .sqrx at its own pace (gaps capped at 2 s), once through."""
    last = None
    with SqrxReader(path) as reader:
        for line in reader:
            if stop.is_set():
                return
            frame = json.loads(line)
            ts = datetime.fromisoformat(frame["timestamp"]).timestamp()
            if last is not None:
                stop.wait(min(2.0, max(0.0, ts - last)))
            last = ts
            yield line, frame.get("t") != "pos"


def feed(live: LiveMap, stop: threading.Event, out_dir: Path, sqrx: Optional[Path],
         round_sec: float) -> None:
    """One .sqrx per round, written and announced the way recorder.py and
    cli.py do it."""
    box: dict = {"current": None}
    live.recording = lambda: box["current"]
    n = 0
    while not stop.is_set():
        n += 1
        path = out_dir / f"dev-round-{n}.sqrx"
        state = RecordingState(match_id=f"dev-{n}", writer=SqrxWriter(path, "live-dev"),
                               path=path, started_at=datetime.now(timezone.utc))
        box["current"] = state
        began = time.monotonic()
        for line, full in recording_lines(stop, sqrx) if sqrx else synthetic_lines(stop):
            state.writer.write_line(line)
            live.publish(line + "\n", full=full)
            if full:
                state.last_snap_ts = json.loads(line)["timestamp"]
                state.first_snap_ts = state.first_snap_ts or state.last_snap_ts
            if round_sec and time.monotonic() - began >= round_sec:
                break
        box["current"] = None
        state.writer.close()
        print(f"round {n} over: {path}", file=sys.stderr)
        stop.wait(BETWEEN_ROUNDS_SEC)


def main() -> int:
    ap = argparse.ArgumentParser(description="Viewer with a fake live round.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--password", default=DEV_PASSWORD)
    ap.add_argument("--sqrx", type=Path, help="replay this recording as the live round")
    ap.add_argument("--round-sec", type=float, default=0.0,
                    help="end each round after this many seconds and start the next")
    ap.add_argument("--no-live", action="store_true", help="serve without the live map")
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s: %(message)s")

    live = None if args.no_live else LiveMap(args.password)
    srv = serve_in_background(args.host, args.port, _TickBeat(), live=live,
                              frontend_dir=ROOT / "frontend" / "dist",
                              icons_dir=ROOT / "icons", sqmaps_dir=ROOT / "sqmaps")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if live is not None:
        if not os.environ.get("SQREADER_CONFIG"):
            cfg = Path(tempfile.mkdtemp()) / "sqreader.config.json"
            cfg.write_text(json.dumps({"live_password": args.password}), encoding="utf-8")
            os.environ["SQREADER_CONFIG"] = str(cfg)
        signal.signal(signal.SIGHUP, live.on_sighup)
        threading.Thread(target=feed, daemon=True,
                         args=(live, stop, Path(tempfile.mkdtemp()), args.sqrx,
                               args.round_sec)).start()
        print(f"http://localhost:{args.port}/  password: {args.password}  "
              f"(pid {os.getpid()})", file=sys.stderr)
    else:
        print(f"http://localhost:{args.port}/  (no live map)", file=sys.stderr)
    stop.wait()
    if live is not None:
        live.hub.close()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Smoke-Test (Port frei wählen, falls 8090 belegt ist):

```bash
.venv/bin/python scripts/live_dev_server.py --port 8091 & PID=$!; sleep 3
curl -s -c /tmp/jar -H 'Content-Type: application/json' -d '{"password":"dev-password-for-the-live-map"}' http://127.0.0.1:8091/api/live/login
curl -s -b /tmp/jar http://127.0.0.1:8091/api/live/round; echo
ID=$(curl -s -b /tmp/jar http://127.0.0.1:8091/api/live/round | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
timeout 3 curl -sN -b /tmp/jar "http://127.0.0.1:8091/api/live/round/$ID" | head -c 300; echo
kill $PID
```

Expected:
- Der Login antwortet `{"authenticated": true}`.
- `/api/live/round` liefert Meta mit `"id": "dev-round-1"`.
- Der Stream gibt JSON-Zeilen aus.

- [ ] **Step 9: Lint, Typen, Commit**

Run: `.venv/bin/python -m ruff check sqreader tests scripts/live_dev_server.py && .venv/bin/python -m mypy --python-version 3.12 sqreader`
Expected: keine neuen Funde. Kommt etwas aus Dateien, die dieser Task nicht anfasst, bitte mit `git stash` gegen den Stand davor vergleichen.

```bash
git add sqreader/live.py sqreader/cli.py tests/live_helpers.py tests/test_live_round.py tests/test_live_http.py tests/test_live_reload.py scripts/live_dev_server.py
git commit -m "Stream the round being recorded to moderators, and follow it as it grows"
```

---

### Task 4: Frontend-Bausteine (URL-Weiche, Runden-Abfrage, Live-Kanten-Regeln)

**Files:**
- Modify: `frontend/src/api/recordings.ts`
- Modify: `frontend/src/live/client.ts`
- Modify: `frontend/src/live/client.test.mts`

**Interfaces:**
- Produces:
  - `recordings.ts`: `LIVE_ID_PREFIX = "@live:"`, `LIVE_DELAY_MS = 8000`, `isLiveId(id: string | null | undefined): id is string`, `recordingUrl(id: string): string`. Echte IDs passen auf `^[A-Za-z0-9_\-]+$` (`httpsrv._REC_ID_RE`), ein `@` kommt dort also nie vor.
  - `client.ts`:
    - `interface LiveRound { id: string; latestMs: number }`
    - `fetchLiveRound(): Promise<LiveRound | "anon" | null>`
    - `interface EdgeInput { playing: boolean; stalled: boolean; speed: number; lagMs: number; pausedForBuffer: boolean }`
    - `type EdgeAction = "slow" | "buffer" | "resume" | "unstall" | null`
    - `edgeStep(s: EdgeInput): EdgeAction`
    - `isAtLive(playing: boolean, lagMs: number): boolean`
    - `shouldAdvanceRound(frameCount: number, currentIdx: number, playing: boolean): boolean`
- `createLiveFeed` und `LiveFeed` bleiben bis Task 5, weil `LiveAccess.tsx` sie noch nutzt.

- [ ] **Step 1: Failing tests**

In `frontend/src/live/client.test.mts` wird die Import-Zeile zu:

```ts
import { createLiveFeed, edgeStep, isAtLive, retryAfterMinutes, shouldAdvanceRound } from "./client.ts";
import { isLiveId, LIVE_DELAY_MS, recordingUrl } from "../api/recordings.ts";
```

Vor der letzten `console.log`-Zeile einfügen:

```ts
// 6. Live ids reach the live round, every other id the archive. Both encoded.
eq(recordingUrl("2026-05-25_002432_Gorodok_RAAS_v1_40323435"),
   "./api/recording/2026-05-25_002432_Gorodok_RAAS_v1_40323435", "plain id");
eq(recordingUrl("@live:2026-10-05_120000_X"), "./api/live/round/2026-10-05_120000_X", "live id");
eq(recordingUrl("@live:a b/c"), "./api/live/round/a%20b%2Fc", "live id is encoded");
eq(recordingUrl("a b"), "./api/recording/a%20b", "plain id is encoded");
ok(isLiveId("@live:x") && !isLiveId("x") && !isLiveId(null) && !isLiveId(""), "isLiveId");

// 7. The live edge.
{
  const base = { playing: true, stalled: false, speed: 1, lagMs: LIVE_DELAY_MS,
                 pausedForBuffer: false };
  eq(edgeStep(base), null, "steady at the edge: nothing to do");
  eq(edgeStep({ ...base, speed: 4 }), "slow", "caught up at 4x: back to 1x");
  eq(edgeStep({ ...base, speed: 4, lagMs: 60_000 }), null, "far behind at 4x: keep going");
  eq(edgeStep({ ...base, speed: 4, playing: false }), null, "paused at 4x: leave the speed");
  eq(edgeStep({ ...base, stalled: true, lagMs: 0 }), "buffer", "hit the edge: rebuffer");
  eq(edgeStep({ ...base, playing: false, stalled: true, pausedForBuffer: true, lagMs: 2000 }),
     null, "still rebuffering");
  eq(edgeStep({ ...base, playing: false, stalled: true, pausedForBuffer: true }),
     "resume", "8 s ahead again: play on");
  eq(edgeStep({ ...base, playing: false, stalled: true, lagMs: 60_000 }),
     "unstall", "a pause the user pressed stays; only the banner goes");
  eq(edgeStep({ ...base, stalled: true, lagMs: 60_000 }),
     "resume", "playing from far back: clear the stale stall");
  ok(isAtLive(true, LIVE_DELAY_MS + 3000), "within 3 s of the edge is live");
  ok(!isAtLive(true, LIVE_DELAY_MS + 3001), "further back is not");
  ok(!isAtLive(false, 0), "paused is not live");
}

// 8. When the round is over: at its end, or at once if nothing of it ever came.
ok(shouldAdvanceRound(0, 0, true), "no frames (the stream was 404): go now");
ok(shouldAdvanceRound(10, 9, false), "stopped at the last frame: go");
ok(!shouldAdvanceRound(10, 9, true), "still playing out: wait");
ok(!shouldAdvanceRound(10, 3, false), "paused further back: leave the moderator be");
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `cd frontend && npm test`
Expected: FAIL. esbuild meldet beim Bündeln von `client.test.mts` fehlende Exporte (`No matching export … "edgeStep"`).

- [ ] **Step 3: `recordings.ts`**

Nach den Imports einfügen:

```ts
/**
 * A live round's id in the viewer store. A real recording's id never has an
 * "@" (httpsrv._REC_ID_RE), so the two can never be confused.
 */
export const LIVE_ID_PREFIX = "@live:";
/** How far behind the newest recorded frame "live" plays. */
export const LIVE_DELAY_MS = 8000;

export function isLiveId(id: string | null | undefined): id is string {
  return !!id && id.startsWith(LIVE_ID_PREFIX);
}

/** Where a recording's frames come from, and with "/meta" its timing. */
export function recordingUrl(id: string): string {
  return isLiveId(id)
    ? `./api/live/round/${encodeURIComponent(id.slice(LIVE_ID_PREFIX.length))}`
    : `./api/recording/${encodeURIComponent(id)}`;
}
```

In `fetchRecordingMeta` wird `` fetch(`./api/recording/${encodeURIComponent(id)}/meta`) `` zu `` fetch(`${recordingUrl(id)}/meta`) ``.

In `fetchReplayTiming` wird `` const r = await fetch(`./api/recording/${path}/meta`, { signal }); `` zu `` const r = await fetch(`${recordingUrl(id)}/meta`, { signal }); ``. `path` bleibt für den `/api/match/`-Rückfall.

In `fetchRecordingFrames` wird

```ts
  const r = await fetch(
    `./api/recording/${encodeURIComponent(id)}?v=${REPLAY_FORMAT_VERSION}${seek}`,
    signal ? { signal } : undefined);
```

zu

```ts
  const r = await fetch(
    `${recordingUrl(id)}?v=${REPLAY_FORMAT_VERSION}${seek}`,
    signal ? { signal } : undefined);
```

- [ ] **Step 4: `client.ts`**

Den Kopfkommentar ersetzen durch:

```ts
// Moderator live map: the few server calls and the rules of the live edge.
// Fork-only; see docs/superpowers/specs/2026-10-05-live-replay-design.md.
```

Den Import `import { LIVE_DELAY_MS } from "../api/recordings";` ergänzen. Vor `export interface LiveFeed` einfügen:

```ts
export interface LiveRound {
  /** The recording's file name without .sqrx. */
  id: string;
  /** Timestamp of its newest full frame, on the SERVER's clock. */
  latestMs: number;
}

/** The round being recorded right now; "anon" if logged out, null if none. */
export async function fetchLiveRound(): Promise<LiveRound | "anon" | null> {
  try {
    const r = await fetch("./api/live/round", { cache: "no-store" });
    if (r.status === 401) return "anon";
    if (!r.ok) return null;
    const m = (await r.json()) as { id?: unknown; latestUtc?: unknown };
    const latestMs = Date.parse(String(m.latestUtc));
    if (typeof m.id !== "string" || !Number.isFinite(latestMs)) return null;
    return { id: m.id, latestMs };
  } catch {
    return null;
  }
}

export interface EdgeInput {
  playing: boolean;
  stalled: boolean;
  speed: number;
  /** Newest held frame minus the playhead, in ms. */
  lagMs: number;
  /** The live edge itself paused playback to rebuffer. */
  pausedForBuffer: boolean;
}

export type EdgeAction = "slow" | "buffer" | "resume" | "unstall" | null;

/**
 * What the live edge does next, looked at four times a second.
 *
 * slow:    caught up faster than 1x: play on at 1x, which is live.
 * buffer:  the playhead hit the newest frame: pause until LIVE_DELAY_MS is
 *          held again, instead of stuttering frame by frame at the edge.
 * resume:  enough is held again (or the user went back): play on.
 * unstall: the user paused far back during a stall: their pause stays, the
 *          "buffering" banner goes.
 */
export function edgeStep(s: EdgeInput): EdgeAction {
  if (s.stalled && s.lagMs < LIVE_DELAY_MS) return s.playing ? "buffer" : null;
  if (s.stalled) return s.playing || s.pausedForBuffer ? "resume" : "unstall";
  if (s.playing && s.speed > 1 && s.lagMs <= LIVE_DELAY_MS) return "slow";
  return null;
}

/** At the live edge: playing, and no more than 3 s further back than it. */
export function isAtLive(playing: boolean, lagMs: number): boolean {
  return playing && lagMs <= LIVE_DELAY_MS + 3000;
}

/**
 * The round is over: move on once it has played out, or at once if none of it
 * ever arrived (the stream answered 404 because the round had just changed).
 */
export function shouldAdvanceRound(frameCount: number, currentIdx: number,
                                   playing: boolean): boolean {
  return frameCount === 0 || (!playing && currentIdx >= frameCount - 1);
}
```

- [ ] **Step 5: Tests laufen lassen, sie sind grün, und Typen prüfen**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: `15/15 suites passed`, die Live-Client-Suite ohne `FAIL`, und keine TypeScript-Fehler.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/recordings.ts frontend/src/live/client.ts frontend/src/live/client.test.mts
git commit -m "Route live ids to the running round, and put the live edge's rules where a test can see them"
```

(Kein dist-Build hier: Das Verhalten der App ändert sich erst in Task 5, und dort wird gebaut.)

---

### Task 5: Der Live-Player im Viewer

**Files:**
- Modify: `frontend/src/live/LiveAccess.tsx` (komplett ersetzen)
- Modify: `frontend/src/live/client.ts` (`createLiveFeed`, `LiveFeed` und deren Imports raus)
- Modify: `frontend/src/live/client.test.mts` (Decoder-Tests 1–4 raus)
- Modify: `frontend/src/live/live.css`
- Modify: `frontend/src/ui/TimelineBar.tsx`, `frontend/src/ui/TopBar.tsx`
- Modify: `frontend/dist/**` (neu gebaut)

**Interfaces:**
- Consumes:
  - aus Task 4: `LIVE_ID_PREFIX`, `LIVE_DELAY_MS`, `isLiveId`, `fetchLiveRound`, `edgeStep`, `isAtLive`, `shouldAdvanceRound`;
  - vorhanden: `useViewerStore` (`openReplay`, `setReplay`, `restartReplayAt`, `setMode`, `replay.{id,frames,frameCount,currentIdx,loading,bufferedMs,stalled,playing,speed}`) und `replayClock` (`frontend/src/state/replayClock.ts`).
- Produces: Exporte von `LiveAccess.tsx`: `LiveAccess`, `LiveEntry`, `LiveControls` (schon in `App.tsx`, `Home.tsx` und `TopBar.tsx` eingehängt), `goLive(): Promise<void>`, `exitLive(): void`.

- [ ] **Step 1: `LiveAccess.tsx` ersetzen**

```tsx
// Moderator live map: login dialog, the Home + TopBar buttons, and the live
// edge of the replay player. Fork-only. The live map IS a replay, of the round
// being recorded right now, which keeps growing while it plays. Design:
// docs/superpowers/specs/2026-10-05-live-replay-design.md; login and sessions:
// docs/superpowers/specs/2026-09-29-live-moderation-design.md.
// Renders nothing unless the server has the live map enabled: without it,
// GET ./api/live/session answers 404 and the public UI stays as it is.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { create } from "zustand";
import { useViewerStore } from "../state/viewerStore";
import { replayClock } from "../state/replayClock";
import { isLiveId, LIVE_DELAY_MS, LIVE_ID_PREFIX } from "../api/recordings";
import {
  edgeStep, fetchLiveRound, isAtLive, login, logout, probeSession, shouldAdvanceRound,
  type LiveAccessState,
} from "./client";
import "./live.css";

const RETRY_MS = 5000;       // between "is a round being recorded yet?" asks
const RECONNECT_MS = 3000;   // before re-opening a stream that dropped mid-round
const TICK_MS = 250;         // how often the live edge looks at the player

interface LiveStore {
  access: LiveAccessState;
  loginOpen: boolean;
  notice: string | null;
  /** In the live map, but no round is being recorded right now. */
  waiting: boolean;
  /** The playhead is at the live edge. */
  atLive: boolean;
}

const useLive = create<LiveStore>(() => ({
  access: "unknown", loginOpen: false, notice: null, waiting: false, atLive: false,
}));

let retryTimer = 0;

function setUrlMode(mode: "live" | null): void {
  const url = new URL(window.location.href);
  if (mode) url.searchParams.set("mode", mode);
  else url.searchParams.delete("mode");
  url.searchParams.delete("id");
  window.history.replaceState(null, "", url.toString());
}

function openLogin(notice: string | null = null): void {
  useLive.setState({ loginOpen: true, notice });
}

/** Still waiting for a round, and not gone off to watch a finished match meanwhile. */
function stillWaiting(): boolean {
  const s = useViewerStore.getState();
  const pastMatch = s.mode === "replay" && s.replay.id !== null && !isLiveId(s.replay.id);
  return useLive.getState().waiting && !pastMatch;
}

export function exitLive(): void {
  window.clearTimeout(retryTimer);
  useLive.setState({ waiting: false });
  useViewerStore.getState().setMode("home");
  setUrlMode(null);
}

function sessionLost(): void {
  useLive.setState({ access: "anon" });
  exitLive();
  openLogin("Sitzung abgelaufen – bitte neu anmelden.");
}

/**
 * To the live edge of the running round. One way in for everything: the Home
 * button, a reload on ?mode=live, the LIVE button, and the next round.
 */
export async function goLive(): Promise<void> {
  window.clearTimeout(retryTimer);
  const round = await fetchLiveRound();
  if (round === "anon") { sessionLost(); return; }
  const v = useViewerStore.getState();
  setUrlMode("live");
  if (!round) {
    // Between rounds. Whatever is on screen stays: the start page, or the end
    // of the last round. The banner says why nothing happens.
    useLive.setState({ waiting: true });
    retryTimer = window.setTimeout(() => { if (stillWaiting()) void goLive(); }, RETRY_MS);
    return;
  }
  useLive.setState({ waiting: false });
  const id = LIVE_ID_PREFIX + round.id;
  const from = round.latestMs - LIVE_DELAY_MS;
  const r = v.replay;
  if (r.id !== id) {
    // Four store updates in one task, which React renders once. Even if it did
    // not, restartReplayAt's nonce bump aborts a load that began at minute zero.
    v.openReplay(id);
    v.setReplay((x) => ({ ...x, playing: true }));
    v.restartReplayAt(from);
    v.setMode("replay");
  } else if (r.loading && r.bufferedMs >= round.latestMs - 3000) {
    // The edge is already held: go there without loading anything.
    const target = r.bufferedMs - LIVE_DELAY_MS;
    let i = r.frameCount - 1;
    while (i > 0 && Date.parse(r.frames[i]!.timestamp ?? "") > target) i--;
    v.setReplay((x) => ({ ...x, currentIdx: i, speed: 1, playing: true,
                          baseWallMs: 0, baseSnapMs: 0 }));
  } else {
    v.setReplay((x) => ({ ...x, speed: 1, playing: true }));
    v.restartReplayAt(from);
  }
}

/**
 * The live edge, for as long as a live round is open: 1x once caught up,
 * rebuffer at the edge, light the LIVE button, and when the stream closes find
 * out why: logged out, dropped, or the round is over.
 */
function useLiveEdge(): void {
  const id = useViewerStore((s) => s.replay.id);
  useEffect(() => {
    if (!isLiveId(id)) { useLive.setState({ atLive: false }); return; }
    let pausedForBuffer = false;
    let wasLoading = true;
    let roundOver = false;
    let timer = 0;
    const tick = () => {
      const s = useViewerStore.getState();
      const r = s.replay;
      if (r.id !== id) return;
      const lagMs = replayClock.valid ? r.bufferedMs - replayClock.ms : Infinity;
      switch (edgeStep({ playing: r.playing, stalled: r.stalled, speed: r.speed, lagMs,
                         pausedForBuffer })) {
        case "slow":
          s.setReplay((x) => ({ ...x, speed: 1, baseWallMs: 0, baseSnapMs: 0 }));
          break;
        case "buffer":
          pausedForBuffer = true;
          s.setReplay((x) => ({ ...x, playing: false }));
          break;
        case "resume":
          pausedForBuffer = false;
          s.setReplay((x) => ({ ...x, playing: true, stalled: false,
                                baseWallMs: 0, baseSnapMs: 0 }));
          break;
        case "unstall":
          s.setReplay((x) => ({ ...x, stalled: false }));
          break;
      }
      if (!r.stalled) pausedForBuffer = false;
      const atLive = isAtLive(r.playing, lagMs);
      if (useLive.getState().atLive !== atLive) useLive.setState({ atLive });

      if (wasLoading && !r.loading) {
        void fetchLiveRound().then((round) => {
          if (useViewerStore.getState().replay.id !== id) return;
          if (round === "anon") {
            sessionLost();
          } else if (round && LIVE_ID_PREFIX + round.id === id) {
            // Same round, so the connection dropped: carry on where the playhead is.
            timer = window.setTimeout(() => {
              const now = useViewerStore.getState();
              if (now.replay.id !== id || now.replay.loading) return;
              if (now.replay.frameCount) now.restartReplayAt(replayClock.ms);
              else void goLive();
            }, RECONNECT_MS);
          } else {
            roundOver = true;
          }
        });
      }
      wasLoading = r.loading;
      if (roundOver && shouldAdvanceRound(r.frameCount, r.currentIdx, r.playing)) {
        roundOver = false;
        void goLive();
      }
    };
    const iv = window.setInterval(tick, TICK_MS);
    return () => { window.clearInterval(iv); window.clearTimeout(timer); };
  }, [id]);
}

function LoginDialog() {
  const open = useLive((s) => s.loginOpen);
  const notice = useLive((s) => s.notice);
  const dlgRef = useRef<HTMLDialogElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const secure = window.isSecureContext;

  useEffect(() => {
    const dlg = dlgRef.current;
    if (!dlg) return;
    const onClose = () => useLive.setState({ loginOpen: false });
    dlg.addEventListener("close", onClose);
    return () => dlg.removeEventListener("close", onClose);
  }, []);

  useEffect(() => {
    const dlg = dlgRef.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      setError(null);
      dlg.showModal();
      pwRef.current?.focus();
    } else if (!open && dlg.open) {
      dlg.close();
    }
  }, [open]);

  const close = () => useLive.setState({ loginOpen: false });

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const input = pwRef.current;
    if (!input || pending) return;
    const password = input.value;
    input.value = "";
    setPending(true);
    const res = await login(password);
    setPending(false);
    if (!res.ok) {
      setError(res.message);
      input.focus();
      return;
    }
    useLive.setState({ access: "ok", loginOpen: false, notice: null });
    void goLive();
  };

  return (
    <dialog id="live-login" ref={dlgRef} onKeyDown={(e) => e.stopPropagation()}>
      <form method="post" onSubmit={(e) => { void submit(e); }}>
        <h2>Moderator-Login</h2>
        <p className="ll-sub">Live-Karte der laufenden Runde – nur für das Moderationsteam.</p>
        {notice && <p className="ll-notice">{notice}</p>}
        {secure ? (
          <>
            <input className="ll-hidden" type="text" name="username" autoComplete="username"
                   value="moderator" readOnly tabIndex={-1} aria-hidden="true" />
            <label className="ll-field">
              <span>Passwort</span>
              <input ref={pwRef} type="password" name="password"
                     autoComplete="current-password" required />
            </label>
            {error && <p className="ll-error" role="alert">{error}</p>}
            <div className="ll-actions">
              <button type="button" className="btn btn-ghost" onClick={close}>Abbrechen</button>
              <button type="submit" className="btn btn-primary" disabled={pending}>Anmelden</button>
            </div>
          </>
        ) : (
          <>
            <p className="ll-error" role="alert">Anmeldung nur über HTTPS möglich.</p>
            <div className="ll-actions">
              <button type="button" className="btn btn-ghost" onClick={close}>Abbrechen</button>
            </div>
          </>
        )}
      </form>
    </dialog>
  );
}

function WaitingBanner() {
  const waiting = useLive((s) => s.waiting);
  const mode = useViewerStore((s) => s.mode);
  const id = useViewerStore((s) => s.replay.id);
  if (!waiting || !(mode === "home" || (mode === "replay" && isLiveId(id)))) return null;
  return (
    <div id="live-waiting" className="buf-banner" role="status">
      <span className="buf-spin" />
      <span>Warte auf die nächste Runde…</span>
    </div>
  );
}

// Mounted once in App, outside the mode branch.
export function LiveAccess() {
  const access = useLive((s) => s.access);
  useLiveEdge();

  useEffect(() => {
    let cancelled = false;
    void probeSession().then((a) => {
      if (cancelled) return;
      useLive.setState({ access: a });
      if (new URL(window.location.href).searchParams.get("mode") !== "live") return;
      if (a === "ok") void goLive();
      else if (a === "anon") openLogin();
      else if (a === "off") setUrlMode(null);
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <WaitingBanner />
      {(access === "anon" || access === "ok") && <LoginDialog />}
    </>
  );
}

// Home nav: the way in. Nothing at all when the server has no live map.
export function LiveEntry() {
  const access = useLive((s) => s.access);
  if (access === "anon") {
    return <button className="btn btn-ghost" onClick={() => openLogin()}>Moderator-Login</button>;
  }
  if (access !== "ok") return null;
  return <button className="btn btn-ghost" onClick={() => { void goLive(); }}>Live-Karte</button>;
}

// TopBar, while the live map is shown.
export function LiveControls() {
  const mode = useViewerStore((s) => s.mode);
  const id = useViewerStore((s) => s.replay.id);
  const atLive = useLive((s) => s.atLive);
  if (mode !== "replay" || !isLiveId(id)) return null;
  const signOut = async () => {
    await logout();
    useLive.setState({ access: "anon" });
    exitLive();
  };
  return (
    <>
      <button className="tb-back" onClick={exitLive} title="zur Startseite">← Zurück</button>
      <button className={"live-edge" + (atLive ? " on" : "")} disabled={atLive}
              onClick={() => { void goLive(); }}
              title={atLive ? "live" : "zum Live-Rand springen"}>● LIVE</button>
      <button onClick={() => { void signOut(); }} title="Live-Sitzung beenden">Abmelden</button>
    </>
  );
}
```

- [ ] **Step 2: SSE-Decoder aus `client.ts` und dem Test entfernen**

In `frontend/src/live/client.ts` werden `export interface LiveFeed { … }` und `export function createLiveFeed() { … }` samt ihrem Kommentar gelöscht. Außerdem die Imports `import type { Snapshot } from "../state/types";` und `import { ReplayReconstructor, type RecordingLine } from "../state/replayReconstruct";`, die danach niemand mehr braucht.

In `frontend/src/live/client.test.mts`:
- Die Konstanten `full` und `pos` und die Blöcke `// 1.` bis `// 4.` löschen.
- `createLiveFeed` aus dem Import nehmen.
- Den Kopfkommentar zu `// Standalone unit test for the live map's client side. Bundled with esbuild and` / `// run under node, no framework (matches replayReconstruct.test.mts).` ändern.

- [ ] **Step 3: LIVE-Knopf und Wartebanner in `live.css`**

Ans Dateiende:

```css
/* The LIVE button: red while the playhead is at the live edge. */
#controls .live-edge.on {
  color: var(--bad);
  border-color: color-mix(in srgb, var(--bad) 50%, transparent);
}
#controls .live-edge:disabled { cursor: default; opacity: 1; }
/* The wait between rounds; LiveAccess sits outside #stage, hence fixed. */
#live-waiting { position: fixed; }
```

- [ ] **Step 4: `TimelineBar.tsx`: am Live-Rand nicht neu laden**

Import ergänzen: `import { isLiveId, LIVE_DELAY_MS } from "../api/recordings";`

Bei den Store-Selektoren oben in `TimelineBar()` (vor dem ersten `if (… ) return null;`) einfügen:

```tsx
  const replayId = useViewerStore((s) => s.replay.id);
```

In `seekToMs` als erste Zeile des Funktionskörpers, vor `const held = …`:

```tsx
    // Live: nothing exists past the live edge yet, and asking the server for it
    // would throw away everything held. Past the edge means "live".
    if (isLiveId(replayId)) target = Math.min(target, bufferedMs - LIVE_DELAY_MS);
```

- [ ] **Step 5: `TopBar.tsx`: Pille und „← Back“**

Import ergänzen: `import { isLiveId } from "../api/recordings";`

Nach `const replayId = useViewerStore((s) => s.replay.id);` einfügen:

```tsx
  const live = isLiveId(replayId);
```

`const statusClass = status === "live" ? "live"` wird zu `const statusClass = live || status === "live" ? "live"`, die Fortsetzungszeilen bleiben.

`{mode === "replay" ? "recording" : (STATUS_TR[status] ?? status)}` wird zu `{mode === "replay" ? (live ? "live" : "recording") : (STATUS_TR[status] ?? status)}`.

`{mode === "replay" && (` vor `<button className="tb-back" onClick={goBack}` wird zu `{mode === "replay" && !live && (`.

- [ ] **Step 6: Typen, Tests, Build**

Run: `cd frontend && npx tsc --noEmit && npm test && npm run build`
Expected: keine TypeScript-Fehler, `15/15 suites passed`, und `vite build` endet mit `✓ built in …`.

- [ ] **Step 7: Kurzer Browser-Check gegen den Dev-Server**

```bash
.venv/bin/python scripts/live_dev_server.py --port 8091
```

In Chrome `http://localhost:8091/` öffnen und prüfen:
1. Oben auf der Startseite steht „Moderator-Login“.
2. Login mit `dev-password-for-the-live-map` öffnet die Karte. Die Timeline wächst, „● LIVE“ ist rot, und die Spieler bewegen sich flüssig.
3. Ein Klick auf die linke Hälfte der Timeline springt zurück, „● LIVE“ wird grau. Ein Klick auf „● LIVE“ macht es wieder rot.

Den Server danach mit Ctrl+C beenden.

- [ ] **Step 8: Commit**

```bash
git add frontend/src frontend/dist
git commit -m "Play the live map as a replay of the running round, with a LIVE button and a login button"
```

---

### Task 6: SSE entfernen

**Files:**
- Modify: `sqreader/live.py` (Docstring, Konstanten, `Hub`, `pick`, `LiveMap.__init__`/`publish`/`on_sighup`/`reload`/`handle_get`/`_logout`/`_stream`)
- Modify: `sqreader/cli.py` (zwei `publish`-Blöcke)
- Modify: `scripts/live_dev_server.py`
- Modify: `tests/live_helpers.py`, `tests/test_live_reload.py`, `tests/test_live_http.py`
- Delete: `tests/test_live_hub.py`, `tests/test_live_stream.py`

**Interfaces:**
- Consumes: alles aus Task 3.
- Produces: `sqreader.live` ohne `Hub`, `pick`, `LiveMap.hub`, `LiveMap.publish` und `RING_SIZE`/`KEEPALIVE_SEC`/`RETRY_MS`. `/api/live/stream` antwortet 404. `tests/live_helpers.Stream(port, token, path, rcvbuf=None, headers="")` hat jetzt `path` als Pflicht und kein `event()` mehr.

- [ ] **Step 1: Tests auf den Endzustand umstellen**

```bash
git rm tests/test_live_hub.py tests/test_live_stream.py
```

`tests/live_helpers.py`:
- In `running()` wird der `finally`-Block zu `srv.shutdown()` und `srv.server_close()`, die zwei `live_map.hub.close()`-Zeilen und ihr `if` fliegen raus.
- `def full`, `def pos` und `def event` löschen.
- `class Stream`: Die Signatur wird zu `def __init__(self, port, token, path, rcvbuf=None, headers=""):`, die Methode `event` wird gelöscht.

`tests/test_live_reload.py`:

Imports ergänzen:

```python
from datetime import datetime, timezone

from sqreader.recorder import RecordingState
from sqreader.sqrx import SqrxWriter
```

In `test_an_unresolvable_config_path_still_revokes_everyone` die Zeilen `cursor, wake, _ = lm.hub.subscribe(1)` und `assert lm.hub.wait(cursor, wake, 2)[3] != wake        # kicked: streams re-check` löschen.

In `test_sighup_fails_closed_inline_when_no_thread_can_start` dieselben zwei Zeilen löschen.

`test_reload_ends_open_streams` wird ersetzt durch:

```python
def test_reload_ends_open_round_streams(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, json.dumps({"live_password": NEW}))
    path = tmp_path / "r.sqrx"
    state = RecordingState(match_id="m", writer=SqrxWriter(path, "srv"), path=path,
                           started_at=datetime.now(timezone.utc))
    state.writer.write_line(json.dumps({"timestamp": "2026-10-05T12:00:00+00:00", "tick": 1}))
    lm = live.LiveMap(PW)
    lm.recording = lambda: state
    with running(lm) as port:
        _, token, _ = login(port)
        st = Stream(port, token, "/api/live/round/r")
        try:
            st.head()
            st.line()
            lm.reload()
            assert st.closed_within(2)
        finally:
            st.close()
            state.writer.close()
```

`test_cli_wires_the_live_map_into_serve` wird zu:

```python
def test_cli_wires_the_live_map_into_serve():
    """Merge guard: upstream edits cmd_serve often, and a merge that drops one
    of these lines leaves the live map silently dead."""
    src = (Path(sqreader.__file__).parent / "cli.py").read_text(encoding="utf-8")
    uses = ('live.recording = lambda: record_state_box["current"]',
            "signal.signal(signal.SIGHUP, live.on_sighup)")
    for needle in ('live = live_from_config(config.get("live_password"), recordings_dir)',
                   "live=live", *uses):
        assert needle in src, needle
    assert "live.publish" not in src          # the SSE hub is gone; a merge must not revive it
    # A bare `live.<x>` would raise AttributeError in the public build, where
    # live is None: each use must stay behind its guard.
    for use in uses:
        assert re.search(rf"if live is not None:\s*{re.escape(use)}", src), \
            f"{use} lost its `if live is not None:` guard"
```

In `tests/test_live_http.py`, `test_legacy_live_paths_stay_404_with_live_enabled`, wird die Pfadliste zu `("/stream", "/latest", "/api/alerts", "/api/live/nope", "/api/live/stream")`.

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_live_reload.py tests/test_live_http.py`
Expected: FAIL in `test_cli_wires_the_live_map_into_serve` (`live.publish` steht noch in `cli.py`) und in `test_legacy_live_paths_stay_404_with_live_enabled` (`/api/live/stream` antwortet noch).

- [ ] **Step 3: `cli.py`**

Beide Blöcke löschen:

```python
        if live is not None:
            live.publish(line, full=True)
```

und

```python
                        if live is not None:
                            live.publish(pos_line, full=False)
```

- [ ] **Step 4: `live.py`**

Der Modul-Docstring wird zu:

```python
"""Moderator-only live map. Fork-only, and OFF unless a live password is set.

Upstream removed the live view from the public build (tests/test_public_no_live.py)
because a live map shows every player of both teams in real time. This module
brings it back behind a login: one shared password (a scrypt hash in
SQREADER_LIVE_PASSWORD_HASH, or live_password in sqreader.config.json), an
in-memory session cookie, and the running round's growing .sqrx, streamed from
any point and then followed as the recorder appends to it. The viewer plays it
like any replay, with "live" a few seconds behind its newest frame.

Without a valid password, live_from_config() returns None and the HTTP server
stays exactly the public build: no /api/live/ route, no do_POST and no SIGHUP
handler.

Design: docs/superpowers/specs/2026-09-29-live-moderation-design.md (login,
sessions, SIGHUP) and docs/superpowers/specs/2026-10-05-live-replay-design.md
(the round stream, the password hash).
"""
```

Löschen:
- die Konstanten `RING_SIZE`, `KEEPALIVE_SEC`, `RETRY_MS`;
- die ganze `class Hub` und die ganze Funktion `pick`;
- in `LiveMap.__init__` die Zeile `self.hub = Hub()`;
- die Methode `LiveMap.publish`;
- die Methode `LiveMap._stream`;
- in `handle_get` den Zweig `elif path == "/api/live/stream":` mit `self._stream(h)`;
- in `reload` beide `self.hub.kick()`.

In `_logout` wird

```python
        if self.access.revoke(presented):
            log.info("live: logout from %s", client)
            self.hub.kick()
```

zu

```python
        if self.access.revoke(presented):
            log.info("live: logout from %s", client)
```

`on_sighup` wird zu:

```python
    def on_sighup(self, _signum: int, _frame: Any) -> None:
        # Runs between two bytecodes of the main thread, which may be inside a
        # log call. Taking locks here could deadlock, so a thread does the work.
        try:
            threading.Thread(target=self.reload, name="sqreader-live-reload",
                             daemon=True).start()
        except Exception:
            # No thread to be had (RuntimeError: can't start new thread), and an
            # exception here would surface in the reader's tick loop. Fail closed
            # on the spot, without logging. Safe from the signal context: the
            # main thread never holds Access._lock. Open round streams notice
            # within POLL_SEC.
            self.access.reset(None)
```

Danach muss `ruff` sagen, ob `deque` noch gebraucht wird (ja, von `Access._fails`). Nicht mehr genutzte Imports entfernen, die `ruff` meldet.

- [ ] **Step 5: Dev-Server**

In `scripts/live_dev_server.py` die Zeile `live.publish(line + "\n", full=full)` löschen, ebenso in `main()` die Zeilen

```python
    if live is not None:
        live.hub.close()
```

- [ ] **Step 6: Alles prüfen**

Run: `grep -rn "hub\|publish\|_stream(\|/api/live/stream\|createLiveFeed\|EventSource" sqreader/live.py sqreader/cli.py scripts/live_dev_server.py tests/live_helpers.py tests/test_live_*.py frontend/src/live`
Expected: nur `/api/live/stream` in den 404-Tests von `tests/test_live_http.py` und sonst kein Treffer. `live.publish` nur noch als Verbot im Merge-Guard.

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: genau 4 Fehler, alle in `tests/test_custom_capzones.py`.

Run: `.venv/bin/python -m ruff check sqreader tests scripts/live_dev_server.py && .venv/bin/python -m mypy --python-version 3.12 sqreader`
Expected: keine neuen Funde.

- [ ] **Step 7: Commit**

```bash
git add -A sqreader/live.py sqreader/cli.py scripts/live_dev_server.py tests/
git commit -m "Drop the SSE live stream: the round's recording carries the live edge now"
```

---

### Task 7: Doku, Ende-zu-Ende-Prüfung, Abschluss

**Files:**
- Modify: `docs/live-map.md` (komplett ersetzen)
- Modify: `docs/superpowers/specs/2026-09-29-live-moderation-design.md` (Kopf)
- Delete: `docs/superpowers/plans/2026-10-05-live-replay.md` (dieser Plan, ganz am Ende)
- Memory: `/home/hans/.claude/projects/-home-hans-PhpstormProjects-squadreader/memory/live-view-private-upstream.md`

**Interfaces:**
- Consumes: Log-Zeilen und Endpunkte aus Task 2, 3 und 6 (wörtlich so dokumentieren).

- [ ] **Step 1: `docs/live-map.md` ersetzen**

```markdown
# Moderator live map

A real-time map of the running round, for moderators only. It is **off**
unless a live password is set; then it lives under `/api/live/` and nowhere
else.

A live map shows **both teams in real time**. Anyone who watches it while
playing can ghost, which is why the public build has no live view at all.
Give the password to moderators and to nobody else.

The live map is the replay viewer, pointed at the round being recorded right
now. Its timeline runs from the start of the round to now, and **● LIVE**
plays a few seconds behind the newest frame. Click anywhere on the timeline to
watch a moment again, and **● LIVE** to come back. So it needs the recording
(`--recordings-dir`, which the Docker image always sets). Between two rounds,
while the scoreboard shows and the next map loads, nothing is recorded and
there is nothing to watch; the map waits and opens the next round by itself.

## Enable

1. Pick a password and turn it into a hash:

       python3 -m sqreader.live hash                                                          # native
       docker compose run --rm --no-deps --entrypoint python3 sqreader -m sqreader.live hash   # Docker

   It asks twice and prints one line that starts with `scrypt:`. A long random
   password is still the better choice: logins are throttled, but whoever gets
   hold of the hash can try passwords offline as fast as scrypt allows. One way
   to make one: `python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`
2. Put the hash where the reader finds it, either
   - **Docker:** `SQREADER_LIVE_PASSWORD_HASH=scrypt:…` in `.env`, or
   - **any install:** `"live_password": "scrypt:…"` in `sqreader.config.json`,
     and `chmod 600` the file. A plain-text password works there as well.
     Under Docker, uncomment the `./sqreader.config.json:/app/sqreader.config.json:ro`
     line in `docker-compose.yml`.

   The environment wins when both are set, and it takes only a hash: anyone
   who can run `docker inspect` reads it.
3. Restart the reader between two rounds (`docker compose up -d` after editing
   `.env`). The log says `live map enabled for moderators (password from …)`.

Moderators open the start page and click **Moderator-Login** at the top, or
go to `https://<your site>/?mode=live`. HTTPS is required; plain `http://`
only works on `localhost`. After a login the button reads **Live-Karte**. A
session lasts 12 hours, and every reader restart, a Squad server restart
included, logs everyone out.

## Watching

- The timeline covers the round so far and grows with it. Click or drag to any
  point; −10 s, +10 s, frame steps (`,` and `.`) and the speeds work as in any
  replay. Kills, markers and the ticket view (`G`) cover what has been loaded.
- **● LIVE** is red while you are live, about 8 s behind the newest frame. Grey
  means you are behind; click it to jump to the live edge. Catching up at 2× or
  faster drops to 1× at the edge on its own.
- If the reader stalls, the map pauses at the edge and goes on once 8 s are
  held again.
- When the round ends, the recording plays to its end, and then the map opens
  the next round, showing "Warte auf die nächste Runde…" until it starts.

## Change the password, or kick everyone out

SIGHUP always ends every session at once, and open live maps drop back to the
login. What it does to the password depends on where the password lives.

**`live_password` in the config file:** edit it, then send SIGHUP. No restart
is needed, and the running recording stays intact:

    docker compose kill -s HUP sqreader                # Docker
    systemctl kill -s HUP --kill-whom=main <unit>      # systemd

`--kill-whom=main` signals the reader alone. Without it systemd also signals
the build worker process of a two-tier recording, which has no SIGHUP handler,
dies, and has to be respawned.

Under Docker the config file is a single-file bind mount, and a bind mount
follows the file's inode. Edit it in place, for example with `cat new.json >
sqreader.config.json` or `tee`. `sed -i`, vim's default save and most editors
replace the file instead; the container then keeps reading the old copy, and
SIGHUP logs `live_password in <path> is UNCHANGED`. After every change, check
that the log says `password reloaded` and that the old password is refused.

If the file cannot be read or holds no valid password, **nobody** can log in
until it is fixed and SIGHUP is sent again. The log line names the reason.

**`SQREADER_LIVE_PASSWORD_HASH` in the environment:** a running process never
sees a changed environment. SIGHUP still logs everyone out and says the
password is `UNCHANGED (environment …)`; a new hash takes a restart between
two rounds: edit `.env`, then `docker compose up -d`.

SIGHUP only does this while the live map is on. A reader that started without
a valid password has no SIGHUP handler. Under systemd or in a terminal it
exits on SIGHUP as before; as a container's PID 1 it does not, because the
kernel ignores a signal that has no handler there. Either way, turning the
live map on, or repairing a value it rejected at startup, takes a restart
between two rounds. With the live map on, SIGHUP always means "revoke", never
"exit", a terminal hangup included.

## Turn it off

Remove the password, from `.env` and from the config file, and restart the
reader **between two rounds**. A restart in the middle of a round leaves that
round's recording so far unplayable.

## Proxies

The round is streamed as NDJSON in one long response, gzip-compressed for
browsers, and must not be buffered:

- **Traefik**: no `buffering` middleware on this router, and keep the
  entrypoint's `respondingTimeouts.writeTimeout` at 0 (the default).
- **Caddy**: keep `/api/live/*` out of `encode`, for example
  `@compress not path /api/live/*` and `encode @compress zstd gzip`.
- **nginx**: nothing to do; the stream sends `X-Accel-Buffering: no`.

Serve the map on its own hostname. Cookies do not tell ports apart, so two
instances on one hostname share a login cookie, and an app that shares the
origin (a path prefix next to other sites) could read the stream with a
moderator's session.

## If logins are being blocked

After five wrong passwords from one address, or fifty from everyone, within
10 minutes, new logins wait. The log says which limit was reached:

    live: login limit reached for <ip>     that address waits
    live: global login limit reached       every address waits

Open live maps keep streaming; only new logins wait, at most 10 minutes after
the last failed one. SIGHUP does not clear the counters. A restart does, and
logs everyone out, but a restart in the middle of a round leaves that round's
recording so far unplayable.

The real fix is to keep strangers off `/api/live/` at the proxy, so that only
moderators can spend failed attempts: an IP allowlist for their addresses, or
basic auth, on a router or location that matches `/api/live/` and nothing
else.

**Traefik (v3)**: a second router for the same service. Traefik ranks routers
by the length of their rule, so the longer one takes `/api/live/` and the
existing router keeps everything else:

    labels:
      - traefik.http.middlewares.sqreader-mods.ipallowlist.sourcerange=203.0.113.7,198.51.100.0/24
      - "traefik.http.routers.sqreader-live.rule=Host(`live.example.com`) && PathPrefix(`/api/live/`)"
      - traefik.http.routers.sqreader-live.middlewares=sqreader-mods
      - traefik.http.routers.sqreader-live.service=sqreader

Use the `Host` of your existing router, and copy its `entrypoints` and `tls`
labels onto the new one if it has any: a router that differs there does not
match the same requests, and the allowlist would silently not apply. Then
check from both sides. A moderator's address still logs in, and
`curl -i https://<your site>/api/live/session` from any other address answers
Traefik's 403.

**Caddy**: put this in the site block; `respond` runs before `reverse_proxy`.

    @strangers {
        path /api/live/*
        not remote_ip 203.0.113.7 198.51.100.0/24
    }
    respond @strangers 403

**nginx**: give `/api/live/` a location of its own, with your usual
`proxy_pass` lines in it.

    location /api/live/ {
        allow 203.0.113.7;
        allow 198.51.100.0/24;
        deny all;
        # proxy_pass and proxy_set_header as in the location that serves the map
    }

The throttle keys on the rightmost `X-Forwarded-For` entry, which is the
address your own proxy saw. With a CDN in front that is the CDN's edge, so
everyone behind the same edge shares one per-address limit; rely on the
allowlist there.

## Check it

Log in with curl and let it keep the cookie in a jar (that leaves the password
in your shell history), or copy the `sqr_live` cookie of a logged-in browser
from the dev tools (Application in Chrome, Storage in Firefox) and pass it with
`-H 'Cookie: sqr_live=<token>'` instead of `-b jar`:

    curl -c jar -H 'Content-Type: application/json' -d '{"password":"…"}' https://<your site>/api/live/login
    curl -s --compressed -b jar https://<your site>/api/live/round

prints the round being recorded, `{"id": "…", "startedAtUtc": "…", …}`: 401
without a valid cookie, 404 between rounds. Then

    curl -sN --compressed -b jar https://<your site>/api/live/round/<id>

prints its frames, one JSON object per line, and keeps printing new ones as
they are recorded.

## What the log says

| Line | Meaning |
|---|---|
| `live map enabled for moderators (password from SQREADER_LIVE_PASSWORD_HASH)`, `… (password from live_password)` | at startup: the live map is on, and where its password came from |
| `live map disabled: <source> <rule>` | at startup: the password was rejected and there is no live map; the rule is named, the value never |
| `live map disabled: it needs --recordings-dir` | at startup: there is a password, but nothing is recorded, so there is no round to show |
| `live: login ok from <ip> (session <id>)` | a moderator logged in |
| `live: login failed from <ip>` | a wrong password |
| `live: logout from <ip>` | a moderator logged out |
| `live: login limit reached for <ip>`, `live: global login limit reached` | 5 failures from one address, or 50 from everyone, within 10 minutes; logins wait, open sessions are unaffected; see "If logins are being blocked" |
| `live: round stream opened from <ip> (<n>/10)`, `live: round stream closed from <ip> after <t> (<reason>)` | a live map started or stopped streaming the round; the reason is `round ended`, `session ended`, `client gone`, `write timeout` or `bad data` |
| `live: stream refused from <ip> (10/10 in use)` | more than 10 live maps at once |
| `live: SIGHUP: all sessions revoked, password reloaded` | the file held a different password; everyone was logged out |
| `live: SIGHUP: all sessions revoked; live_password in <path> is UNCHANGED` | everyone was logged out, but the file still holds the old password; expected for a plain revoke, a problem after an edit (see the Docker bind mount above) |
| `live: SIGHUP: all sessions revoked; password from SQREADER_LIVE_PASSWORD_HASH is UNCHANGED (environment: change it with a restart between rounds)` | everyone was logged out; the password comes from the environment, which a running process cannot reload |
| `live: SIGHUP: all sessions revoked; NO valid live_password in <path> (<why>), logins disabled until fixed` | the file cannot be read or holds no valid password, and nobody can log in until it is fixed and SIGHUP is sent again; `<why>` is an error type such as `FileNotFoundError` or `JSONDecodeError`, or the rule the value broke; `<path>` is `None` if the path itself could not be found |

A login or a stream from an address you do not know means the password has
leaked: change it and send SIGHUP (or restart, for the environment's hash).
```

- [ ] **Step 2: Alte Spec verweist auf die neue**

In `docs/superpowers/specs/2026-09-29-live-moderation-design.md` nach der Zeile `- **Status:** zur Durchsicht` einfügen:

```markdown
- **Abgelöst in Teilen:** Abschnitt 8 (Live-Stream per SSE) und die SSE-Teile von 3, 9, 10, 13 und 14 ersetzt `2026-10-05-live-replay-design.md`: Die Live-Karte ist seitdem ein Replay der laufenden Runde.
```

- [ ] **Step 3: Ende-zu-Ende im Browser**

```bash
.venv/bin/python scripts/live_dev_server.py --port 8091 --round-sec 90
```

In Chrome nacheinander prüfen, am besten mit einem GIF-Mitschnitt:
1. Auf `http://localhost:8091/` steht „Moderator-Login“. Nach dem Login öffnet sich die Karte, „● LIVE“ ist rot, und die Pille sagt „live“.
2. Nach etwa 30 s auf den Anfang der Timeline klicken. Die Karte zeigt den Rundenstart, „● LIVE“ ist grau.
3. Geschwindigkeit 8× wählen. Nach wenigen Sekunden steht sie von selbst auf 1×, und „● LIVE“ ist rot, ohne Standbild.
4. Am Rand „+10 s“ klicken. Es bleibt live, nichts lädt neu (DevTools > Network: kein neuer `/api/live/round/…`-Request).
5. Aus der Mitte „● LIVE“ klicken. Das bringt einen sofortigen Sprung zum Rand.
6. Warten bis Sekunde 90. Die Runde läuft aus, dann steht „Warte auf die nächste Runde…“, und nach etwa 10 s öffnet sich `dev-round-2` von selbst.
7. Zweiten Tab mit derselben Seite öffnen und dort „Abmelden“ klicken. Im ersten Tab erscheint innerhalb weniger Sekunden der Login mit „Sitzung abgelaufen – bitte neu anmelden.“
8. Mit `--no-live` neu starten. Auf der Startseite gibt es keinen Login-Button.

Run danach: `.venv/bin/python -m pytest -q -p no:cacheprovider && (cd frontend && npm test)`
Expected: genau die 4 bekannten Fehler in `tests/test_custom_capzones.py`, und die Frontend-Suiten sind grün.

- [ ] **Step 4: Gedächtnis aktualisieren**

`/home/hans/.claude/projects/-home-hans-PhpstormProjects-squadreader/memory/live-view-private-upstream.md`:
- Den Abschnitt „Neu gebaut am 2026-09-29 …“ um einen Punkt ergänzen: „2026-10-05: Die Live-Karte ist ein Replay der laufenden Runde (Datei-Folge-Stream `/api/live/round/<id>`), SSE entfernt; Passwort auch als scrypt-Hash in `SQREADER_LIVE_PASSWORD_HASH`; Spezifikation `docs/superpowers/specs/2026-10-05-live-replay-design.md`.“
- In „How to apply“ wird „Die Hooks in cli.py und httpsrv.py sind durch einen Merge-Guard-Test gepinnt“ ergänzt um „(`live.recording = …`, SIGHUP, kein `live.publish`)“.

- [ ] **Step 5: Plan löschen und committen**

```bash
git rm docs/superpowers/plans/2026-10-05-live-replay.md
git add docs/live-map.md docs/superpowers/specs/2026-09-29-live-moderation-design.md
git commit -m "Document the live map as a replay of the running round"
```

Danach `git log --oneline master..live-moderation | head -20` zeigen und den Nutzer ans Pushen erinnern (`git push origin live-moderation`).

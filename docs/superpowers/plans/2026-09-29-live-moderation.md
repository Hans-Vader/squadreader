# Live-Karte für die Moderation – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Moderatoren sehen die laufende Runde live im Viewer. Der Zugang läuft über eine Login-Maske mit einem gemeinsamen Passwort aus `sqreader.config.json`. Ist kein Passwort gesetzt, bleibt der Server exakt der öffentliche Build.

**Architecture:**
- Ein neues Modul `sqreader/live.py` enthält drei Teile: den Zugang (Passwort, Sitzungen, Login-Bremse), einen Verteiler (`Hub`), der nie blockiert, und die HTTP-Endpunkte unter `/api/live/*` samt SSE-Schleife.
- `httpsrv.py` und `cli.py` bekommen nur kleine Hooks.
- Im Frontend speist `frontend/src/live/` per `EventSource` den vorhandenen, bisher ungenutzten Live-Renderpfad.

**Tech Stack:**
- Backend: Python ≥ 3.10, nur Standardbibliothek (`http.server`, `threading`, `hashlib`, `hmac`, `secrets`, `ipaddress`); Tests mit pytest.
- Frontend: React 18, TypeScript, zustand, Vite; Node-Tests über `frontend/scripts/run-tests.mjs`.

**Spec:** `docs/superpowers/specs/2026-09-29-live-moderation-design.md`

## Global Constraints

**Git**
- Gearbeitet wird auf dem Branch `live-moderation`.
- Commit-Nachrichten sind schlichtes Englisch, **ohne** `Co-Authored-By`-Trailer und ohne jede andere KI-Attribution.

**Abhängigkeiten**
- Python ≥ 3.10; die CI prüft auf 3.10 und 3.13.
- Nur die Python-Standardbibliothek, im Frontend keine neuen npm-Pakete.

**Ohne Passwort unverändert**
- Ohne gültiges `live_password` sind alle Antworten byte-gleich zu heute (abgesehen vom `Date`-Header).
- `tests/test_public_no_live.py` und `tests/test_http_versions.py` werden nicht verändert.

**Config-Wert `live_password`**
- Ein String mit mindestens 20 Zeichen, ohne Leerzeichen am Anfang oder Ende.
- Kein Eintrag in `config.DEFAULTS`, kein CLI-Flag, keine Umgebungsvariable.

**Konstanten** (auf Modul-Ebene in `sqreader/live.py`):

| Name | Wert |
|---|---|
| `MIN_PASSWORD_LEN` | 20 |
| `SESSION_TTL_SEC` | 43200 |
| `MAX_SESSIONS` | 32 |
| `BODY_MAX` | 1024 |
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

**Cookies**
- Login: `sqr_live=<token>; Max-Age=43200; HttpOnly; Secure; SameSite=Strict`
- Logout: `sqr_live=; Max-Age=0; HttpOnly; Secure; SameSite=Strict`
- Kein `Path`, keine `Domain`.

**HTTP-Antworten**
- Die Antworten der Live-Endpunkte (`session`, `login`, `logout`, `stream`) tragen `Cache-Control: no-store` und nie CORS-Header.
- Unbekannte `/api/live/*`-Pfade: `send_error(404, "no such endpoint")`.
- POST auf andere Pfade: `send_error(501, "Unsupported method ('POST')")`.

**Logs und Texte**
- Logzeilen kommen wörtlich aus der Spec und laufen über den Logger `sqreader.live`. Passwort und Token werden nie geloggt.
- Die UI-Texte kommen wörtlich aus Spec §10 und sind deutsch.

**Tabu**
- Nicht anfassen: `frontend/src/state/viewerStore.ts`, `frontend/src/canvas/MapCanvas.tsx`, `frontend/src/style.css`, `frontend/vite.config.ts`, `sqreader/config.py`.
- In `sqreader/httpsrv.py` keine neuen Imports und keine Änderungen an Docstrings.

**Build und Prüfung**
- Nach jeder Frontend-Änderung wird `frontend/dist` neu gebaut und mitcommittet.
- Prüfbefehle, jeweils vom Repo-Wurzelverzeichnis aus:
  - `.venv/bin/python -m pytest -q`
  - `.venv/bin/ruff check sqreader tests scripts`
  - `.venv/bin/mypy sqreader`
  - `(cd frontend && npm run build && npm test)`

## Review Focus

Fünf Fälle, die kein Unit-Test nebenbei abdeckt, jeweils mit erwartetem Verhalten und dem Ort, an dem sie geprüft werden:

1. **Ein Proxy puffert oder komprimiert den Stream** (Traefik compress v3.0–3.3.4, Caddy `encode`).
   - Erwartet: Jedes Ereignis kommt innerhalb von etwa 1 s an.
   - Geprüft in Task 10, Schritt 6.
2. **Der Reader oder Container startet neu, während Moderatoren zusehen.**
   - Erwartet: zuerst „Reconnecting…“, danach der Login-Dialog mit „Sitzung abgelaufen – bitte neu anmelden.“
   - Geprüft in Task 10, Schritt 4.
3. **Die Sitzung läuft ab, während der Stream zwischen zwei Runden leerläuft.**
   - Erwartet: Der Stream endet spätestens nach `KEEPALIVE_SEC`.
   - Geprüft in Task 5 mit `test_an_expired_session_ends_an_idle_stream`.
4. **Ein Passwort mit Umlauten oder „€“ wird im Browser getippt.**
   - Erwartet: Der Login klappt.
   - Geprüft in Task 1 mit `test_non_ascii_password_works` und in Task 4 mit `test_a_non_ascii_password_works_over_http`.
5. **Ein Stream wird geöffnet, bevor es einen einzigen Frame gibt** (der Reader startet gerade).
   - Erwartet: Der Stream bleibt offen, sendet Keepalives und liefert dann den ersten Frame.
   - Geprüft in Task 5 mit `test_before_any_frame_the_stream_keeps_alive_then_delivers`.

## File Structure

| Datei | Verantwortung |
|---|---|
| `sqreader/live.py` (neu) | Zugang, Hub, HTTP-Endpunkte, SSE, SIGHUP |
| `sqreader/httpsrv.py` | Parameter `live`, eine Routing-Zeile, `do_POST` nur mit Live |
| `sqreader/cli.py` | `live` bauen, zwei `publish`-Hooks, SIGHUP-Handler |
| `sqreader.config.example.json` | `_live_comment` und `"live_password": null` |
| `docs/live-map.md` (neu) | Betriebsanleitung (Englisch) |
| `tests/live_helpers.py` (neu) | Test-Server, HTTP- und Stream-Helfer |
| `tests/test_live_access.py` (neu) | Passwort, Sitzungen, Bremse, Client-Schlüssel |
| `tests/test_live_hub.py` (neu) | `Hub`, `pick()` |
| `tests/test_live_http.py` (neu) | Endpunkte, Abwesenheit ohne Passwort, Gates |
| `tests/test_live_stream.py` (neu) | SSE-Stream |
| `tests/test_live_reload.py` (neu) | SIGHUP-Reload, Verdrahtung in `cli.py`, Beispiel-Config |
| `frontend/src/live/client.ts` (neu) | Server-Aufrufe, Feed-Decoder |
| `frontend/src/live/client.test.mts` (neu) | Tests zu `client.ts` |
| `frontend/src/live/LiveAccess.tsx` (neu) | Login-Dialog, Stream-Hook, Buttons |
| `frontend/src/live/live.css` (neu) | Dialog-Stil |
| `frontend/src/App.tsx`, `frontend/src/ui/TopBar.tsx`, `frontend/src/ui/Home.tsx` | je ein Import und eine Zeile |
| `frontend/dist/**` | neu gebaut |
| `scripts/live_dev_server.py` (neu) | Viewer mit simulierter Live-Runde für den Ende-zu-Ende-Test |

Die Spec nennt eine einzige Testdatei `tests/test_live.py`. Hier sind die Tests nach Einheiten auf fünf Dateien verteilt, damit jeder Task seine eigene Datei hat.

---

### Task 1: Passwort und Sitzungen (`Access`, Teil 1)

**Files:**
- Create: `sqreader/live.py`
- Test: `tests/test_live_access.py`

**Interfaces:**
- Consumes: –
- Produces:
  - Die Konstanten aus den Global Constraints und `COOKIE = "sqr_live"`.
  - `validate_password(value: Any) -> tuple[Optional[str], Optional[str]]` mit den Meldungen `_TOO_SHORT` und `_PADDED`.
  - `session_id(token: str) -> str`.
  - `class Access(password: Optional[str])` mit diesen Methoden:
    - `reset(password: Optional[str]) -> None`
    - `login(given: str, client: str, presented: list[str]) -> tuple[str, Any]`, liefert `("ok", token)` oder `("wrong", None)`
    - `valid(token: str) -> bool`
    - `first_valid(tokens: list[str]) -> Optional[str]`
    - `revoke(tokens: list[str]) -> bool`

- [ ] **Step 1: Write the failing test**

`tests/test_live_access.py`:

```python
"""Password, sessions and login throttle of the moderator live map (sqreader/live.py)."""
from __future__ import annotations

import time

import pytest

from sqreader import live

PW = "correct-horse-battery-staple-42"
WRONG = "wrong-password-but-long"


@pytest.mark.parametrize("value, reason", [
    (None, None),
    ("", None),
    (12345678901234567890123, live._TOO_SHORT),
    ("short-password", live._TOO_SHORT),
    (" " + PW, live._PADDED),
    (PW + "\n", live._PADDED),
    ("   ", live._PADDED),
])
def test_unusable_passwords_are_rejected_without_echoing_them(value, reason):
    password, why = live.validate_password(value)
    assert password is None
    assert why == reason
    if why is not None and isinstance(value, str) and value.strip():
        assert value.strip() not in why


def test_a_long_enough_password_is_accepted():
    assert live.validate_password(PW) == (PW, None)
    exact = "x" * live.MIN_PASSWORD_LEN
    assert live.validate_password(exact) == (exact, None)


def test_right_password_opens_a_session_and_wrong_one_does_not():
    a = live.Access(PW)
    assert a.login(WRONG, "1.2.3.4", []) == ("wrong", None)
    result, token = a.login(PW, "1.2.3.4", [])
    assert result == "ok"
    assert a.valid(token)
    assert not a.valid("forged-token")


def test_non_ascii_password_works():
    pw = "pässwörter-sind-lang-genug-€"
    a = live.Access(pw)
    assert a.login(pw, "c", [])[0] == "ok"
    assert a.login(pw.upper(), "c", [])[0] == "wrong"


def test_each_login_mints_a_new_token_and_drops_the_presented_one():
    a = live.Access(PW)
    _, first = a.login(PW, "c", [])
    _, second = a.login(PW, "c", [first])
    assert first != second
    assert not a.valid(first)
    assert a.valid(second)


def test_sessions_expire(monkeypatch):
    monkeypatch.setattr(live, "SESSION_TTL_SEC", 0.2)
    a = live.Access(PW)
    _, token = a.login(PW, "c", [])
    assert a.valid(token)
    time.sleep(0.3)
    assert not a.valid(token)


def test_the_oldest_session_is_evicted_beyond_the_cap(monkeypatch):
    monkeypatch.setattr(live, "MAX_SESSIONS", 3)
    a = live.Access(PW)
    tokens = [a.login(PW, f"c{i}", [])[1] for i in range(4)]
    assert not a.valid(tokens[0])
    assert all(a.valid(t) for t in tokens[1:])


def test_first_valid_skips_dead_duplicates_and_revoke_ends_sessions():
    a = live.Access(PW)
    _, token = a.login(PW, "c", [])
    assert a.first_valid(["junk", token]) == token
    assert a.revoke(["junk", token]) is True
    assert a.first_valid([token]) is None
    assert a.revoke([token]) is False


def test_reset_swaps_the_password_and_ends_every_session():
    other = "another-password-long-enough"
    a = live.Access(PW)
    _, token = a.login(PW, "c", [])
    a.reset(other)
    assert not a.valid(token)
    assert a.login(PW, "c", [])[0] == "wrong"
    assert a.login(other, "c", [])[0] == "ok"
    a.reset(None)
    assert a.login(other, "c", [])[0] == "wrong"


def test_login_is_logged_without_password_or_token(caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    a = live.Access(PW)
    a.login(WRONG, "1.2.3.4", [])
    _, token = a.login(PW, "1.2.3.4", [])
    assert "live: login failed from 1.2.3.4" in caplog.text
    assert f"live: login ok from 1.2.3.4 (session {live.session_id(token)})" in caplog.text
    assert PW not in caplog.text
    assert token not in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_access.py -q`
Expected: FAIL mit `ImportError: cannot import name 'live' from 'sqreader'`

- [ ] **Step 3: Write minimal implementation**

`sqreader/live.py`:

```python
"""Moderator-only live map. Fork-only, and OFF unless live_password is set.

Upstream removed the live view from the public build (tests/test_public_no_live.py)
because a live map shows every player of both teams in real time. This module
brings it back behind a login: one shared password from sqreader.config.json,
an in-memory session cookie, and a Server-Sent-Events stream of the reader's
own frame lines.

Without a valid live_password, live_from_config() returns None and the HTTP
server stays exactly the public build: no /api/live/ route, no do_POST and no
SIGHUP handler.

Design: docs/superpowers/specs/2026-09-29-live-moderation-design.md
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from typing import Any, Optional

log = logging.getLogger("sqreader.live")

MIN_PASSWORD_LEN = 20
SESSION_TTL_SEC = 43200           # 12 h, absolute; never extended
MAX_SESSIONS = 32
BODY_MAX = 1024                   # bytes of a login/logout body
BODY_DEADLINE_SEC = 10.0          # for the WHOLE body, not per recv
FAIL_WINDOW_SEC = 600.0
FAILS_PER_CLIENT = 5
FAILS_GLOBAL = 50
MAX_STREAMS = 10
RING_SIZE = 128
KEEPALIVE_SEC = 15.0
RETRY_MS = 3000
WRITE_TIMEOUT_SEC = 20.0
REFUSED_LOG_INTERVAL_SEC = 60.0

COOKIE = "sqr_live"

_TOO_SHORT = ("live map disabled: live_password must be a string of at least "
              f"{MIN_PASSWORD_LEN} characters")
_PADDED = "live map disabled: live_password has leading or trailing whitespace"


def validate_password(value: Any) -> tuple[Optional[str], Optional[str]]:
    """(password, None) if usable, (None, reason) if not, (None, None) if unset.

    The reason never contains the value: it is a credential.
    """
    if value is None or value == "":
        return None, None
    if not isinstance(value, str):
        return None, _TOO_SHORT
    if value != value.strip():
        return None, _PADDED
    if len(value) < MIN_PASSWORD_LEN:
        return None, _TOO_SHORT
    return value, None


def _digest(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


def session_id(token: str) -> str:
    """What the log shows in place of a token."""
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()[:8]


class Access:
    """The shared password and the sessions it grants. One lock guards both."""

    def __init__(self, password: Optional[str]) -> None:
        self._lock = threading.Lock()
        self._digest: Optional[bytes] = _digest(password) if password else None
        # token -> (monotonic expiry, client key)
        self._sessions: dict[str, tuple[float, str]] = {}

    def reset(self, password: Optional[str]) -> None:
        """A new password (None: nobody can log in) and no sessions at all."""
        with self._lock:
            self._digest = _digest(password) if password else None
            self._sessions.clear()

    def login(self, given: str, client: str,
              presented: list[str]) -> tuple[str, Any]:
        """('ok', token) or ('wrong', None).

        A successful login drops the sessions of the cookies the browser sent,
        so nobody can fix a token for a victim in advance.
        """
        now = time.monotonic()
        with self._lock:
            if not self._check(given):
                result: tuple[str, Any] = ("wrong", None)
            else:
                for token in presented:
                    self._sessions.pop(token, None)
                result = ("ok", self._new_session(client, now))
        if result[0] == "ok":
            log.info("live: login ok from %s (session %s)", client, session_id(result[1]))
        else:
            log.warning("live: login failed from %s", client)
        return result

    def valid(self, token: str) -> bool:
        now = time.monotonic()
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return False
            if entry[0] <= now:
                del self._sessions[token]
                return False
            return True

    def first_valid(self, tokens: list[str]) -> Optional[str]:
        """The first presented token that is a live session.

        A sibling app can plant a second cookie of the same name, so the first
        VALID one wins, not simply the first one.
        """
        for token in tokens:
            if self.valid(token):
                return token
        return None

    def revoke(self, tokens: list[str]) -> bool:
        """End these sessions; True if any of them existed."""
        found = False
        with self._lock:
            for token in tokens:
                if self._sessions.pop(token, None) is not None:
                    found = True
        return found

    def _check(self, given: str) -> bool:                     # caller holds the lock
        if self._digest is None:
            return False
        return hmac.compare_digest(_digest(given), self._digest)

    def _new_session(self, client: str, now: float) -> str:   # caller holds the lock
        for token, (expires, _client) in list(self._sessions.items()):
            if expires <= now:
                del self._sessions[token]
        while len(self._sessions) >= MAX_SESSIONS:
            oldest = min(self._sessions, key=lambda t: self._sessions[t][0])
            del self._sessions[oldest]
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (now + SESSION_TTL_SEC, client)
        return token
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_access.py -q && .venv/bin/ruff check sqreader tests && .venv/bin/mypy sqreader`
Expected: alle Tests PASS, ruff und mypy ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add sqreader/live.py tests/test_live_access.py
git commit -m "Check the moderator password and keep its sessions"
```

---

### Task 2: Login-Bremse und Client-Schlüssel (`Access`, Teil 2)

**Files:**
- Modify: `sqreader/live.py`
- Test: `tests/test_live_access.py` (erweitern)

**Interfaces:**
- Consumes: `Access` aus Task 1.
- Produces:
  - `Access.login(...)` liefert jetzt zusätzlich `("limited", retry_after_seconds: int)`.
  - `client_key(headers: Any, peer: str) -> str`. `headers` braucht nur `get_all(name, failobj=None)`.

- [ ] **Step 1: Write the failing test**

Ans Ende von `tests/test_live_access.py` anfügen:

```python
class _Headers:
    """Just the part of http.client.HTTPMessage that client_key reads."""

    def __init__(self, *forwarded: str) -> None:
        self._forwarded = list(forwarded)

    def get_all(self, name, failobj=None):
        if name == "X-Forwarded-For" and self._forwarded:
            return list(self._forwarded)
        return failobj


@pytest.mark.parametrize("forwarded, peer, key", [
    ((), "10.0.0.5", "10.0.0.5"),
    (("1.1.1.1, 2.2.2.2",), "172.18.0.2", "2.2.2.2"),
    (("9.9.9.9", "1.1.1.1, 3.3.3.3"), "172.18.0.2", "3.3.3.3"),
    (("10.9.9.9\r\n INFO sqreader.live: live: login ok from 6.6.6.6",), "172.18.0.2",
     "172.18.0.2"),
    (("not-an-ip",), "172.18.0.2", "172.18.0.2"),
    (("2001:db8:1:2:3:4:5:6",), "172.18.0.2", "2001:db8:1:2::/64"),
    (("::ffff:1.2.3.4",), "172.18.0.2", "1.2.3.4"),
    ((), "garbage", "unknown"),
])
def test_client_key(forwarded, peer, key):
    assert live.client_key(_Headers(*forwarded), peer) == key


def test_a_client_is_limited_after_five_failures_even_with_the_right_password():
    a = live.Access(PW)
    for _ in range(live.FAILS_PER_CLIENT):
        assert a.login(WRONG, "1.2.3.4", [])[0] == "wrong"
    result, retry = a.login(PW, "1.2.3.4", [])
    assert result == "limited"
    assert 1 <= retry <= live.FAIL_WINDOW_SEC
    assert a.login(PW, "5.6.7.8", [])[0] == "ok"        # nobody else is


def test_the_global_ceiling_limits_everyone(monkeypatch):
    monkeypatch.setattr(live, "FAILS_GLOBAL", 3)
    a = live.Access(PW)
    for i in range(3):
        a.login(WRONG, f"10.0.0.{i}", [])
    assert a.login(PW, "10.0.0.99", [])[0] == "limited"


def test_a_success_clears_that_clients_failures():
    a = live.Access(PW)
    for _ in range(live.FAILS_PER_CLIENT - 1):
        a.login(WRONG, "c", [])
    assert a.login(PW, "c", [])[0] == "ok"
    for _ in range(live.FAILS_PER_CLIENT):
        assert a.login(WRONG, "c", [])[0] == "wrong"      # a fresh budget of five
    assert a.login(PW, "c", [])[0] == "limited"


def test_the_window_slides(monkeypatch):
    monkeypatch.setattr(live, "FAIL_WINDOW_SEC", 0.3)
    a = live.Access(PW)
    for _ in range(live.FAILS_PER_CLIENT):
        a.login(WRONG, "c", [])
    assert a.login(PW, "c", [])[0] == "limited"
    time.sleep(0.4)
    assert a.login(PW, "c", [])[0] == "ok"


def test_reaching_a_limit_is_logged_once(caplog):
    caplog.set_level("WARNING", logger="sqreader.live")
    a = live.Access(PW)
    for _ in range(live.FAILS_PER_CLIENT + 3):
        a.login(WRONG, "1.2.3.4", [])
    assert caplog.text.count("live: login limit reached for 1.2.3.4") == 1
    assert caplog.text.count("live: login failed from 1.2.3.4") == live.FAILS_PER_CLIENT


def test_failures_stay_bounded_under_forged_client_keys():
    a = live.Access(PW)
    for i in range(1000):
        a.login(WRONG, f"10.{i // 250}.{i % 250}.1", [])
    assert len(a._fails) <= live.FAILS_GLOBAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_access.py -q`
Expected:
- `test_client_key` schlägt fehl mit `AttributeError: module 'sqreader.live' has no attribute 'client_key'`.
- Die Bremse-Tests schlagen fehl, weil der 6. Versuch mit richtigem Passwort `"ok"` liefert statt `"limited"`.

- [ ] **Step 3: Write minimal implementation**

In `sqreader/live.py` wird der Import-Block so geändert:

```python
import hashlib
import hmac
import ipaddress
import logging
import math
import secrets
import threading
import time
from collections import deque
from typing import Any, Optional
```

Direkt hinter `session_id()` kommt diese Funktion:

```python
def client_key(headers: Any, peer: str) -> str:
    """Throttle and log key for a request.

    The rightmost X-Forwarded-For entry of the LAST such header is the hop our
    own proxy appended (Traefik, Caddy and the nginx example all do so), so it
    is the real client behind the proxy. Anything that is not an IP address,
    such as a folded header carrying a forged log line, falls back to the
    socket peer. IPv6 is grouped by /64, which is what one host usually gets.
    """
    forwarded = headers.get_all("X-Forwarded-For") or []
    candidates = [forwarded[-1].rsplit(",", 1)[-1].strip()] if forwarded else []
    for cand in [*candidates, peer]:
        try:
            ip = ipaddress.ip_address(cand)
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped is not None:
                return str(ip.ipv4_mapped)
            return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))
        return str(ip)
    return "unknown"
```

In `Access.__init__` kommt nach der Zeile mit `self._sessions` diese Zeile dazu:

```python
        self._fails: deque[tuple[float, str]] = deque()      # (monotonic time, client)
```

`Access.login` wird vollständig durch diese Fassung ersetzt, und die zwei Hilfsmethoden kommen direkt dahinter:

```python
    def login(self, given: str, client: str,
              presented: list[str]) -> tuple[str, Any]:
        """('ok', token), ('wrong', None) or ('limited', retry_after_seconds).

        Only failures count. The throttle runs before the password is looked
        at, and a limited client is refused even with the right password, or
        the limit would be no limit at all.
        """
        now = time.monotonic()
        warn: list[str] = []
        with self._lock:
            self._prune(now)
            retry = self._retry_after(client, now)
            if retry:
                return "limited", retry
            if not self._check(given):
                self._fails.append((now, client))
                if sum(1 for _t, c in self._fails if c == client) == FAILS_PER_CLIENT:
                    warn.append(f"live: login limit reached for {client}")
                if len(self._fails) == FAILS_GLOBAL:
                    warn.append("live: global login limit reached")
                result: tuple[str, Any] = ("wrong", None)
            else:
                for token in presented:
                    self._sessions.pop(token, None)
                self._fails = deque(f for f in self._fails if f[1] != client)
                result = ("ok", self._new_session(client, now))
        if result[0] == "ok":
            log.info("live: login ok from %s (session %s)", client, session_id(result[1]))
        else:
            log.warning("live: login failed from %s", client)
            for line in warn:
                log.warning("%s", line)
        return result

    def _prune(self, now: float) -> None:                     # caller holds the lock
        while self._fails and self._fails[0][0] <= now - FAIL_WINDOW_SEC:
            self._fails.popleft()

    def _retry_after(self, client: str, now: float) -> int:   # caller holds the lock
        """Seconds until `client` may try again, or 0 if it may try now.

        Nothing is appended while a limit holds, so the deque never grows past
        FAILS_GLOBAL entries, forged client keys or not.
        """
        waits: list[float] = []
        mine = [t for t, c in self._fails if c == client]
        if len(mine) >= FAILS_PER_CLIENT:
            waits.append(mine[0] + FAIL_WINDOW_SEC - now)
        if len(self._fails) >= FAILS_GLOBAL:
            waits.append(self._fails[0][0] + FAIL_WINDOW_SEC - now)
        return max(1, math.ceil(max(waits))) if waits else 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_access.py -q && .venv/bin/ruff check sqreader tests && .venv/bin/mypy sqreader`
Expected: alle Tests PASS, einschließlich der Tests aus Task 1; ruff und mypy ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add sqreader/live.py tests/test_live_access.py
git commit -m "Throttle failed moderator logins per address and in total"
```

---

### Task 3: Verteiler (`Hub`) und Nachhol-Regel (`pick`)

**Files:**
- Modify: `sqreader/live.py`
- Test: `tests/test_live_hub.py`

**Interfaces:**
- Consumes: Die Konstante `RING_SIZE` wird in `Hub.__init__` gelesen.
- Produces:
  - `class Hub()` mit diesen Methoden:
    - `publish(line: str, *, full: bool) -> None`
    - die Property `subscribers -> int`
    - `subscribe(limit: int) -> Optional[tuple[int, int, Optional[bytes]]]`, liefert `(cursor, wake, newest_full_payload)`
    - `unsubscribe() -> None`
    - `wait(cursor: int, wake: int, timeout: float) -> tuple[str, list[tuple[int, bool, bytes]], int, int]`, liefert `(status, events, cursor, wake)`; `status` ist `"ok"`, `"gap"` oder `"closed"`
    - `kick() -> None`
    - `close() -> None`
  - `pick(events: list[tuple[int, bool, bytes]]) -> list[tuple[int, bool, bytes]]`
  - Ein Ereignis ist `(seq, is_full, payload)`, `payload` ist `b"data: <json>\n\n"`.

- [ ] **Step 1: Write the failing test**

`tests/test_live_hub.py`:

```python
"""The live map's frame fan-out (sqreader/live.py: Hub, pick)."""
from __future__ import annotations

import threading
import time

from sqreader import live


def _ev(seq, full):
    return (seq, full, f"{'F' if full else 'P'}{seq}".encode())


def _line(n):
    return f'{{"tick": {n}}}\n'


def test_pick_keeps_every_full_frame_and_only_the_newest_position():
    p1, f2, p3, p4 = _ev(1, False), _ev(2, True), _ev(3, False), _ev(4, False)
    f5, p6, p7 = _ev(5, True), _ev(6, False), _ev(7, False)
    assert live.pick([p1, f2, p3, p4, f5, p6, p7]) == [f2, f5, p7]
    assert live.pick([p1, p3]) == [p3]
    assert live.pick([f2]) == [f2]
    assert live.pick([f2, p3]) == [f2, p3]
    assert live.pick([]) == []


def test_a_new_reader_gets_the_newest_full_frame_and_then_everything_new():
    hub = live.Hub()
    hub.publish(_line(1), full=True)
    hub.publish(_line(2), full=False)
    hub.publish(_line(3), full=True)
    cursor, wake, first = hub.subscribe(10)
    assert first == b'data: {"tick": 3}\n\n'
    hub.publish(_line(4), full=False)
    status, events, cursor, wake = hub.wait(cursor, wake, 1)
    assert status == "ok"
    assert [p for _s, _f, p in events] == [b'data: {"tick": 4}\n\n']


def test_nothing_is_buffered_while_nobody_watches():
    hub = live.Hub()
    for n in range(500):
        hub.publish(_line(n), full=False)
    assert len(hub._ring) == 0
    cursor, wake, first = hub.subscribe(10)
    assert first is None                      # no full frame yet
    hub.unsubscribe()
    hub.publish(_line(1), full=True)
    assert len(hub._ring) == 0


def test_a_reader_the_ring_overtook_is_told_so(monkeypatch):
    monkeypatch.setattr(live, "RING_SIZE", 3)
    hub = live.Hub()
    cursor, wake, _ = hub.subscribe(10)
    for n in range(5):
        hub.publish(_line(n), full=True)
    assert hub.wait(cursor, wake, 1)[0] == "gap"


def test_kick_and_close_wake_a_waiting_reader():
    hub = live.Hub()
    cursor, wake, _ = hub.subscribe(10)
    results = []
    t = threading.Thread(target=lambda: results.append(hub.wait(cursor, wake, 10)))
    t.start()
    time.sleep(0.1)
    hub.kick()
    t.join(2)
    assert results
    assert results[0][0] == "ok"
    assert results[0][1] == []
    hub.close()
    assert hub.wait(cursor, results[0][3], 10)[0] == "closed"


def test_the_stream_limit_is_enforced_atomically():
    hub = live.Hub()
    assert hub.subscribe(2) is not None
    assert hub.subscribe(2) is not None
    assert hub.subscribe(2) is None
    hub.unsubscribe()
    assert hub.subscribe(2) is not None
    assert hub.subscribers == 2


def test_publish_never_raises_and_logs_the_first_failure_only(caplog):
    hub = live.Hub()
    hub.publish(None, full=True)          # type: ignore[arg-type]
    hub.publish(None, full=True)          # type: ignore[arg-type]
    assert caplog.text.count("live: publish failed") == 1
    hub.publish(_line(1), full=True)      # and it keeps working
    assert hub.subscribe(10)[2] == b'data: {"tick": 1}\n\n'


def test_publish_stays_fast_with_a_reader_that_never_reads():
    hub = live.Hub()
    hub.subscribe(10)                     # subscribed, never waits
    line = '{"x": "' + "a" * 100_000 + '"}\n'
    t0 = time.perf_counter()
    for _ in range(2000):
        hub.publish(line, full=True)
    assert time.perf_counter() - t0 < 2.0
    assert len(hub._ring) == live.RING_SIZE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_hub.py -q`
Expected: FAIL mit `AttributeError: module 'sqreader.live' has no attribute 'pick'` bzw. `... 'Hub'`

- [ ] **Step 3: Write minimal implementation**

Ans Ende von `sqreader/live.py` anfügen:

```python
class Hub:
    """Hands the reader's frames to the stream threads.

    publish() runs on the reader's main tick thread, the one that also writes
    the recording. It encodes once, appends and notifies; it never touches a
    socket and never raises, so no viewer can slow or break the recorder.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = 0
        # (seq, is_full, payload), filled only while someone is watching
        self._ring: deque[tuple[int, bool, bytes]] = deque(maxlen=RING_SIZE)
        self._last_full: Optional[bytes] = None
        self._subs = 0
        self._wake = 0
        self._closed = False
        self._failed = False

    def publish(self, line: str, *, full: bool) -> None:
        try:
            payload = b"data: " + line.rstrip("\n").encode("utf-8", "replace") + b"\n\n"
            with self._cond:
                self._seq += 1
                if full:
                    self._last_full = payload
                if self._subs:
                    self._ring.append((self._seq, full, payload))
                self._cond.notify_all()
        except Exception:
            if not self._failed:
                self._failed = True
                log.exception("live: publish failed (logged once; the recording is unaffected)")

    @property
    def subscribers(self) -> int:
        with self._cond:
            return self._subs

    def subscribe(self, limit: int) -> Optional[tuple[int, int, Optional[bytes]]]:
        """Admit one stream unless `limit` are open: (cursor, wake, newest full frame)."""
        with self._cond:
            if self._closed or self._subs >= limit:
                return None
            self._subs += 1
            return self._seq, self._wake, self._last_full

    def unsubscribe(self) -> None:
        with self._cond:
            self._subs -= 1
            if self._subs == 0:
                self._ring.clear()

    def wait(self, cursor: int, wake: int,
             timeout: float) -> tuple[str, list[tuple[int, bool, bytes]], int, int]:
        """Block for a new frame, a kick()/close(), or `timeout` seconds.

        Returns (status, events, cursor, wake). status is 'ok', 'gap' (the ring
        overtook this reader, so frames are missing) or 'closed'.
        """
        with self._cond:
            self._cond.wait_for(
                lambda: self._seq > cursor or self._wake != wake or self._closed,
                timeout=timeout)
            if self._closed:
                return "closed", [], cursor, self._wake
            events = [e for e in self._ring if e[0] > cursor]
            if self._seq > cursor and (not events or events[0][0] != cursor + 1):
                return "gap", [], cursor, self._wake
            return "ok", events, (events[-1][0] if events else cursor), self._wake

    def kick(self) -> None:
        """Wake every stream so it re-checks its session now."""
        with self._cond:
            self._wake += 1
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


def pick(events: list[tuple[int, bool, bytes]]) -> list[tuple[int, bool, bytes]]:
    """What a reader that fell behind still needs.

    Every full frame, because each one carries the kill events that the kill
    feed attributes, plus the newest position frame after the last full one.
    Older position frames are superseded.
    """
    fulls = [e for e in events if e[1]]
    last_full = fulls[-1][0] if fulls else -1
    tail = [e for e in events if not e[1] and e[0] > last_full]
    return fulls + tail[-1:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_hub.py tests/test_live_access.py -q && .venv/bin/ruff check sqreader tests && .venv/bin/mypy sqreader`
Expected: alle Tests PASS, ruff und mypy ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add sqreader/live.py tests/test_live_hub.py
git commit -m "Fan frames out to live viewers without ever blocking the reader"
```

---

### Task 4: HTTP-Endpunkte für Session, Login und Logout, plus Hooks in `httpsrv.py`

**Files:**
- Modify: `sqreader/live.py`
- Modify: `sqreader/httpsrv.py:346-357` (Signatur `_make_handler`), `:424-429` (Ende von `do_GET`), `:1044` (`return _H`), `:1053-1086` (`serve_in_background`)
- Create: `tests/live_helpers.py`
- Test: `tests/test_live_http.py`

**Interfaces:**
- Consumes: `Access`, `client_key`, `Hub` aus den Tasks 1 bis 3.
- Produces:
  - `cookie_values(headers: Any) -> list[str]`
  - `class LiveMap(password: str)` mit den Attributen `hub: Hub` und `access: Access` und diesen Methoden:
    - `publish(line: str, *, full: bool) -> None`
    - `handle_get(h, path: str) -> None`
    - `handle_post(h, path: str) -> None`
  - `live_from_config(value: Any) -> Optional[LiveMap]`
  - `serve_in_background(..., live: Any = None)`
  - Helfer in `tests/live_helpers.py`: `PW`, `running(live_map=None, **kw)` (liefert den Port), `request(port, method, path, body=None, headers=None) -> (status, headers, body)`, `login(port, password=PW, headers=None) -> (status, token_or_None, headers)`, `cookie(token) -> dict`, `raw(port, data: bytes) -> bytes`, `without_date(resp: bytes) -> bytes`

- [ ] **Step 1: Write the failing test**

`tests/live_helpers.py`:

```python
"""Shared helpers for the live-map HTTP tests: a real server, raw sockets."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from contextlib import contextmanager

from sqreader import live
from sqreader.httpsrv import _TickBeat, serve_in_background

PW = "correct-horse-battery-staple-42"


@contextmanager
def running(live_map=None, **kw):
    """A real server on a free port; yields the port."""
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(), live=live_map, **kw)
    try:
        yield srv.server_address[1]
    finally:
        if live_map is not None:
            live_map.hub.close()
        srv.shutdown()
        srv.server_close()


def request(port, method, path, body=None, headers=None):
    """(status, headers, body). HTTP errors are answers here, not exceptions."""
    if body is None:
        data = None
    elif isinstance(body, bytes):
        data = body
    else:
        data = json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json"} if body is not None else {}
    hdrs.update(headers or {})
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def login(port, password=PW, headers=None):
    """(status, token or None, headers)."""
    status, hdrs, _ = request(port, "POST", "/api/live/login", {"password": password}, headers)
    m = re.match(rf"{live.COOKIE}=([^;]+);", hdrs.get("Set-Cookie") or "")
    return status, (m.group(1) if status == 200 and m else None), hdrs


def cookie(token):
    return {"Cookie": f"{live.COOKIE}={token}"}


def raw(port, data: bytes) -> bytes:
    """Send bytes and read until the server closes the connection."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall(data)
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                return buf
            buf += chunk
    finally:
        s.close()


def without_date(response: bytes) -> bytes:
    return b"\r\n".join(ln for ln in response.split(b"\r\n") if not ln.startswith(b"Date: "))
```

`tests/test_live_http.py`:

```python
"""HTTP surface of the moderator live map: absent without a password, and
login/logout/session behind one (sqreader/live.py and the httpsrv.py hooks)."""
from __future__ import annotations

import json
import re
import socket
import time

import pytest

from live_helpers import PW, cookie, login, raw, request, running, without_date
from sqreader import live

WRONG = "nope-nope-nope-nope-1"


def _get(path):
    return f"GET {path} HTTP/1.1\r\nHost: t\r\nConnection: close\r\n\r\n".encode()


# -- off means absent ---------------------------------------------------------

def test_without_a_password_the_live_paths_are_any_unknown_path(tmp_path):
    with running(None, recordings_dir=tmp_path) as port:
        unknown = without_date(raw(port, _get("/nope")))
        for path in ("/api/live/session", "/api/live/stream", "/api/live/login"):
            assert without_date(raw(port, _get(path))) == unknown, path


@pytest.mark.parametrize("method", ["POST", "HEAD", "OPTIONS"])
def test_without_a_password_other_methods_are_stdlibs_501(tmp_path, method):
    with running(None, recordings_dir=tmp_path) as port:
        resp = raw(port, f"{method} /api/live/login HTTP/1.1\r\nHost: t\r\n"
                         "Content-Length: 0\r\n\r\n".encode())
    assert resp.startswith(f"HTTP/1.0 501 Unsupported method ('{method}')".encode())


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_with_a_password_head_and_options_stay_stdlibs_501(method):
    # The CSRF argument rests on this: a cross-site JSON POST needs a CORS
    # preflight, and the preflight (OPTIONS) must keep failing.
    with running(live.LiveMap(PW)) as port:
        resp = raw(port, f"{method} /api/live/login HTTP/1.1\r\nHost: t\r\n\r\n".encode())
    assert resp.startswith(f"HTTP/1.0 501 Unsupported method ('{method}')".encode())


def test_with_a_password_post_elsewhere_is_byte_identical(tmp_path):
    req = b"POST /api/recordings HTTP/1.1\r\nHost: t\r\nContent-Length: 0\r\n\r\n"
    with running(None, recordings_dir=tmp_path) as port:
        off = without_date(raw(port, req))
    with running(live.LiveMap(PW), recordings_dir=tmp_path) as port:
        on = without_date(raw(port, req))
    assert on == off


def test_invalid_config_disables_live_and_never_logs_the_value(caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    assert live.live_from_config(None) is None
    assert caplog.text == ""
    assert live.live_from_config("too-short-secret") is None
    assert "at least 20 characters" in caplog.text
    assert "too-short-secret" not in caplog.text
    assert isinstance(live.live_from_config(PW), live.LiveMap)
    assert "live map enabled for moderators (login via ?mode=live)" in caplog.text


def test_legacy_live_paths_stay_404_with_live_enabled(tmp_path):
    with running(live.LiveMap(PW), recordings_dir=tmp_path) as port:
        for path in ("/stream", "/latest", "/api/alerts", "/api/live/nope"):
            assert request(port, "GET", path)[0] == 404, path


# -- login, session, logout ---------------------------------------------------

COOKIE_RE = re.compile(
    rf"^{live.COOKIE}=[A-Za-z0-9_-]{{43}}; Max-Age=43200; HttpOnly; Secure; SameSite=Strict$")


def test_login_sets_a_strict_session_cookie():
    with running(live.LiveMap(PW)) as port:
        status, token, hdrs = login(port)
        assert status == 200
        assert COOKIE_RE.match(hdrs["Set-Cookie"]), hdrs["Set-Cookie"]
        assert hdrs["Cache-Control"] == "no-store"
        st, _, body = request(port, "GET", "/api/live/session", headers=cookie(token))
        assert (st, json.loads(body)) == (200, {"authenticated": True})


def test_a_wrong_password_always_gets_the_same_401():
    with running(live.LiveMap(PW)) as port:
        a = request(port, "POST", "/api/live/login", {"password": WRONG})
        b = request(port, "POST", "/api/live/login", {"password": "x"})
    assert a[0] == b[0] == 401
    assert a[2] == b[2] == b'{"error": "wrong password"}'


def test_logout_ends_the_session_and_clears_the_cookie():
    with running(live.LiveMap(PW)) as port:
        _, token, _ = login(port)
        st, hdrs, body = request(port, "POST", "/api/live/logout", {}, cookie(token))
        assert st == 200
        assert json.loads(body) == {"authenticated": False}
        assert hdrs["Set-Cookie"] == (
            f"{live.COOKIE}=; Max-Age=0; HttpOnly; Secure; SameSite=Strict")
        _, _, body = request(port, "GET", "/api/live/session", headers=cookie(token))
        assert json.loads(body) == {"authenticated": False}


def test_a_non_ascii_password_works_over_http():
    pw = "pässwörter-sind-lang-genug-€"
    with running(live.LiveMap(pw)) as port:
        utf8 = json.dumps({"password": pw}, ensure_ascii=False).encode("utf-8")
        assert request(port, "POST", "/api/live/login", utf8)[0] == 200
        assert request(port, "POST", "/api/live/login", {"password": pw})[0] == 200


def test_the_throttle_answers_429_with_retry_after():
    with running(live.LiveMap(PW)) as port:
        for _ in range(live.FAILS_PER_CLIENT):
            request(port, "POST", "/api/live/login", {"password": WRONG})
        st, hdrs, _ = request(port, "POST", "/api/live/login", {"password": PW})
    assert st == 429
    assert 1 <= int(hdrs["Retry-After"]) <= live.FAIL_WINDOW_SEC


def test_live_responses_are_never_cached_and_never_cors():
    with running(live.LiveMap(PW), cors_origin="*") as port:
        _, token, first = login(port)
        answers = [first,
                   request(port, "GET", "/api/live/session")[1],
                   request(port, "POST", "/api/live/logout", {}, cookie(token))[1],
                   request(port, "POST", "/api/live/login", {"password": WRONG})[1]]
    for hdrs in answers:
        assert hdrs.get("Access-Control-Allow-Origin") is None
        assert hdrs["Cache-Control"] == "no-store"


# -- input rules --------------------------------------------------------------

def _post_raw(port, body: bytes, ctype="application/json", length=True):
    head = f"POST /api/live/login HTTP/1.1\r\nHost: t\r\nContent-Type: {ctype}\r\n"
    if length:
        head += f"Content-Length: {len(body)}\r\n"
    return raw(port, head.encode() + b"\r\n" + body)


@pytest.mark.parametrize("body, ctype, length, status", [
    (b'{"password": "x"}', "text/plain", True, b"415"),
    (b'{"password": "x"}', "application/json", False, b"411"),
    (b"{" + b" " * 1100 + b"}", "application/json", True, b"413"),
    (b"not json", "application/json", True, b"400"),
    (b"[]", "application/json", True, b"400"),
    (b"{}", "application/json", True, b"400"),
    (b'{"password": ""}', "application/json", True, b"400"),
    (b'{"password": 5}', "application/json", True, b"400"),
    (b"[" * 1024, "application/json", True, b"400"),     # RecursionError on 3.10/3.11
])
def test_login_input_rules(body, ctype, length, status):
    with running(live.LiveMap(PW)) as port:
        resp = _post_raw(port, body, ctype, length)
    assert resp.split(b" ", 2)[1] == status, resp[:80]


def test_a_trickled_body_is_dropped_at_the_deadline(monkeypatch):
    monkeypatch.setattr(live, "BODY_DEADLINE_SEC", 0.5)
    with running(live.LiveMap(PW)) as port:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            s.sendall(b"POST /api/live/login HTTP/1.1\r\nHost: t\r\n"
                      b"Content-Type: application/json\r\nContent-Length: 50\r\n\r\n")
            s.settimeout(0.2)
            t0 = time.monotonic()
            closed = False
            while not closed and time.monotonic() - t0 < 3:
                try:
                    s.sendall(b" ")          # one byte per 0.2 s: every recv is quick
                    closed = s.recv(1024) == b""
                except TimeoutError:
                    pass                     # no answer yet; keep trickling
                except OSError:
                    closed = True            # a reset is closed as well
            assert closed
            assert time.monotonic() - t0 < 2
        finally:
            s.close()


# -- the existing gates hold for moderators too -------------------------------

def test_the_recording_and_stats_gates_hold_for_a_logged_in_moderator(tmp_path):
    from test_public_no_live import _make_sqrx, _seed_two_matches
    _make_sqrx(tmp_path, "active", state="active", in_progress=True)
    db = tmp_path / "stats.db"
    _seed_two_matches(db)
    with running(live.LiveMap(PW), recordings_dir=tmp_path, stats_db=db) as port:
        _, token, _ = login(port)
        c = cookie(token)
        assert request(port, "GET", "/api/recording/active", headers=c)[0] == 404
        assert request(port, "GET", "/api/recordings", headers=c)[2] == b"[]"
        _, _, body = request(port, "GET", "/api/matches", headers=c)
        assert "mopen" not in [m["match_id"] for m in json.loads(body)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_http.py -q`
Expected: FAIL mit `TypeError: serve_in_background() got an unexpected keyword argument 'live'`

- [ ] **Step 3: Write minimal implementation**

In `sqreader/live.py` kommt `json` in den Import-Block, nach `ipaddress`:

```python
import json
```

Direkt hinter `client_key()` kommt diese Funktion:

```python
def cookie_values(headers: Any) -> list[str]:
    """Every sqr_live value the browser sent, in order."""
    values = []
    for header in headers.get_all("Cookie") or []:
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name == COOKIE and value:
                values.append(value)
    return values
```

Ans Ende von `sqreader/live.py` anfügen:

```python
def _json(h: Any, code: int, obj: Any, extra: Optional[dict[str, str]] = None) -> None:
    body = json.dumps(obj).encode("utf-8")
    try:
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            h.send_header(name, value)
        h.end_headers()
        h.wfile.write(body)
    except OSError:
        pass                      # the client hung up; nobody left to tell


def _read_body(h: Any) -> Optional[bytes]:
    """The request body, or None after answering 411/413 or dropping a client
    that trickles it. The deadline covers the WHOLE body: a per-recv timeout
    would let one byte every 9 s hold a thread for hours."""
    raw = h.headers.get("Content-Length")
    try:
        length = int(raw) if raw is not None else -1
    except ValueError:
        length = -1
    if length < 0:
        _json(h, 411, {"error": "length required"})
        return None
    if length > BODY_MAX:
        _json(h, 413, {"error": "body too large"})
        return None
    deadline = time.monotonic() + BODY_DEADLINE_SEC
    body = b""
    try:
        while len(body) < length:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            h.connection.settimeout(left)
            chunk = h.rfile.read1(length - len(body))
            if not chunk:
                raise ConnectionError
            body += chunk
    except OSError:               # TimeoutError and ConnectionError included
        h.close_connection = True
        return None
    return body


class LiveMap:
    """The /api/live/* endpoints, fed by the reader through publish()."""

    def __init__(self, password: str) -> None:
        self.hub = Hub()
        self.access = Access(password)

    def publish(self, line: str, *, full: bool) -> None:
        self.hub.publish(line, full=full)

    def handle_get(self, h: Any, path: str) -> None:
        if path == "/api/live/session":
            token = self.access.first_valid(cookie_values(h.headers))
            _json(h, 200, {"authenticated": token is not None})
        else:
            h.send_error(404, "no such endpoint")

    def handle_post(self, h: Any, path: str) -> None:
        if path not in ("/api/live/login", "/api/live/logout"):
            # Byte for byte what stdlib answers when there is no do_POST at all.
            h.send_error(501, "Unsupported method ('POST')")
            return
        body = _read_body(h)
        if body is None:
            return
        ctype = (h.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            # A cross-site form cannot send JSON, and a cross-site fetch() that
            # does needs a preflight, which OPTIONS (stdlib's 501) refuses.
            _json(h, 415, {"error": "unsupported media type"})
            return
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:         # RecursionError on 3.10/3.11 for '[' * 1024, too
            data = None
        if not isinstance(data, dict):
            _json(h, 400, {"error": "bad request"})
            return
        client = client_key(h.headers, h.client_address[0])
        presented = cookie_values(h.headers)
        if path == "/api/live/logout":
            self._logout(h, client, presented)
        else:
            self._login(h, data, client, presented)

    def _login(self, h: Any, data: dict, client: str, presented: list[str]) -> None:
        password = data.get("password")
        if not isinstance(password, str) or not password:
            _json(h, 400, {"error": "bad request"})
            return
        result, value = self.access.login(password, client, presented)
        if result == "limited":
            _json(h, 429, {"error": "too many attempts"}, {"Retry-After": str(value)})
        elif result == "wrong":
            _json(h, 401, {"error": "wrong password"})
        else:
            cookie = (f"{COOKIE}={value}; Max-Age={int(SESSION_TTL_SEC)}; "
                      "HttpOnly; Secure; SameSite=Strict")
            _json(h, 200, {"authenticated": True}, {"Set-Cookie": cookie})

    def _logout(self, h: Any, client: str, presented: list[str]) -> None:
        if self.access.revoke(presented):
            log.info("live: logout from %s", client)
            self.hub.kick()
        _json(h, 200, {"authenticated": False},
              {"Set-Cookie": f"{COOKIE}=; Max-Age=0; HttpOnly; Secure; SameSite=Strict"})


def live_from_config(value: Any) -> Optional[LiveMap]:
    """The live map for this config value, or None, and then it does not exist."""
    password, reason = validate_password(value)
    if password is None:
        if reason:
            log.warning("%s", reason)
        return None
    log.info("live map enabled for moderators (login via ?mode=live)")
    return LiveMap(password)
```

In `sqreader/httpsrv.py` kommen vier Einfügungen dazu. Es gibt keinen neuen Import, denn `Any` ist schon importiert.

1. In der Signatur von `_make_handler` nach `stats_db: Optional[Path] = None,`:

```python
    live: Any = None,
```

2. In `do_GET` direkt vor dem abschließenden `else:`, also vor `self.send_error(404, "no such endpoint")`:

```python
            elif live is not None and path.startswith("/api/live/"):
                live.handle_get(self, path)
```

3. Direkt vor `    return _H`:

```python
    if live is not None:
        # Fork-only moderator live map (sqreader/live.py). POST exists only
        # while it is enabled, so a build without live_password keeps stdlib's 501.
        def do_POST(self: Any) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            live.handle_post(self, self.path.split("?", 1)[0])
        _H.do_POST = do_POST  # type: ignore[attr-defined]
```

4. In der Signatur von `serve_in_background` nach `stats_db: Optional[Path] = None,`:

```python
                        live: Any = None,
```

   Im Aufruf von `_make_handler` in `serve_in_background` wird `stats_db` um `live` ergänzt:

```python
        _make_handler(heartbeat, recordings_dir, meta_cache, icons_dir,
                      sqmaps_dir, frontend_dir, health_provider,
                      stale_after_sec, cors_origin, stats_db, live),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_http.py tests/test_public_no_live.py tests/test_http_versions.py -q && .venv/bin/ruff check sqreader tests && .venv/bin/mypy sqreader`
Expected: alle Tests PASS, auch die unveränderten Tests `test_public_no_live` und `test_http_versions`; ruff und mypy ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add sqreader/live.py sqreader/httpsrv.py tests/live_helpers.py tests/test_live_http.py
git commit -m "Serve the live login behind a configured password only"
```

---

### Task 5: Der Live-Stream (`GET /api/live/stream`)

**Files:**
- Modify: `sqreader/live.py` (`LiveMap.__init__`, `LiveMap.handle_get`, neue Methoden)
- Modify: `tests/live_helpers.py` (Stream-Helfer anfügen)
- Test: `tests/test_live_stream.py`

**Interfaces:**
- Consumes: `Hub.subscribe`/`wait`/`unsubscribe`, `pick`, `Access.first_valid`/`valid`, `client_key`, `cookie_values` und `_json` aus den Tasks 3 und 4.
- Produces:
  - Die Route `GET /api/live/stream` und `_fmt_duration(seconds: float) -> str`.
  - Helfer in `tests/live_helpers.py`:
    - `full(tick, pad=0) -> str` und `pos(tick) -> str`
    - `event(line) -> bytes`
    - `wait_for(pred, timeout=3) -> bool`
    - `class Stream(port, token, rcvbuf=None)` mit `head()`, `event(timeout=5)`, `closed_within(seconds) -> bool` und `close()`

- [ ] **Step 1: Write the failing test**

Ans Ende von `tests/live_helpers.py` anfügen, dazu `import time` in den Import-Block:

```python
def full(tick, pad=0):
    return json.dumps({"tick": tick, "players": [], "damageEvents": [], "pad": "x" * pad}) + "\n"


def pos(tick):
    return json.dumps({"t": "pos", "tick": tick, "players": [], "vehicles": []}) + "\n"


def event(line):
    return b"data: " + line.rstrip("\n").encode() + b"\n\n"


def wait_for(pred, timeout=3):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


class Stream:
    """One open GET /api/live/stream, read incrementally."""

    def __init__(self, port, token, rcvbuf=None):
        self.s = socket.socket()
        if rcvbuf:
            self.s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.s.settimeout(5)
        self.s.connect(("127.0.0.1", port))
        hdr = f"Cookie: {live.COOKIE}={token}\r\n" if token else ""
        self.s.sendall(f"GET /api/live/stream HTTP/1.1\r\nHost: t\r\n{hdr}\r\n".encode())
        self.buf = b""

    def _until(self, marker, timeout):
        self.s.settimeout(timeout)
        while marker not in self.buf:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError(self.buf)
            self.buf += chunk
        i = self.buf.index(marker) + len(marker)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def head(self):
        return self._until(b"\r\n\r\n", 5)

    def event(self, timeout=5):
        return self._until(b"\n\n", timeout)

    def closed_within(self, seconds):
        self.s.settimeout(seconds)
        try:
            while True:
                chunk = self.s.recv(65536)
                if not chunk:
                    return True
                self.buf += chunk
        except TimeoutError:
            return False
        except OSError:
            return True

    def close(self):
        self.s.close()
```

`tests/test_live_stream.py`:

```python
"""The moderator live stream (Server-Sent Events) end to end over a real socket."""
from __future__ import annotations

import json
import time

from live_helpers import (
    PW, Stream, cookie, event, full, login, pos, request, running, wait_for,
)
from sqreader import live


def _open(port, token, **kw):
    st = Stream(port, token, **kw)
    return st, st.head()


def test_no_session_means_401_and_no_slot():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        st, hdrs, body = request(port, "GET", "/api/live/stream")
        assert (st, json.loads(body)) == (401, {"error": "not logged in"})
        assert hdrs["Cache-Control"] == "no-store"
        assert request(port, "GET", "/api/live/stream", headers=cookie("forged"))[0] == 401
        assert lm.hub.subscribers == 0


def test_the_head_is_a_plain_http10_event_stream():
    lm = live.LiveMap(PW)
    lm.publish(full(1), full=True)
    with running(lm, cors_origin="*") as port:
        _, token, _ = login(port)
        st, head = _open(port, token)
        try:
            lines = head.split(b"\r\n")
            assert lines[0] == b"HTTP/1.0 200 OK"
            h = {}
            for ln in lines[1:]:
                if ln:
                    name, _, value = ln.decode().partition(": ")
                    h[name.lower()] = value
            assert h["content-type"] == "text/event-stream; charset=utf-8"
            assert h["cache-control"] == "no-store"
            assert h["x-accel-buffering"] == "no"
            assert h["connection"] == "close"
            for absent in ("content-length", "transfer-encoding", "content-encoding",
                           "access-control-allow-origin"):
                assert absent not in h
            assert st.event() == b"retry: 3000\n\n"
            assert st.event() == event(full(1))
        finally:
            st.close()


def test_frames_arrive_in_order_as_they_are_published():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.event() == b"retry: 3000\n\n"
            for line, is_full in ((full(1), True), (pos(2), False), (full(3), True)):
                lm.publish(line, full=is_full)
                assert st.event() == event(line)
        finally:
            st.close()


def test_before_any_frame_the_stream_keeps_alive_then_delivers(monkeypatch):
    monkeypatch.setattr(live, "KEEPALIVE_SEC", 0.3)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.event() == b"retry: 3000\n\n"
            assert st.event(timeout=2) == b": ka\n\n"
            lm.publish(full(1), full=True)
            assert st.event() == event(full(1))
        finally:
            st.close()


def test_a_large_frame_arrives_intact():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            st.event()
            big = full(1, pad=300_000)
            lm.publish(big, full=True)
            assert st.event() == event(big)
        finally:
            st.close()


def test_logout_ends_that_sessions_stream_at_once_and_only_that_one():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, a, _ = login(port)
        _, b, _ = login(port)
        sa, _ = _open(port, a)
        sb, _ = _open(port, b)
        try:
            sa.event()
            sb.event()
            request(port, "POST", "/api/live/logout", {}, cookie(a))
            assert sa.closed_within(2)            # not after the 15 s keepalive
            lm.publish(full(1), full=True)
            assert sb.event() == event(full(1))
        finally:
            sa.close()
            sb.close()


def test_an_expired_session_ends_an_idle_stream(monkeypatch):
    monkeypatch.setattr(live, "SESSION_TTL_SEC", 0.5)
    monkeypatch.setattr(live, "KEEPALIVE_SEC", 0.2)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.closed_within(3)
        finally:
            st.close()


def test_close_ends_every_stream():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            st.event()
            lm.hub.close()
            assert st.closed_within(2)
        finally:
            st.close()


def test_the_stream_limit_answers_503(monkeypatch):
    monkeypatch.setattr(live, "MAX_STREAMS", 2)
    monkeypatch.setattr(live, "KEEPALIVE_SEC", 0.2)     # so a gone client is noticed fast
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        s1, _ = _open(port, token)
        s2, _ = _open(port, token)
        try:
            st, hdrs, body = request(port, "GET", "/api/live/stream", headers=cookie(token))
            assert (st, hdrs["Retry-After"]) == (503, "30")
            assert json.loads(body) == {"error": "too many live viewers"}
            s1.close()
            assert wait_for(lambda: lm.hub.subscribers == 1)
        finally:
            s1.close()
            s2.close()


def test_refused_streams_are_logged_at_most_once_a_minute(monkeypatch, caplog):
    monkeypatch.setattr(live, "MAX_STREAMS", 1)
    caplog.set_level("WARNING", logger="sqreader.live")
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            for _ in range(5):
                assert request(port, "GET", "/api/live/stream", headers=cookie(token))[0] == 503
            assert caplog.text.count("live: stream refused from 127.0.0.1 (1/1 in use)") == 1
        finally:
            st.close()


def test_a_reader_that_stops_reading_is_dropped_and_never_slows_publish(monkeypatch):
    monkeypatch.setattr(live, "WRITE_TIMEOUT_SEC", 0.5)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token, rcvbuf=4096)       # and then never read again
        try:
            big = full(1, pad=256_000)
            worst = 0.0
            for _ in range(60):
                t0 = time.perf_counter()
                lm.publish(big, full=True)
                worst = max(worst, time.perf_counter() - t0)
                time.sleep(0.02)
            assert worst < 0.05
            assert wait_for(lambda: lm.hub.subscribers == 0, timeout=5)
        finally:
            st.close()


def test_a_reader_the_ring_overtook_is_disconnected(monkeypatch, caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    lm = live.LiveMap(PW)
    real_wait = lm.hub.wait
    calls = []

    def gap_once(cursor, wake, timeout):
        if not calls:
            calls.append(1)
            return "gap", [], cursor, wake
        return real_wait(cursor, wake, timeout)

    monkeypatch.setattr(lm.hub, "wait", gap_once)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.closed_within(2)
        finally:
            st.close()
    assert wait_for(lambda: "(too slow)" in caplog.text)


def test_streams_are_logged_with_client_count_and_reason(caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        st.event()
        lm.hub.close()
        assert st.closed_within(2)
        st.close()
    assert "live: stream opened from 127.0.0.1 (1/10)" in caplog.text
    assert wait_for(lambda: "live: stream closed from 127.0.0.1 after 0m0" in caplog.text)
    assert "(server stopping)" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_stream.py -q`
Expected: FAIL. `/api/live/stream` liefert noch 404; `test_no_session_means_401_and_no_slot` erwartet 401.

- [ ] **Step 3: Write minimal implementation**

In `sqreader/live.py` wird `LiveMap.__init__` so ersetzt:

```python
    def __init__(self, password: str) -> None:
        self.hub = Hub()
        self.access = Access(password)
        self._refused_lock = threading.Lock()
        self._refused_at = float("-inf")      # monotonic time of the last refusal log
```

`LiveMap.handle_get` wird so ersetzt:

```python
    def handle_get(self, h: Any, path: str) -> None:
        if path == "/api/live/session":
            token = self.access.first_valid(cookie_values(h.headers))
            _json(h, 200, {"authenticated": token is not None})
        elif path == "/api/live/stream":
            self._stream(h)
        else:
            h.send_error(404, "no such endpoint")
```

In die Klasse `LiveMap` kommen diese Methoden, hinter `_logout`:

```python
    def _stream(self, h: Any) -> None:
        token = self.access.first_valid(cookie_values(h.headers))
        if token is None:
            _json(h, 401, {"error": "not logged in"})
            return
        client = client_key(h.headers, h.client_address[0])
        admitted = self.hub.subscribe(MAX_STREAMS)
        if admitted is None:
            self._log_refused(client)
            _json(h, 503, {"error": "too many live viewers"}, {"Retry-After": "30"})
            return
        cursor, wake, first = admitted
        log.info("live: stream opened from %s (%d/%d)", client, self.hub.subscribers,
                 MAX_STREAMS)
        started = time.monotonic()
        reason = "client gone"
        try:
            h.connection.settimeout(WRITE_TIMEOUT_SEC)
            # HTTP/1.0 (the handler's default, never switched here): the body is
            # delimited by the close, so neither Content-Length nor chunking.
            h.send_response(200)
            h.send_header("Content-Type", "text/event-stream; charset=utf-8")
            h.send_header("Cache-Control", "no-store")
            h.send_header("X-Accel-Buffering", "no")
            h.send_header("Connection", "close")
            h.end_headers()
            h.wfile.write(f"retry: {RETRY_MS}\n\n".encode("ascii") + (first or b""))
            last_write = time.monotonic()
            while True:
                idle = time.monotonic() - last_write
                status, events, cursor, wake = self.hub.wait(
                    cursor, wake, max(0.0, KEEPALIVE_SEC - idle))
                if status == "closed":
                    reason = "server stopping"
                    break
                if status == "gap":
                    reason = "too slow"
                    break
                if not self.access.valid(token):
                    reason = "session ended"
                    break
                if events:
                    h.wfile.write(b"".join(payload for _s, _f, payload in pick(events)))
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= KEEPALIVE_SEC:
                    h.wfile.write(b": ka\n\n")
                    last_write = time.monotonic()
        except TimeoutError:
            reason = "write timeout"
        except OSError:
            reason = "client gone"
        finally:
            self.hub.unsubscribe()
            h.close_connection = True
            log.info("live: stream closed from %s after %s (%s)", client,
                     _fmt_duration(time.monotonic() - started), reason)

    def _log_refused(self, client: str) -> None:
        now = time.monotonic()
        with self._refused_lock:
            if now - self._refused_at < REFUSED_LOG_INTERVAL_SEC:
                return
            self._refused_at = now
        log.warning("live: stream refused from %s (%d/%d in use)", client,
                    MAX_STREAMS, MAX_STREAMS)
```

Direkt vor `class LiveMap` kommt diese Funktion:

```python
def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m{s % 60:02d}s"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_stream.py tests/test_live_http.py tests/test_live_hub.py tests/test_live_access.py -q && .venv/bin/ruff check sqreader tests && .venv/bin/mypy sqreader`
Expected: alle Tests PASS, ruff und mypy ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add sqreader/live.py tests/live_helpers.py tests/test_live_stream.py
git commit -m "Stream the running round to logged-in moderators"
```

---

### Task 6: SIGHUP-Widerruf und Verdrahtung in `cli.py`

**Files:**
- Modify: `sqreader/live.py` (Imports, `config_path`, `LiveMap.on_sighup`, `LiveMap.reload`)
- Modify: `sqreader/cli.py` in `cmd_serve`: vor `:937` (`srv = serve_in_background(...)`), `:948` (letztes Argument), `:1089-1090` (`_consume_full`), `:1162-1163` (Positionsframe), `:1022-1023` (Signale)
- Test: `tests/test_live_reload.py`

**Interfaces:**
- Consumes: `LiveMap`, `Access.reset`, `Hub.kick`, `validate_password`.
- Produces:
  - `config_path() -> Path`
  - `LiveMap.reload() -> None`
  - `LiveMap.on_sighup(_signum: int, _frame: Any) -> None`
  - Die Hooks in `cmd_serve`

- [ ] **Step 1: Write the failing test**

`tests/test_live_reload.py`:

```python
"""SIGHUP revokes every moderator session and re-reads live_password; and the
live map is wired into `sqreader serve`."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import sqreader
from live_helpers import PW, Stream, cookie, login, request, running
from sqreader import live

NEW = "a-brand-new-password-for-mods"


def _config(tmp_path, monkeypatch, content):
    path = tmp_path / "sqreader.config.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("SQREADER_CONFIG", str(path))
    return path


def test_reload_rotates_the_password_and_revokes_everyone(tmp_path, monkeypatch, caplog):
    _config(tmp_path, monkeypatch, json.dumps({"live_password": NEW}))
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        lm.reload()
        _, _, body = request(port, "GET", "/api/live/session", headers=cookie(token))
        assert json.loads(body) == {"authenticated": False}
        assert login(port, PW)[0] == 401
        assert login(port, NEW)[0] == 200
    assert "live: SIGHUP: all sessions revoked, password reloaded" in caplog.text


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"live_password": "short"}),
    json.dumps({}),
    json.dumps(["x"]),
])
def test_a_broken_config_fails_closed(tmp_path, monkeypatch, caplog, content):
    path = _config(tmp_path, monkeypatch, content)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        lm.reload()
        assert login(port, PW)[0] == 401
    assert f"NO valid live_password in {path}" in caplog.text


def test_a_missing_config_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SQREADER_CONFIG", str(tmp_path / "gone.json"))
    lm = live.LiveMap(PW)
    lm.reload()
    assert lm.access.login(PW, "c", [])[0] == "wrong"


def test_config_path_follows_config_py(tmp_path, monkeypatch):
    monkeypatch.delenv("SQREADER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert live.config_path() == tmp_path / "sqreader.config.json"
    monkeypatch.setenv("SQREADER_CONFIG", "/etc/x.json")
    assert live.config_path() == Path("/etc/x.json")


def test_sighup_hands_the_reload_to_a_thread(monkeypatch):
    lm = live.LiveMap(PW)
    seen = []
    done = threading.Event()

    def fake_reload():
        seen.append(threading.current_thread().name)
        done.set()

    monkeypatch.setattr(lm, "reload", fake_reload)
    lm.on_sighup(1, None)
    assert done.wait(2)
    assert seen == ["sqreader-live-reload"]


def test_reload_ends_open_streams(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, json.dumps({"live_password": NEW}))
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st = Stream(port, token)
        try:
            st.head()
            st.event()
            lm.reload()
            assert st.closed_within(2)
        finally:
            st.close()


def test_cli_wires_the_live_map_into_serve():
    """Merge guard: upstream edits cmd_serve often, and a merge that drops one
    of these lines leaves the live map silently dead or silently frozen."""
    src = (Path(sqreader.__file__).parent / "cli.py").read_text(encoding="utf-8")
    for needle in ('live = live_from_config(config.get("live_password"))',
                   "live=live",
                   "live.publish(line, full=True)",
                   "live.publish(pos_line, full=False)",
                   "signal.signal(signal.SIGHUP, live.on_sighup)"):
        assert needle in src, needle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_reload.py -q`
Expected: FAIL mit `AttributeError: 'LiveMap' object has no attribute 'reload'` bzw. `module 'sqreader.live' has no attribute 'config_path'`. Der Test `test_cli_wires_the_live_map_into_serve` schlägt ebenfalls fehl.

- [ ] **Step 3: Write minimal implementation**

In `sqreader/live.py` kommen zwei Imports in den Import-Block: `import os` nach `import math`, und `from pathlib import Path` nach `from collections import deque`.

Direkt vor `class LiveMap` kommt diese Funktion:

```python
def config_path() -> Path:
    """Where config.py reads the config from (config._load), kept in step by
    hand: SIGHUP re-reads live_password alone and must not reset the cache
    that every other key was read from."""
    env = os.environ.get("SQREADER_CONFIG")
    return Path(env) if env else Path.cwd() / "sqreader.config.json"
```

In die Klasse `LiveMap` kommen diese Methoden, hinter `publish`:

```python
    def on_sighup(self, _signum: int, _frame: Any) -> None:
        # Runs between two bytecodes of the main thread, which may be inside
        # publish() or a log call. Taking locks here could deadlock, so a
        # thread does the work.
        threading.Thread(target=self.reload, name="sqreader-live-reload",
                         daemon=True).start()

    def reload(self) -> None:
        """Revoke every session and re-read live_password. Fails closed: a file
        that cannot be read, or holds no valid password, disables logins."""
        path = config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            value = data.get("live_password") if isinstance(data, dict) else None
        except Exception:
            value = None
        password, _reason = validate_password(value)
        self.access.reset(password)
        self.hub.kick()
        if password is None:
            log.warning("live: SIGHUP: all sessions revoked; NO valid live_password in %s, "
                        "logins disabled until fixed", path)
        else:
            log.warning("live: SIGHUP: all sessions revoked, password reloaded")
```

`sqreader/cli.py` in `cmd_serve` bekommt fünf Änderungen.

1. Direkt vor `    srv = serve_in_background(args.host, args.port, beat,`:

```python
    # Fork-only moderator live map: None (absent) unless live_password is set.
    from .live import live_from_config
    live = live_from_config(config.get("live_password"))
```

2. Im Aufruf `serve_in_background(...)` wird die letzte Zeile `stats_db=stats_db_path)` ersetzt durch:

```python
                              stats_db=stats_db_path,
                              live=live)
```

3. In `_consume_full` nach `beat.mark()`, direkt hinter `line = json.dumps(snap, ensure_ascii=False) + "\n"`:

```python
        line = json.dumps(snap, ensure_ascii=False) + "\n"
        beat.mark()
        if live is not None:
            live.publish(line, full=True)
```

4. Im Zweig für Positionsframes nach `beat.mark()`:

```python
                        pos_line = json.dumps(pos, ensure_ascii=False) + "\n"
                        beat.mark()
                        if live is not None:
                            live.publish(pos_line, full=False)
```

5. Nach den beiden Signal-Zeilen in `cmd_serve`. In `cmd_watch` stehen an Zeile 391/392 genau dieselben zwei Zeilen; nur die Stelle in `cmd_serve` an Zeile 1022/1023 wird geändert.

```python
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if live is not None:
        signal.signal(signal.SIGHUP, live.on_sighup)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_reload.py -q && .venv/bin/python -m pytest -q && .venv/bin/ruff check sqreader tests && .venv/bin/mypy sqreader`
Expected: alle Tests der ganzen Suite PASS, ruff und mypy ohne Befund.

- [ ] **Step 5: Commit**

```bash
git add sqreader/live.py sqreader/cli.py tests/test_live_reload.py
git commit -m "Wire the live map into serve and revoke it on SIGHUP"
```

---

### Task 7: Beispiel-Config und Betriebsanleitung

**Files:**
- Modify: `sqreader.config.example.json` (Dateiende)
- Create: `docs/live-map.md`
- Test: `tests/test_live_reload.py` (erweitern)

**Interfaces:**
- Consumes: –
- Produces: –

- [ ] **Step 1: Write the failing test**

Ans Ende von `tests/test_live_reload.py` anfügen:

```python
def test_the_example_config_documents_live_password():
    example = Path(sqreader.__file__).resolve().parent.parent / "sqreader.config.example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    assert data["live_password"] is None
    text = " ".join(data["_live_comment"])
    for needle in ("chmod 600", "SIGHUP", "?mode=live", "docs/live-map.md"):
        assert needle in text, needle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_reload.py::test_the_example_config_documents_live_password -q`
Expected: FAIL mit `KeyError: 'live_password'`

- [ ] **Step 3: Write minimal implementation**

In `sqreader.config.example.json` wird das Dateiende

```json
  "push_backlog_dir": null
}
```

ersetzt durch:

```json
  "push_backlog_dir": null,

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
}
```

`docs/live-map.md`:

````markdown
# Moderator live map

A real-time map of the running round, for moderators only. It is **off**
unless `live_password` is set in `sqreader.config.json`; then it lives under
`/api/live/` and nowhere else.

A live map shows **both teams in real time**. Anyone who watches it while
playing can ghost, which is why the public build has no live view at all.
Give the password to moderators and to nobody else.

## Enable

1. Generate a password (at least 20 characters):
   `python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`
2. Put it into `sqreader.config.json` as `"live_password": "<password>"` and
   `chmod 600` the file. It is a credential.
3. Docker: uncomment the `./sqreader.config.json:/app/sqreader.config.json:ro`
   line in `docker-compose.yml`.
4. Restart the reader. The log says `live map enabled for moderators`.

Moderators open `https://<your site>/?mode=live` and log in. HTTPS is
required; plain `http://` only works on `localhost`. After a login the start
page shows a **Live-Karte** button. A session lasts 12 hours, and every reader
restart, a Squad server restart included, logs everyone out.

## Change the password, or kick everyone out

Edit `live_password`, then send SIGHUP. No restart is needed, and the running
recording stays intact:

    docker compose kill -s HUP sqreader      # Docker
    systemctl kill -s HUP <unit>             # systemd

Every session ends at once and open live maps drop back to the login. If the
file cannot be read or holds no valid password, **nobody** can log in until it
is fixed and SIGHUP is sent again.

## Turn it off

Remove `live_password` and restart the reader **between two rounds**. A
restart in the middle of a round leaves that round's recording so far
unplayable.

## Proxies

The stream is Server-Sent Events (`text/event-stream`) and must not be
buffered:

- **Traefik**: no `buffering` middleware on this router, and keep the
  entrypoint's `respondingTimeouts.writeTimeout` at 0 (the default). On
  Traefik v3.0 to v3.3.4 add `text/event-stream` to the compress middleware's
  `excludedContentTypes`.
- **Caddy**: keep `/api/live/*` out of `encode`, for example
  `@compress not path /api/live/*` and `encode @compress zstd gzip`.
- **nginx**: nothing to do; the stream sends `X-Accel-Buffering: no`.

Serve the map on its own hostname. Cookies do not tell ports apart, so two
instances on one hostname share a login cookie, and an app that shares the
origin (a path prefix next to other sites) could read the stream with a
moderator's session.

## Check it

    curl -sN -H 'Cookie: sqr_live=<token>' https://<your site>/api/live/stream

prints `retry: 3000` and then one `data:` line per frame. Without a valid
cookie it answers 401.

## What the log says

| Line | Meaning |
|---|---|
| `live: login ok from <ip> (session <id>)` | a moderator logged in |
| `live: login failed from <ip>` | a wrong password |
| `live: login limit reached for <ip>`, `live: global login limit reached` | 5 failures from one address, or 50 from everyone, within 10 minutes; logins wait, open sessions are unaffected |
| `live: stream opened from <ip> (<n>/10)`, `live: stream closed from <ip> after <t> (<reason>)` | a live map was opened or closed |
| `live: stream refused from <ip> (10/10 in use)` | more than 10 live maps at once |
| `live: SIGHUP: ...` | the password was reloaded and everyone was logged out |

A login or a stream from an address you do not know means the password has
leaked: change it and send SIGHUP.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_live_reload.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sqreader.config.example.json docs/live-map.md tests/test_live_reload.py
git commit -m "Document how to run the moderator live map"
```

---

### Task 8: Frontend – Server-Aufrufe und Feed-Decoder (`client.ts`)

**Files:**
- Create: `frontend/src/live/client.ts`
- Test: `frontend/src/live/client.test.mts`

**Interfaces:**
- Consumes: `ReplayReconstructor` und `type RecordingLine` aus `frontend/src/state/replayReconstruct.ts`; `type Snapshot` aus `frontend/src/state/types.ts`.
- Produces:
  - `type LiveAccessState = "unknown" | "off" | "anon" | "ok"`
  - `probeSession(): Promise<LiveAccessState>`
  - `type LoginResult = { ok: true } | { ok: false; message: string }`
  - `login(password: string): Promise<LoginResult>`
  - `logout(): Promise<void>`
  - `interface LiveFeed { push(data: string): Snapshot | null }`
  - `createLiveFeed(): LiveFeed`
  - `retryAfterMinutes(header: string | null): number`

- [ ] **Step 1: Write the failing test**

`frontend/src/live/client.test.mts`:

```ts
// Standalone unit test for the live stream decoder. Bundled with esbuild and
// run under node, no framework (matches replayReconstruct.test.mts).
import { createLiveFeed, retryAfterMinutes } from "./client.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const full = (tick: number, x: number) => JSON.stringify({
  timestamp: `2026-01-01T00:00:0${tick}+00:00`, tick,
  players: [{ name: "Alice", eosId: "eos-a", teamId: 1,
              soldier: { addr: "0x1", position: { x, y: 0, z: 0 }, health: 100, yaw: 0 } }],
  vehicles: [], damageEvents: [{ killed: true }], gameState: { matchState: "InProgress" },
});
const pos = (tick: number, x: number) => JSON.stringify({
  t: "pos", tick, timestamp: `2026-01-01T00:00:0${tick}.5+00:00`,
  players: [{ id: "eos-a", x, y: 0 }], vehicles: [],
});

// 1. Garbage and non-objects are dropped.
{
  const f = createLiveFeed();
  eq(f.push("not json"), null, "garbage dropped");
  eq(f.push("[1,2]"), null, "array dropped");
  eq(f.push("42"), null, "number dropped");
  eq(f.push("null"), null, "null dropped");
}

// 2. A position frame before the first full frame is dropped.
{
  const f = createLiveFeed();
  eq(f.push(pos(1, 5)), null, "orphan position frame dropped");
}

// 3. Full frames pass through; position frames after them move players and
//    carry no kill events (those were delivered on the full frame).
{
  const f = createLiveFeed();
  const a = f.push(full(1, 10));
  eq(a?.tick, 1, "full frame returned");
  eq(a?.damageEvents.length, 1, "full frame keeps its kill events");
  const b = f.push(pos(2, 12));
  eq(b?.players[0]?.soldier?.position?.x, 12, "position frame moves Alice");
  eq(b?.damageEvents.length, 0, "position frame carries no kill events");
}

// 4. A new feed (a reconnect) starts without a base frame.
{
  const f1 = createLiveFeed();
  f1.push(full(1, 10));
  const f2 = createLiveFeed();
  eq(f2.push(pos(2, 12)), null, "fresh feed has no base frame");
}

// 5. Retry-After seconds become whole minutes for the message.
eq(retryAfterMinutes("600"), 10, "600 s is 10 min");
eq(retryAfterMinutes("61"), 2, "61 s rounds up");
eq(retryAfterMinutes("1"), 1, "at least one minute");
eq(retryAfterMinutes(null), 1, "missing header means 1 min");
eq(retryAfterMinutes("soon"), 1, "garbage means 1 min");

console.log(`\nlive client tests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx esbuild src/live/client.test.mts --bundle --platform=node --format=esm --outfile=node_modules/.cache/live-client-test.mjs --log-level=warning && node node_modules/.cache/live-client-test.mjs`
Expected: FAIL mit `Could not resolve "./client.ts"`

- [ ] **Step 3: Write minimal implementation**

`frontend/src/live/client.ts`:

```ts
// Moderator live map: the few server calls and the per-connection frame
// decoder. Fork-only; see docs/superpowers/specs/2026-09-29-live-moderation-design.md.
import type { Snapshot } from "../state/types";
import { ReplayReconstructor, type RecordingLine } from "../state/replayReconstruct";

// off = the server has no live map (404), anon = logged out, ok = logged in,
// unknown = could not tell (network or server error).
export type LiveAccessState = "unknown" | "off" | "anon" | "ok";

export async function probeSession(): Promise<LiveAccessState> {
  try {
    const r = await fetch("./api/live/session", { cache: "no-store" });
    if (r.status === 404) return "off";
    if (!r.ok) return "unknown";
    const body = (await r.json()) as { authenticated?: unknown };
    return body.authenticated === true ? "ok" : "anon";
  } catch {
    return "unknown";
  }
}

export type LoginResult = { ok: true } | { ok: false; message: string };

export function retryAfterMinutes(header: string | null): number {
  const sec = Number(header);
  return Number.isFinite(sec) && sec > 0 ? Math.max(1, Math.ceil(sec / 60)) : 1;
}

export async function login(password: string): Promise<LoginResult> {
  let r: Response;
  try {
    r = await fetch("./api/live/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } catch {
    return { ok: false, message: "Server nicht erreichbar." };
  }
  if (r.ok) return { ok: true };
  if (r.status === 401) return { ok: false, message: "Falsches Passwort." };
  if (r.status === 429) {
    const n = retryAfterMinutes(r.headers.get("Retry-After"));
    return { ok: false, message: `Zu viele Fehlversuche – bitte in ${n} Min. erneut versuchen.` };
  }
  return { ok: false, message: `Anmeldung fehlgeschlagen (HTTP ${r.status}).` };
}

export async function logout(): Promise<void> {
  try {
    await fetch("./api/live/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    // The session still expires on the server on its own.
  }
}

export interface LiveFeed {
  push(data: string): Snapshot | null;
}

// One per connection: the stream always starts with a full frame, and the
// reconstructor folds the 4 Hz position frames onto the last one.
export function createLiveFeed(): LiveFeed {
  const recon = new ReplayReconstructor();
  return {
    push(data: string): Snapshot | null {
      let v: unknown;
      try { v = JSON.parse(data); } catch { return null; }
      if (!v || typeof v !== "object" || Array.isArray(v)) return null;
      return recon.push(v as RecordingLine);
    },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx esbuild src/live/client.test.mts --bundle --platform=node --format=esm --outfile=node_modules/.cache/live-client-test.mjs --log-level=warning && node node_modules/.cache/live-client-test.mjs && npm test && npx tsc --noEmit`
Expected:
- `live client tests: 15 passed, 0 failed`
- `npm test` meldet alle Suiten als bestanden.
- `tsc` ohne Befund.

- [ ] **Step 5: Commit**

`frontend/dist` ändert sich in diesem Task nicht: `client.ts` wird noch von keinem Modul importiert, also bleibt der Bundle gleich.

```bash
git add frontend/src/live/client.ts frontend/src/live/client.test.mts
git commit -m "Decode the live stream in the browser"
```

---

### Task 9: Frontend – Login-Dialog, Stream-Hook und Buttons

**Files:**
- Create: `frontend/src/live/LiveAccess.tsx`, `frontend/src/live/live.css`
- Modify: `frontend/src/App.tsx` (Import nach Zeile 31, JSX nach `{!statsRoute && <PlayerStats />}`)
- Modify: `frontend/src/ui/TopBar.tsx` (Import nach `import { SettingsMenu } from "./SettingsMenu";`, JSX vor `<ClipRecorder />`)
- Modify: `frontend/src/ui/Home.tsx` (Import nach `import type { RecordingMeta, LeaderRow } from "../state/types";`, JSX als erstes Kind von `<nav className="hm-nav">`)
- Modify: `frontend/dist/**` (neu gebaut)

**Interfaces:**
- Consumes: alles aus `client.ts` (Task 8); aus `useViewerStore` die Felder `mode`, `setMode`, `setStatus`, `ingestLive`, `setState`.
- Produces: `LiveAccess`, `LiveEntry`, `LiveControls` (React-Komponenten), `enterLive(): void`, `exitLive(): void`

- [ ] **Step 1: Write the failing test**

Der Hook braucht `EventSource` und DOM, beides fehlt unter Node. Der Test dieses Tasks ist deshalb der Typ-Check. Das Verhalten prüft der Browser-Test in Task 10.

Zuerst wird der Import in `frontend/src/App.tsx` eingefügt, noch ohne die Komponente. Das ist der rote Zustand.

Nach `import { useViewerStore } from "./state/viewerStore";`:

```tsx
import { LiveAccess } from "./live/LiveAccess";
```

Nach `      {!statsRoute && <PlayerStats />}`:

```tsx
      {!statsRoute && <LiveAccess />}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx tsc --noEmit`
Expected: FAIL mit `Cannot find module './live/LiveAccess'`

- [ ] **Step 3: Write minimal implementation**

`frontend/src/live/LiveAccess.tsx`:

```tsx
// Moderator live map: login dialog, stream hook, and the Home + TopBar buttons.
// Fork-only (docs/superpowers/specs/2026-09-29-live-moderation-design.md).
// Renders nothing unless the server has the live map enabled: without it,
// GET ./api/live/session answers 404 and the public UI stays as it is.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { create } from "zustand";
import { useViewerStore } from "../state/viewerStore";
import { createLiveFeed, login, logout, probeSession, type LiveAccessState } from "./client";
import "./live.css";

const RECONNECT_MS = 5000;

interface LiveStore {
  access: LiveAccessState;
  loginOpen: boolean;
  notice: string | null;
}

const useLive = create<LiveStore>(() => ({ access: "unknown", loginOpen: false, notice: null }));

// Frames of whatever was shown before, a replay say, must not seed the live
// kill-feed diff: useKillFeed reads curSnap on the mode flip.
const EMPTY_FRAMES = { curSnap: null, prevSnap: null, lastInProgressTeams: null, curArrivalMs: 0 };

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

export function enterLive(): void {
  useViewerStore.setState({ ...EMPTY_FRAMES, status: "connecting" });
  useViewerStore.getState().setMode("live");
  setUrlMode("live");
}

export function exitLive(): void {
  useViewerStore.getState().setMode("home");
  setUrlMode(null);
}

function useLiveStream(): void {
  const mode = useViewerStore((s) => s.mode);
  useEffect(() => {
    if (mode !== "live") return;
    const store = useViewerStore.getState;
    let es: EventSource | null = null;
    let timer = 0;
    let stopped = false;

    const connect = (): void => {
      const feed = createLiveFeed();
      const source = new EventSource("./api/live/stream");
      es = source;
      source.onopen = () => store().setStatus("live");
      source.onmessage = (ev: MessageEvent) => {
        if (store().mode !== "live") return;
        const snap = feed.push(String(ev.data));
        if (snap) store().ingestLive(snap);
      };
      source.onerror = () => {
        store().setStatus("reconnecting");
        if (source.readyState !== EventSource.CLOSED) return;   // the browser retries itself
        source.close();
        es = null;
        void probeSession().then((access) => {
          if (stopped) return;
          if (access === "anon") {
            useLive.setState({ access: "anon" });
            exitLive();
            openLogin("Sitzung abgelaufen – bitte neu anmelden.");
            return;
          }
          // "ok", "unknown" (proxy or server hiccup) and "off" (Traefik's 404
          // while the container restarts) all mean: try again shortly.
          timer = window.setTimeout(connect, RECONNECT_MS);
        });
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      es?.close();
      useViewerStore.setState({ ...EMPTY_FRAMES });
    };
  }, [mode]);
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
    enterLive();
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

// Mounted once in App, outside the mode branch.
export function LiveAccess() {
  const access = useLive((s) => s.access);
  useLiveStream();

  useEffect(() => {
    let cancelled = false;
    void probeSession().then((a) => {
      if (cancelled) return;
      useLive.setState({ access: a });
      if (new URL(window.location.href).searchParams.get("mode") !== "live") return;
      if (a === "ok") enterLive();
      else if (a === "anon") openLogin();
      else if (a === "off") setUrlMode(null);
    });
    return () => { cancelled = true; };
  }, []);

  if (access !== "anon" && access !== "ok") return null;
  return <LoginDialog />;
}

// Home nav: only for a logged-in moderator.
export function LiveEntry() {
  const access = useLive((s) => s.access);
  if (access !== "ok") return null;
  return <button className="btn btn-ghost" onClick={enterLive}>Live-Karte</button>;
}

// TopBar, live mode only.
export function LiveControls() {
  const mode = useViewerStore((s) => s.mode);
  if (mode !== "live") return null;
  const signOut = async () => {
    await logout();
    useLive.setState({ access: "anon" });
    exitLive();
  };
  return (
    <>
      <button className="tb-back" onClick={exitLive} title="zur Startseite">← Zurück</button>
      <button onClick={() => { void signOut(); }} title="Live-Sitzung beenden">Abmelden</button>
    </>
  );
}
```

`frontend/src/live/live.css`:

```css
/* Moderator live map (fork-only): login dialog. Existing tokens only, so every
   theme applies; imported by LiveAccess.tsx and therefore bundled. */
#live-login {
  background: var(--glass-2);
  -webkit-backdrop-filter: var(--blur); backdrop-filter: var(--blur);
  border: 1px solid var(--glass-brd);
  border-radius: var(--radius);
  color: var(--ink);
  padding: 20px 22px;
  width: min(380px, 92vw);
  box-shadow: var(--shadow);
}
#live-login::backdrop {
  background: rgba(6, 9, 13, 0.6);
  -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px);
}
#live-login h2 { margin: 0 0 4px; font-size: 16px; font-weight: 800; }
#live-login .ll-sub { margin: 0 0 14px; font-size: 12.5px; color: var(--ink-mute); }
#live-login .ll-notice { margin: 0 0 12px; font-size: 12.5px; color: var(--ink); }
#live-login .ll-field { display: grid; gap: 6px; font-size: 12px; color: var(--ink-mute); }
#live-login .ll-field input {
  background: rgba(0, 0, 0, 0.28);
  border: 1px solid var(--glass-brd);
  border-radius: var(--radius-sm);
  color: var(--ink);
  font-size: 14px;
  padding: 8px 10px;
}
#live-login .ll-field input:focus {
  outline: none;
  border-color: color-mix(in srgb, var(--accent) 60%, transparent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}
#live-login .ll-error { margin: 10px 0 0; font-size: 12.5px; color: var(--bad); }
#live-login .ll-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
/* There for password managers, invisible and unreachable for everyone else. */
#live-login .ll-hidden {
  position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip: rect(0 0 0 0); border: 0;
}
```

In `frontend/src/ui/TopBar.tsx` kommt nach `import { SettingsMenu } from "./SettingsMenu";`:

```tsx
import { LiveControls } from "../live/LiveAccess";
```

Direkt vor `        <ClipRecorder />`:

```tsx
        <LiveControls />
```

In `frontend/src/ui/Home.tsx` kommt nach `import type { RecordingMeta, LeaderRow } from "../state/types";`:

```tsx
import { LiveEntry } from "../live/LiveAccess";
```

Und als erstes Kind der Navigation:

```tsx
          <nav className="hm-nav">
            <LiveEntry />
            <button className="btn btn-ghost" onClick={() => showModal("player-stats")}>Stats</button>
```

- [ ] **Step 4: Run test to verify it passes, then rebuild dist**

Run: `cd frontend && npm run build && npm test`
Expected:
- `tsc` ohne Befund, `vite build` erfolgreich.
- In `frontend/dist/assets/` liegen neue gehashte Dateien, und `dist/index.html` verweist darauf.
- `npm test` meldet alle Suiten als bestanden.

Danach vom Repo-Wurzelverzeichnis aus: `grep -l "live-login" frontend/dist/assets/*.js frontend/dist/assets/*.css`
Expected: je mindestens eine JS- und eine CSS-Datei.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/live/LiveAccess.tsx frontend/src/live/live.css frontend/src/App.tsx \
        frontend/src/ui/TopBar.tsx frontend/src/ui/Home.tsx
git add -A frontend/dist
git commit -m "Add the moderator login and live controls to the viewer"
```

---

### Task 10: Entwicklungs-Server und Ende-zu-Ende-Test

**Files:**
- Create: `scripts/live_dev_server.py`

**Interfaces:**
- Consumes: `LiveMap`, `serve_in_background(..., live=...)`, `SqrxReader`.
- Produces: `scripts/live_dev_server.py` mit den Optionen `--host`, `--port` (Standard 8090), `--password`, `--sqrx PATH` und `--no-live`.

- [ ] **Step 1: Write the script**

`scripts/live_dev_server.py`:

```python
"""Serve the viewer with a fake live round; no Squad server needed.

    .venv/bin/python scripts/live_dev_server.py            # synthetic players
    .venv/bin/python scripts/live_dev_server.py --sqrx R   # replay a recording as live
    .venv/bin/python scripts/live_dev_server.py --no-live  # the public build, to compare

Then open http://localhost:8090/?mode=live and log in with the printed
password. It serves frontend/dist, icons and sqmaps the way `sqreader serve`
does and feeds the live map a full frame every 2 s plus 4 Hz position frames.
SIGHUP exercises the revoke path: the password is re-read from
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqreader.httpsrv import _TickBeat, serve_in_background   # noqa: E402
from sqreader.live import LiveMap                             # noqa: E402
from sqreader.sqrx import SqrxReader                          # noqa: E402

DEV_PASSWORD = "dev-password-for-the-live-map"
PLAYERS = 20
RADIUS = 150_000.0      # UE units; Gorodok spans about +-203 000


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


def feed_synthetic(live: LiveMap, stop: threading.Event) -> None:
    tick = 0
    while not stop.wait(0.25):
        tick += 1
        t = tick * 0.25
        if tick % 8 == 1:
            live.publish(json.dumps(full_frame(tick, t)) + "\n", full=True)
        else:
            live.publish(json.dumps(pos_frame(tick, t)) + "\n", full=False)


def feed_recording(live: LiveMap, stop: threading.Event, path: Path) -> None:
    """Replay a .sqrx at its own pace (gaps capped at 2 s), looping."""
    while not stop.is_set():
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
                live.publish(line + "\n", full=frame.get("t") != "pos")


def main() -> int:
    ap = argparse.ArgumentParser(description="Viewer with a fake live round.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--password", default=DEV_PASSWORD)
    ap.add_argument("--sqrx", type=Path, help="replay this recording as the live feed")
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
        if args.sqrx:
            feed = threading.Thread(target=feed_recording, args=(live, stop, args.sqrx),
                                    daemon=True)
        else:
            feed = threading.Thread(target=feed_synthetic, args=(live, stop), daemon=True)
        feed.start()
        print(f"http://localhost:{args.port}/?mode=live  password: {args.password}  "
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

Run: `.venv/bin/ruff check scripts/live_dev_server.py`
Expected: keine Befunde.

- [ ] **Step 2: Smoke-check the stream from the shell**

Run im Hintergrund: `.venv/bin/python scripts/live_dev_server.py --port 8090`

Dann:

```bash
TOKEN=$(curl -si -X POST -H 'Content-Type: application/json' \
  -d '{"password":"dev-password-for-the-live-map"}' http://127.0.0.1:8090/api/live/login \
  | sed -n 's/^Set-Cookie: sqr_live=\([^;]*\);.*/\1/p' | tr -d '\r')
timeout 3 curl -sN -H "Cookie: sqr_live=$TOKEN" http://127.0.0.1:8090/api/live/stream | head -c 300; echo
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/api/live/stream
```

Expected:
- Der Stream beginnt mit `retry: 3000` und danach `data: {"timestamp": ...`.
- Die letzte Zeile ohne Cookie gibt `401` aus.

- [ ] **Step 3: Browser check with a logged-out and a logged-in moderator**

Mit den Chrome-Werkzeugen (`mcp__claude-in-chrome__*`, laden per ToolSearch) in einem neuen Tab. Das Konsolen-Log wird dabei auf Fehler beobachtet.

1. `http://localhost:8090/` öffnen. Die Startseite sieht aus wie bisher, ohne Button „Live-Karte“.
2. `http://localhost:8090/?mode=live` öffnen. Der Dialog „Moderator-Login“ ist offen, und der Fokus liegt im Passwortfeld.
3. Ein falsches Passwort eingeben. Erscheint „Falsches Passwort.“?
4. `dev-password-for-the-live-map` eingeben. Erwartet:
   - Die Karte erscheint nach dem Aufwärmen (etwa 6 s); 20 Punkte kreisen über Gorodok.
   - Die Statusanzeige zeigt „live“ und „data 0s“ bis „data 2s“.
5. Die Seite neu laden. Sie springt ohne Dialog direkt in den Live-Modus, weil das Cookie noch gilt.
6. „← Zurück“ klicken. Auf der Startseite steht jetzt „Live-Karte“; ein Klick darauf führt wieder in den Live-Modus.
7. „Abmelden“ klicken. Man landet auf der Startseite, der Button „Live-Karte“ ist weg, und `?mode=live` öffnet wieder den Dialog.

- [ ] **Step 4: Browser check for restart and revoke**

Dieser Schritt deckt Review Focus 2 ab.

1. Einloggen und im Live-Modus bleiben.
2. Den Entwicklungs-Server stoppen. Die Anzeige wechselt auf „Reconnecting…“.
3. Den Server neu starten. Innerhalb von etwa 10 s erscheint der Login-Dialog mit „Sitzung abgelaufen – bitte neu anmelden.“, denn der Neustart hat alle Sitzungen gelöscht.
4. Erneut einloggen, dann `kill -HUP <pid>` ausführen; die PID steht in der Startzeile. Erwartet:
   - Der Login-Dialog erscheint mit dem Hinweis.
   - Das Log enthält `live: SIGHUP: all sessions revoked, password reloaded`.
   - Einloggen mit demselben Passwort klappt wieder.

- [ ] **Step 5: Public build check**

1. Den Server mit `--no-live` neu starten.
2. `http://localhost:8090/?mode=live` öffnen. Es erscheint kein Dialog, der Parameter `mode` verschwindet aus der URL, und die Startseite sieht aus wie bisher.

- [ ] **Step 6: Traefik and bandwidth check**

Dieser Schritt deckt Review Focus 1 ab. Den Server wieder mit Live starten (optional mit `--sqrx <echte Aufnahme>` für realistische Frame-Größen), dann:

```bash
S=/tmp/claude-1000/-home-hans-PhpstormProjects-squadreader/fe619ab2-b90f-4585-8c1a-d24b9ae4d497/scratchpad/traefik
mkdir -p "$S" && cat > "$S/dynamic.yml" <<'EOF'
http:
  routers:
    live:
      rule: "PathPrefix(`/`)"
      entryPoints: [web]
      service: sq
      middlewares: [gz]
  middlewares:
    gz:
      compress: {}
  services:
    sq:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8090"
EOF
docker run --rm -d --name live-traefik --network host -v "$S/dynamic.yml:/etc/traefik/dynamic.yml:ro" \
  traefik:v3 --entrypoints.web.address=:8091 --providers.file.filename=/etc/traefik/dynamic.yml
sleep 2
timeout 6 curl -sN --compressed -H "Cookie: sqr_live=$TOKEN" http://127.0.0.1:8091/api/live/stream \
  | python3 -c 'import sys,time; t0=time.time(); [print(f"{time.time()-t0:5.2f}s {len(l)}B") for l in sys.stdin if l.startswith("data:")]'
timeout 30 curl -sN -H "Cookie: sqr_live=$TOKEN" http://127.0.0.1:8090/api/live/stream | wc -c
docker rm -f live-traefik
```

`$TOKEN` ist hier ein frischer Login nach Schritt 2.

Expected:
- Durch Traefik kommen die `data:`-Zeilen gleichmäßig etwa alle 0,25 s an, nicht in Schüben.
- `wc -c` geteilt durch 30 ergibt die Bytes pro Sekunde und Zuschauer. Die Zahl gehört in den Abschlussbericht.
- Optional: dasselbe mit `traefik:v3.3.4`. Wenn die Ereignisse dort hängen bleiben, bestätigt das den Doku-Hinweis. Dann den Test mit `compress: {excludedContentTypes: ["text/event-stream"]}` wiederholen; danach müssen die Ereignisse wieder durchkommen.

- [ ] **Step 7: Full verification**

Run: `.venv/bin/python -m pytest -q && .venv/bin/ruff check sqreader tests scripts && .venv/bin/mypy sqreader && (cd frontend && npm run build && npm test) && git status --short`
Expected:
- Alles grün.
- `npm run build` ändert `frontend/dist` nicht mehr, weil es in Task 9 schon gebaut wurde.
- `git status` zeigt nur `scripts/live_dev_server.py` und die zwei ungetrackten Traefik-Dateien des Nutzers.

- [ ] **Step 8: Commit**

```bash
git add scripts/live_dev_server.py
git commit -m "Add a dev server that fakes a live round"
```

---

### Task 11: Merge in `dcn-branding`

**Nur nach ausdrücklicher Freigabe durch den Nutzer**, denn `dcn-branding` ist der Deploy-Branch. Es wird nichts gepusht.

**Files:**
- Merge: `live-moderation` → `dcn-branding`
- Modify (auf `dcn-branding`): `frontend/src/ui/Home.tsx` (Konflikt), `frontend/dist/**`, `deploy/Caddyfile`, `docker-compose.yml`

- [ ] **Step 1: Merge**

```bash
git switch dcn-branding
git merge --no-ff live-moderation
```

Expected: Konflikte in `frontend/src/ui/Home.tsx` und `frontend/dist/*`.

- [ ] **Step 2: Resolve Home.tsx**

- Die Fassung von `dcn-branding` bleibt erhalten.
- Der Import `import { LiveEntry } from "../live/LiveAccess";` kommt hinter `import type { RecordingMeta, LeaderRow } from "../state/types";`.
- `<LiveEntry />` kommt in die `.hm-nav` hinter den Status-Span:

```tsx
          <nav className="hm-nav">
            <span className={"hm-status " + (online ? "is-on" : "is-off")}>
              <span className="hm-status-dot" />
              {online ? "Server online" : "Archiv"}
            </span>
            <LiveEntry />
            <button className="btn btn-ghost" onClick={() => showModal("player-stats")}>Statistiken</button>
```

- [ ] **Step 3: Caddyfile and compose comment**

In `deploy/Caddyfile` wird `	encode zstd gzip` ersetzt durch:

```
	# The moderator live map is a Server-Sent-Events stream: compressing it
	# would hold events inside a compression frame (docs/live-map.md).
	@compress not path /api/live/*
	encode @compress zstd gzip
```

Im HSTS-Kommentar wird der Satz `This site has no login and no cookies, so a year of that trade buys little.` ersetzt durch `The only login is the optional moderator live map (docs/live-map.md), so a year of that trade buys little.`

In `docker-compose.yml` wird die Kommentarzeile `# Optional: plugins, alert webhook, central URL — and squad_port, the only` ersetzt durch `# Optional: plugins, alert webhook, central URL, live_password (moderator live map) — and squad_port, the only`.

- [ ] **Step 4: Rebuild dist on the merged tree and verify**

Run: `(cd frontend && npm run build && npm test) && git add -A frontend/dist && .venv/bin/python -m pytest -q && .venv/bin/ruff check sqreader tests scripts && .venv/bin/mypy sqreader`
Expected: alles grün, auch `tests/test_compose_proxy.py` und `tests/test_docker_entrypoint.py`.

- [ ] **Step 5: Commit the merge**

```bash
git add frontend/src/ui/Home.tsx deploy/Caddyfile docker-compose.yml
git commit --no-edit
```

Der Merge-Commit behält die Standardnachricht, ohne Trailer. Danach mit dem Nutzer klären, ob und wann gepusht wird.

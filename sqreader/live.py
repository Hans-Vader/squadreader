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
import ipaddress
import json
import logging
import math
import secrets
import threading
import time
from collections import deque
from typing import Any, Optional

from .config import config_path

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
MAX_COOKIE_VALUES = 8             # sqr_live values read from one request

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


def cookie_values(headers: Any) -> list[str]:
    """The first MAX_COOKIE_VALUES sqr_live values the browser sent, in order.

    A browser sends one or two. The cap keeps a header stuffed with duplicates
    from costing a lock and a lookup for every one of them.
    """
    values: list[str] = []
    for header in headers.get_all("Cookie") or []:
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name == COOKIE and value:
                values.append(value)
                if len(values) == MAX_COOKIE_VALUES:
                    return values
    return values


class Access:
    """The shared password and the sessions it grants. One lock guards both."""

    def __init__(self, password: Optional[str]) -> None:
        self._lock = threading.Lock()
        self._digest: Optional[bytes] = _digest(password) if password else None
        # token -> monotonic expiry
        self._sessions: dict[str, float] = {}
        self._fails: deque[tuple[float, str]] = deque()      # (monotonic time, client)

    def reset(self, password: Optional[str]) -> bool:
        """A new password (None: nobody can log in) and no sessions at all.

        True if that changed the stored password, "no password" counting as a
        value of its own. The sessions are gone either way.
        """
        new = _digest(password) if password else None
        with self._lock:
            changed = new != self._digest
            self._digest = new
            self._sessions.clear()
        return changed

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
                result = ("ok", self._new_session(now))
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

    def _new_session(self, now: float) -> str:               # caller holds the lock
        self._sessions = {t: e for t, e in self._sessions.items() if e > now}
        while len(self._sessions) >= MAX_SESSIONS:
            del self._sessions[min(self._sessions, key=self._sessions.__getitem__)]
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_TTL_SEC
        return token


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
        self._refused_lock = threading.Lock()
        self._refused_at = float("-inf")      # monotonic time of the last refusal log

    def publish(self, line: str, *, full: bool) -> None:
        self.hub.publish(line, full=full)

    def on_sighup(self, _signum: int, _frame: Any) -> None:
        # Runs between two bytecodes of the main thread, which may be inside
        # publish() or a log call. Taking locks here could deadlock, so a
        # thread does the work.
        try:
            threading.Thread(target=self.reload, name="sqreader-live-reload",
                             daemon=True).start()
        except Exception:
            # No thread to be had (RuntimeError: can't start new thread), and an
            # exception here would surface in the reader's tick loop. Fail closed
            # on the spot, without logging. Safe from the signal context: the
            # main thread never holds Access._lock, and the hub's Condition
            # is re-entrant.
            self.access.reset(None)
            self.hub.kick()

    def reload(self) -> None:
        """Revoke every session and re-read live_password. Fails closed: a file
        that cannot be read, or holds no valid password, disables logins.

        The log says which of the three happened. A file that still holds the
        old password (a Docker single-file bind mount keeps serving the old
        inode after an editor replaced the file) is reported as UNCHANGED, and a
        failure names its reason: an error TYPE or a rule, never a message, which
        could quote the file, and never the value.
        """
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
            why = (reason.removeprefix("live map disabled: ") if reason
                   else "live_password is not set")
        changed = self.access.reset(password)
        self.hub.kick()
        if password is None:
            log.warning("live: SIGHUP: all sessions revoked; NO valid live_password in %s "
                        "(%s), logins disabled until fixed", path, why)
        elif changed:
            log.warning("live: SIGHUP: all sessions revoked, password reloaded")
        else:
            log.warning("live: SIGHUP: all sessions revoked; live_password in %s is UNCHANGED",
                        path)

    def handle_get(self, h: Any, path: str) -> None:
        if path == "/api/live/session":
            token = self.access.first_valid(cookie_values(h.headers))
            _json(h, 200, {"authenticated": token is not None})
        elif path == "/api/live/stream":
            self._stream(h)
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
            log.info("live: stream closed from %s after %dm%02ds (%s)", client,
                     *divmod(int(time.monotonic() - started), 60), reason)

    def _log_refused(self, client: str) -> None:
        now = time.monotonic()
        with self._refused_lock:
            if now - self._refused_at < REFUSED_LOG_INTERVAL_SEC:
                return
            self._refused_at = now
        log.warning("live: stream refused from %s (%d/%d in use)", client,
                    MAX_STREAMS, MAX_STREAMS)


def live_from_config(value: Any) -> Optional[LiveMap]:
    """The live map for this config value, or None, and then it does not exist."""
    password, reason = validate_password(value)
    if password is None:
        if reason:
            log.warning("%s", reason)
        return None
    log.info("live map enabled for moderators (login via ?mode=live)")
    return LiveMap(password)

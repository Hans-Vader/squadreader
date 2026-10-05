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
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import secrets
import sys
import threading
import time
import urllib.parse
import zlib
from collections import deque
from collections.abc import Callable
from typing import Any, Optional

import zstandard as zstd

from .config import config_path
from .httpsrv import _replay_from
from .sqrx import SqrxReader

log = logging.getLogger("sqreader.live")

SESSION_TTL_SEC = 43200           # 12 h, absolute; never extended
MAX_SESSIONS = 32
BODY_MAX = 1024                   # bytes of a login/logout body
BODY_DEADLINE_SEC = 10.0          # for the WHOLE body, not per recv
FAIL_WINDOW_SEC = 600.0
FAILS_PER_CLIENT = 5
FAILS_GLOBAL = 50
MAX_STREAMS = 10
WRITE_TIMEOUT_SEC = 20.0
POLL_SEC = 0.25                   # at the end of a round's file: look again after this
REFUSED_LOG_INTERVAL_SEC = 60.0
MAX_COOKIE_VALUES = 8             # sqr_live values read from one request

COOKIE = "sqr_live"

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
            and 128 * n * r <= 2**28 and n < 2**(16 * r) and len(salt) >= 16 and len(key) == 32):
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
    """The shared password (or its hash) and the sessions it grants. One lock guards both."""

    def __init__(self, secret: Optional[str]) -> None:
        self._lock = threading.Lock()
        self._secret = secret
        # token -> monotonic expiry
        self._sessions: dict[str, float] = {}
        self._fails: deque[tuple[float, str]] = deque()      # (monotonic time, client)

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
        # scrypt under the lock (~50 ms) serialises logins, which together with
        # the throttle also bounds what a flood of them costs.
        if self._secret is None:
            return False
        if not self._secret.startswith("scrypt:"):
            return hmac.compare_digest(_digest(given), _digest(self._secret))
        parsed = parse_hash(self._secret)
        if parsed is None:                    # fail closed: a hash is never a password
            return False
        n, r, p, salt, key = parsed
        got = hashlib.scrypt(given.encode("utf-8", "surrogatepass"), salt=salt,
                             n=n, r=r, p=p, maxmem=2**29, dklen=len(key))
        return hmac.compare_digest(got, key)

    def _new_session(self, now: float) -> str:               # caller holds the lock
        self._sessions = {t: e for t, e in self._sessions.items() if e > now}
        while len(self._sessions) >= MAX_SESSIONS:
            del self._sessions[min(self._sessions, key=self._sessions.__getitem__)]
        token = secrets.token_urlsafe(32)
        self._sessions[token] = now + SESSION_TTL_SEC
        return token


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


class LiveMap:
    """The /api/live/* endpoints."""

    def __init__(self, password: str) -> None:
        self.access = Access(password)
        # The round being recorded (recorder.RecordingState) or None; cli.py
        # points this at record_state_box["current"].
        self.recording: Callable[[], Any] = lambda: None
        self._streams_lock = threading.Lock()
        self._streams = 0
        self._refused_lock = threading.Lock()
        self._refused_at = float("-inf")      # monotonic time of the last refusal log

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
        elif path == "/api/live/round" or path.startswith("/api/live/round/"):
            self._round(h, path[len("/api/live/round/"):])
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
        _json(h, 200, {"authenticated": False},
              {"Set-Cookie": f"{COOKIE}=; Max-Age=0; HttpOnly; Secure; SameSite=Strict"})

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

    def _log_refused(self, client: str) -> None:
        now = time.monotonic()
        with self._refused_lock:
            if now - self._refused_at < REFUSED_LOG_INTERVAL_SEC:
                return
            self._refused_at = now
        log.warning("live: stream refused from %s (%d/%d in use)", client,
                    MAX_STREAMS, MAX_STREAMS)


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

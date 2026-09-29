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
import logging
import math
import secrets
import threading
import time
from collections import deque
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


class Access:
    """The shared password and the sessions it grants. One lock guards both."""

    def __init__(self, password: Optional[str]) -> None:
        self._lock = threading.Lock()
        self._digest: Optional[bytes] = _digest(password) if password else None
        # token -> (monotonic expiry, client key)
        self._sessions: dict[str, tuple[float, str]] = {}
        self._fails: deque[tuple[float, str]] = deque()      # (monotonic time, client)

    def reset(self, password: Optional[str]) -> None:
        """A new password (None: nobody can log in) and no sessions at all."""
        with self._lock:
            self._digest = _digest(password) if password else None
            self._sessions.clear()

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

"""Shared helpers for the live-map HTTP tests: a real server, raw sockets."""
from __future__ import annotations

import json
import re
import socket
import time
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
        # `timeout` is a TOTAL deadline for the marker, not a per-recv timeout: a peer that
        # keeps sending (keepalives) but never sends the marker must fail, not block forever.
        deadline = time.monotonic() + timeout
        while marker not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(self.buf)
            self.s.settimeout(left)           # never 0: that is non-blocking mode
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
        # `seconds` is a TOTAL deadline, not a per-recv timeout: a stream that keeps
        # sending keepalives must still come back False, not block the test forever.
        deadline = time.monotonic() + seconds
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.s.settimeout(left)           # never 0: that is non-blocking mode
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

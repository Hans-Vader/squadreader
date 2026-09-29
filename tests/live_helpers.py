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

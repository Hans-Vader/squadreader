"""The chunked replay response must carry an HTTP/1.1 status line.

/api/recording/<id> has no knowable Content-Length, so it is delimited by
chunked framing — and chunked framing is HTTP/1.1-only. On stdlib's default
HTTP/1.0 status line a conformant client MUST ignore Transfer-Encoding and read
to EOF instead: Go's net/http (Caddy, Traefik) does exactly that, so the
chunk-size lines land INSIDE the body and the browser fails to gunzip/unzstd it.
Replays then break behind Caddy while the Content-Length'd SPA loads fine.

urllib and http.client honour chunked regardless of the response version, so
only the raw status line catches this — hence the socket.
"""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import socket
import time

from sqreader.httpsrv import _TickBeat, serve_in_background
from sqreader.sqrx import SqrxWriter


def _finalized(tmp_path, rec_id="rec1", lines=1):
    p = tmp_path / f"{rec_id}.sqrx"
    with SqrxWriter(p, "srv-1") as w:
        for i in range(lines):
            w.write_line(json.dumps({"tick": i, "gameState": {"matchId": rec_id}}))
    p.with_suffix(".meta.json").write_text(json.dumps({
        "id": rec_id, "filename": p.name, "serverId": "srv-1",
        "matchId": rec_id, "recordingState": "finalized", "inProgress": False,
    }), encoding="utf-8")


def _raw_get(port, path, accept_encoding):
    req = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           f"Accept-Encoding: {accept_encoding}\r\nConnection: close\r\n\r\n")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(req.encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return buf


def test_chunked_replay_announces_http_1_1(tmp_path):
    _finalized(tmp_path)
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(),
                              recordings_dir=tmp_path)
    try:
        raw = _raw_get(srv.server_address[1], "/api/recording/rec1", "gzip")
    finally:
        srv.shutdown()
        srv.server_close()

    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"Transfer-Encoding: chunked" in head
    assert raw.startswith(b"HTTP/1.1 200"), head[:64]
    # The framing really is framing: the gzip member starts after the first
    # chunk-size line, so a de-chunking proxy hands the browser clean gzip.
    assert body.split(b"\r\n", 1)[1].startswith(b"\x1f\x8b"), body[:32]


def test_get_with_a_body_does_not_desync_the_connection(tmp_path):
    # Keep-alive turns an unread request body into the next request on the
    # connection: one GET in, two responses out, and through a proxy that
    # poisons a pooled upstream connection.
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(),
                              recordings_dir=tmp_path)
    smuggled = b"GET /api/recordings HTTP/1.1\r\nHost: x\r\n\r\n"
    req = (b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           b"Content-Length: " + str(len(smuggled)).encode() + b"\r\n\r\n"
           + smuggled)
    try:
        with socket.create_connection(
                ("127.0.0.1", srv.server_address[1]), timeout=2) as s:
            s.sendall(req)
            buf = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except TimeoutError:
                pass  # connection held open — the assert below says why
    finally:
        srv.shutdown()
        srv.server_close()

    assert buf.count(b"HTTP/1.") == 1, buf[:400]


def test_a_duplicated_content_length_cannot_smuggle_past_the_guard(tmp_path):
    # "Content-Length: 0" then a real one: headers.get() hands back only the
    # first, so a truthiness check on it lets the body through as a request.
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(),
                              recordings_dir=tmp_path)
    smuggled = b"GET /api/recordings HTTP/1.1\r\nHost: x\r\n\r\n"
    req = (b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n"
           b"Content-Length: " + str(len(smuggled)).encode() + b"\r\n\r\n"
           + smuggled)
    try:
        with socket.create_connection(
                ("127.0.0.1", srv.server_address[1]), timeout=2) as s:
            s.sendall(req)
            buf = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except TimeoutError:
                pass
    finally:
        srv.shutdown()
        srv.server_close()

    assert buf.count(b"HTTP/1.") == 1, buf[:400]


def test_an_http_10_client_gets_no_chunked_framing(tmp_path):
    # The mirror image of the Caddy bug: announcing 1.1 does not make the
    # client 1.1, and a 1.0 client must ignore Transfer-Encoding too.
    _finalized(tmp_path)
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(),
                              recordings_dir=tmp_path)
    try:
        with socket.create_connection(
                ("127.0.0.1", srv.server_address[1]), timeout=5) as s:
            s.sendall(b"GET /api/recording/rec1 HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                      b"Accept-Encoding: gzip\r\n\r\n")
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    finally:
        srv.shutdown()
        srv.server_close()

    head, _, body = buf.partition(b"\r\n\r\n")
    assert b"Transfer-Encoding" not in head, head
    assert b"Connection: close" in head, head       # body is delimited by EOF
    assert gzip.decompress(body).count(b"\n") == 1, body[:64]


def _serve_with_idle_timeout(tmp_path, seconds):
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(),
                              recordings_dir=tmp_path)
    # _make_handler builds a fresh class per server, so this touches no one else.
    # A default must exist at all: every test below overrides it with a short
    # one, so nothing else here would notice it going back to None.
    assert srv.RequestHandlerClass.timeout is not None
    srv.RequestHandlerClass.timeout = seconds
    return srv


def test_an_idle_keep_alive_connection_is_reaped(tmp_path):
    # Without the timeout the handler thread blocks in readline forever, and a
    # connection that dies without a FIN never gives it back.
    srv = _serve_with_idle_timeout(tmp_path, 0.3)
    try:
        with socket.create_connection(
                ("127.0.0.1", srv.server_address[1]), timeout=5) as s:
            s.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            # Headers and body are separate sends, so drain rather than count
            # recv calls; EOF is the server reaping the now-idle connection.
            buf, closed = b"", False
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        closed = True
                        break
                    buf += chunk
            except TimeoutError:
                pass
            assert buf.startswith(b"HTTP/1.1 "), buf[:80]
            assert closed, "idle connection was not closed"
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_idle_timeout_does_not_cut_off_a_slow_download(tmp_path):
    # The reason the timeout is dropped for the duration of the response: it is
    # armed on the socket, so it bounds writes too. A tiny receive buffer makes
    # the server block on send long past the timeout, as a slow client would.
    _finalized(tmp_path, lines=2000)
    srv = _serve_with_idle_timeout(tmp_path, 0.3)
    # Accepted sockets inherit this, so the server blocks on send after a few
    # KB instead of dumping the whole body into a 2 MB kernel buffer.
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        s.settimeout(15)
        s.connect(("127.0.0.1", srv.server_address[1]))
        s.sendall(b"GET /api/recording/rec1 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  b"Accept-Encoding: identity\r\nConnection: close\r\n\r\n")
        time.sleep(1.5)  # 5x the timeout, server blocked mid-body
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
    finally:
        srv.shutdown()
        srv.server_close()

    assert buf.endswith(b"0\r\n\r\n"), "chunked body was truncated"
    assert buf.count(b'{"tick"') == 2000, buf.count(b'{"tick"')


def test_a_client_that_drops_the_connection_logs_no_traceback(tmp_path):
    # Keep-alive means the handler waits for another request on a connection
    # the client may abandon with data still unread — which arrives as RST.
    # Small enough that the response completes and the handler is back at the
    # idle readline — a body that blocked mid-write would fail inside the
    # stream's own except instead, which is a different path.
    _finalized(tmp_path, lines=200)
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(),
                              recordings_dir=tmp_path)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            with socket.create_connection(
                    ("127.0.0.1", srv.server_address[1]), timeout=5) as s:
                s.sendall(b"GET /api/recording/rec1 HTTP/1.1\r\n"
                          b"Host: 127.0.0.1\r\nAccept-Encoding: identity\r\n\r\n")
                s.recv(256)      # leave the rest unread, then hang up -> RST
                time.sleep(0.5)  # let the handler get back to waiting
            time.sleep(1.0)
    finally:
        srv.shutdown()
        srv.server_close()

    assert "Traceback" not in err.getvalue(), err.getvalue()[:600]

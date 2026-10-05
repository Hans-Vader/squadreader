"""Defense in depth on the replay server: what it sends besides the payload.

None of these close a known hole — the request path was reviewed and every
segment is allowlisted before it touches disk or SQL. They bound the damage of
the bug that has not been found yet, and they cost a header or an except clause
each:

  * nosniff on every response, error pages included, so a mistyped asset can
    never be executed as script;
  * a Content-Security-Policy on the one HTML document, with no unsafe-inline,
    so a future DOM-injection bug in the viewer is a console error and not
    script execution;
  * a Server header that names the product, not the interpreter version;
  * a 404 for a directory where a file was expected — `/assets/..` passes the
    charset regex and used to escape as IsADirectoryError, a ten-line traceback
    per anonymous request in the container log;
  * a fixed 500 body for stats failures, with the real exception in the
    operator's log rather than in the anonymous client's browser.
"""
from __future__ import annotations

import contextlib
import io
import logging
import re
import socket
import struct
import time
from pathlib import Path

import pytest

from sqreader.httpsrv import _TickBeat, serve_in_background

REPO = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def _serving(**kw):
    srv = serve_in_background("127.0.0.1", 0, _TickBeat(), **kw)
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _raw_get(port, path):
    req = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           f"Connection: close\r\n\r\n")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(req.encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    return head, body


def _header(head, name):
    m = re.search(rb"\r\n" + name.encode() + rb": ([^\r]*)", head)
    return m.group(1) if m else None


def _status(head):
    # Assert the code, not the framing: these tests are about nosniff, CSP, the
    # Server header and error pages, and pinning the status line's version here
    # broke every one of them when the replay endpoint went back to deciding
    # the version per request.
    return head.split(b" ", 2)[1]


def _frontend(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><script type="module" src="./assets/a.js"></script>',
        encoding="utf-8")
    (dist / "assets" / "a.js").write_text("1", encoding="utf-8")
    return dist


def test_every_response_including_error_pages_says_nosniff(tmp_path):
    with _serving(recordings_dir=tmp_path) as port:
        ok, _ = _raw_get(port, "/health")
        err, _ = _raw_get(port, "/no-such-route")
    assert _header(ok, "X-Content-Type-Options") == b"nosniff", ok
    # send_error is stdlib's path, not ours — the hook has to sit under both.
    assert _status(err) == b"404", err[:80]
    assert _header(err, "X-Content-Type-Options") == b"nosniff", err


def test_the_server_header_does_not_name_the_python_version():
    with _serving() as port:
        head, _ = _raw_get(port, "/health")
    server = _header(head, "Server")
    assert server is not None, head
    assert b"Python/" not in server and b"BaseHTTP" not in server, server


def test_the_spa_document_carries_a_csp_and_its_assets_do_not(tmp_path):
    with _serving(frontend_dir=_frontend(tmp_path)) as port:
        doc, _ = _raw_get(port, "/")
        asset, _ = _raw_get(port, "/assets/a.js")
    csp = _header(doc, "Content-Security-Policy")
    assert csp is not None, doc
    for directive in (b"default-src 'self'", b"object-src 'none'",
                      b"base-uri 'none'", b"frame-ancestors 'none'"):
        assert directive in csp, csp
    assert b"unsafe-inline" not in csp, csp
    # CSP governs documents; on a script it is noise at best.
    assert _status(asset) == b"200", asset[:80]
    assert _header(asset, "Content-Security-Policy") is None, asset


def test_dist_index_ships_no_inline_script():
    """The policy has no hash and no unsafe-inline, so an inline script in
    dist/index.html would be blocked silently. Fail loudly here instead."""
    html = (REPO / "frontend" / "dist" / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert inline == [], inline


@pytest.mark.parametrize("route", ["/assets/..", "/icons/weapons/x.png"])
def test_a_directory_where_a_file_was_expected_is_a_404_not_a_traceback(
        tmp_path, route):
    # Both names pass their allowlist regex and resolve to a directory;
    # read_bytes() then raises IsADirectoryError, which is an OSError but
    # not a FileNotFoundError.
    icons = tmp_path / "icons"
    (icons / "weapons" / "x.png").mkdir(parents=True)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), \
            _serving(frontend_dir=_frontend(tmp_path), icons_dir=icons) as port:
        head, _ = _raw_get(port, route)
    assert _status(head) == b"404", head[:80]
    assert "Traceback" not in err.getvalue(), err.getvalue()[:600]


def test_a_stats_failure_is_generic_to_the_client_and_logged_for_the_operator(
        tmp_path, caplog):
    db = tmp_path / "stats.db"
    db.write_bytes(b"this is not a sqlite database\n" * 8)
    with caplog.at_level(logging.WARNING, logger="sqreader.httpsrv"), \
            _serving(stats_db=db) as port:
        head, body = _raw_get(port, "/api/leaderboard")
    assert _status(head) == b"500", head[:80]
    # The reason phrase and the body used to carry repr(exception).
    assert b"file is not a database" not in head + body, head + body
    assert b"DatabaseError" not in head + body, head + body
    logged = [r.getMessage() for r in caplog.records]
    assert any("stats query failed" in m and "/api/leaderboard" in m
               and "file is not a database" in m for m in logged), logged


# ---- the request path must not report the client's own disconnect as a fault --

def _bare_handler(**kw):
    """A handler instance without a socket, to exercise the error helpers.

    BaseHTTPRequestHandler.__init__ runs the whole request cycle, so the class
    is built the way serve_in_background builds it and then instantiated
    without it — these two helpers touch nothing but self.path and send_error.
    """
    from sqreader.httpsrv import _make_handler
    H = _make_handler(_TickBeat(), None, None, None, None, None, **kw)
    h = H.__new__(H)
    h.path = kw.pop("_path", "/api/leaderboard")
    return h


def test_a_client_disconnect_is_not_reported_as_a_stats_failure(caplog):
    """A reader closing the tab mid-response raises BrokenPipeError inside the
    same try as the query. Logging that as "stats query failed" poisons the
    one signal this helper exists to provide, and answering it with send_error
    writes a second status line into a response that already sent 200."""
    h = _bare_handler()
    sent = []
    h.send_error = lambda *a, **k: sent.append(a)
    with caplog.at_level(logging.WARNING, logger="sqreader.httpsrv"):
        h._stats_500(BrokenPipeError(32, "Broken pipe"))
    assert sent == [], "nothing can be written to a connection the client dropped"
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


def test_a_real_stats_failure_is_still_logged_and_answered(caplog):
    """The guard above must not swallow the failures it was built to surface."""
    import sqlite3
    h = _bare_handler()
    sent = []
    h.send_error = lambda *a, **k: sent.append(a)
    with caplog.at_level(logging.WARNING, logger="sqreader.httpsrv"):
        h._stats_500(sqlite3.DatabaseError("file is not a database"))
    assert sent and sent[0][0] == 500
    assert any("file is not a database" in r.getMessage() for r in caplog.records)


def test_a_huge_query_string_cannot_write_an_unbounded_log_line(caplog):
    """stdlib accepts a 64KB request line, and the path goes straight into the
    log — one request per 64KB of disk on a service with no log rotation."""
    h = _bare_handler()
    h.path = "/api/leaderboard?" + "x" * 60_000
    h.send_error = lambda *a, **k: None
    with caplog.at_level(logging.WARNING, logger="sqreader.httpsrv"):
        h._stats_500(RuntimeError("boom"))
    assert caplog.records
    assert len(caplog.records[0].getMessage()) < 1000, \
        len(caplog.records[0].getMessage())


# ---- a file the server cannot read is a 404 the OPERATOR can still see -------

@pytest.mark.skipif(__import__("os").geteuid() == 0,
                    reason="root reads through mode 000")
@pytest.mark.parametrize("route,name", [("/assets/a.js", "asset"),
                                        ("/icons/weapons/x.png", "icon")])
def test_an_unreadable_file_404s_and_says_why_in_the_log(tmp_path, caplog,
                                                         route, name):
    """A bind mount with the wrong uid must not turn the whole viewer into
    silent 404s with nothing in `docker logs` — that is the failure mode the
    generic 500 body was added to prevent, one screen away."""
    dist = _frontend(tmp_path)
    icons = tmp_path / "icons"
    (icons / "weapons").mkdir(parents=True)
    (icons / "weapons" / "x.png").write_bytes(b"\x89PNG")
    (dist / "assets" / "a.js").chmod(0o000)
    (icons / "weapons" / "x.png").chmod(0o000)
    err = io.StringIO()
    with caplog.at_level(logging.WARNING, logger="sqreader.httpsrv"), \
            contextlib.redirect_stderr(err), \
            _serving(frontend_dir=dist, icons_dir=icons) as port:
        head, _ = _raw_get(port, route)
    assert _status(head) == b"404", head[:80]
    assert "Traceback" not in err.getvalue(), err.getvalue()[:400]
    assert any("PermissionError" in r.getMessage() for r in caplog.records), \
        f"{name}: an unreadable file must leave a trace: " \
        f"{[r.getMessage() for r in caplog.records]}"


def test_a_directory_404s_without_alarming_the_operator(tmp_path, caplog):
    """The `/assets/..` case is expected input, not a fault — 404 it quietly."""
    dist = _frontend(tmp_path)
    with caplog.at_level(logging.WARNING, logger="sqreader.httpsrv"), \
            _serving(frontend_dir=dist) as port:
        head, _ = _raw_get(port, "/assets/..")
    assert _status(head) == b"404", head[:80]
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


def test_a_client_that_hangs_up_mid_response_logs_no_traceback(tmp_path):
    """Closing a tab on a large asset is routine, and must stay quiet.

    The client reads a little, then resets with data still unread, so the
    server's write raises. Only the replay body catches that itself; every
    other write path — the SPA bundle here, and icons and map textures the same
    way — escapes to the server's handle_error, which would print a full
    traceback per abandoned download into a container log that has no rotation
    policy. Small send/receive buffers make the server block mid-body instead
    of dumping the whole response into the kernel's.
    """
    dist = _frontend(tmp_path)
    (dist / "index.html").write_text("<!doctype html>" + "x" * 8_000_000,
                                     encoding="utf-8")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        srv = serve_in_background("127.0.0.1", 0, _TickBeat(), frontend_dir=dist)
        srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        try:
            s = socket.create_connection(("127.0.0.1", srv.server_address[1]),
                                         timeout=5)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
            s.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            s.recv(256)
            # SO_LINGER with a zero timeout: close with RST, not FIN, which is
            # what a browser does when it abandons a response it is behind on.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                         struct.pack("ii", 1, 0))
            s.close()
            time.sleep(1.0)  # let the handler thread reach its write and die
        finally:
            srv.shutdown()
            srv.server_close()

    assert "Traceback" not in err.getvalue(), err.getvalue()[:600]

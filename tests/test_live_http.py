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

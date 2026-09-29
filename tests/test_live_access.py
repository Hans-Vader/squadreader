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

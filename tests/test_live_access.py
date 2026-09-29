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

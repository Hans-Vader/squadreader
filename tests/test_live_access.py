"""Password, sessions and login throttle of the moderator live map (sqreader/live.py)."""
from __future__ import annotations

import time
from http.client import HTTPMessage

import pytest

from sqreader import live

PW = "correct-horse-battery-staple-42"
WRONG = "wrong-password-but-long"


@pytest.mark.parametrize("value, reason", [
    (None, None),
    ("", None),
    (12345, live._NOT_STR),
    (" " + PW, live._PADDED),
    (PW + "\n", live._PADDED),
    ("   ", live._PADDED),
    ("scrypt:16384:8:1:short:key", live._BAD_HASH),
])
def test_unusable_passwords_are_rejected_without_echoing_them(value, reason):
    password, why = live.validate_password(value)
    assert password is None
    assert why == reason
    if why is not None and isinstance(value, str) and value.strip():
        assert value.strip() not in why


def test_a_password_of_any_length_is_accepted():
    assert live.validate_password("x") == ("x", None)
    assert live.validate_password(PW) == (PW, None)
    assert live.Access("x").login("x", "c", [])[0] == "ok"


def test_plain_text_is_refused_where_only_a_hash_may_stand():
    assert live.validate_password(PW, hash_only=True) == (None, live._NOT_HASH)
    h = live.hash_password(PW)
    assert live.validate_password(h, hash_only=True) == (h, None)


def test_a_hash_logs_in_with_its_password_only():
    h = live.hash_password(PW)
    assert h.startswith("scrypt:16384:8:1:")
    assert "$" not in h and "=" not in h          # Compose expands `$` in an .env
    assert live.validate_password(h) == (h, None)
    a = live.Access(h)
    assert a.login(WRONG, "c", []) == ("wrong", None)
    assert a.login(PW, "c", [])[0] == "ok"


def test_hashes_are_salted():
    one, two = live.hash_password(PW), live.hash_password(PW)
    assert one != two
    assert live.Access(two).login(PW, "c", [])[0] == "ok"


GOOD = live.hash_password(PW, salt=b"s" * 16)


@pytest.mark.parametrize("value", [
    "scrypt:",
    GOOD.rsplit(":", 1)[0],                            # no key
    GOOD.replace(":16384:", ":16383:"),                # n not a power of two
    GOOD.replace(":16384:", f":{2**21}:"),             # n too large
    GOOD.replace(":16384:8:", f":{2**20}:8:"),         # 128*n*r beyond 256 MiB
    GOOD.replace(":8:1:", ":8:5:"),                    # p too large
    GOOD.replace(":8:1:", ":x:1:"),                    # not a number
    GOOD[:-4],                                         # key too short
])
def test_broken_or_costly_hashes_are_rejected(value):
    assert live.validate_password(value) == (None, live._BAD_HASH)


def test_reset_with_the_same_hash_says_unchanged():
    h = live.hash_password(PW)
    a = live.Access(h)
    assert a.reset(h) is False
    assert a.reset(live.hash_password(PW)) is True     # same password, new salt: new value


def test_the_environment_hash_wins_over_live_password(monkeypatch):
    h = live.hash_password(PW)
    monkeypatch.setenv(live.ENV_HASH, h)
    assert live.password_from("some-other-password") == (h, None, live.ENV_HASH)
    monkeypatch.setenv(live.ENV_HASH, PW)               # plain text there is refused
    assert live.password_from(PW) == (None, live._NOT_HASH, live.ENV_HASH)
    monkeypatch.setenv(live.ENV_HASH, h + "\n")         # pasted with its newline
    assert live.password_from(PW) == (None, live._PADDED, live.ENV_HASH)
    monkeypatch.setenv(live.ENV_HASH, "")               # Compose's ${…:-} when unset
    assert live.password_from(PW) == (PW, None, "live_password")


def test_the_hash_command_prints_a_working_hash(monkeypatch, capsys):
    answers = iter([PW, PW])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert live.main(["hash"]) == 0
    printed = capsys.readouterr().out.strip()
    assert PW not in printed
    assert live.Access(printed).login(PW, "c", [])[0] == "ok"


@pytest.mark.parametrize("first, second", [
    (PW, PW + "x"), ("", ""), (" " + PW, " " + PW), (GOOD, GOOD),
])
def test_the_hash_command_refuses_mismatches_and_unusable_input(monkeypatch, capsys,
                                                                 first, second):
    answers = iter([first, second])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert live.main(["hash"]) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert PW not in out.err


def test_the_hash_command_wants_its_word(capsys):
    assert live.main([]) == 2
    assert "usage: python3 -m sqreader.live hash" in capsys.readouterr().err


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


def test_reset_says_whether_the_password_changed():
    a = live.Access(PW)
    _, token = a.login(PW, "c", [])
    assert a.reset(PW) is False
    assert not a.valid(token)                     # unchanged or not, nobody stays logged in
    assert a.reset("another-password-long-enough") is True
    assert a.reset("another-password-long-enough") is False
    assert a.reset(None) is True                  # "no password" is a value of its own
    assert a.reset(None) is False
    assert a.reset(PW) is True                    # a repaired config after a failed reload


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
    # ipaddress accepts ANY text after "%" as an IPv6 scope id, CR/LF included, and
    # str() of that address repeats it; only the /64 network drops it again.
    (("fe80::1%x\r\n INFO sqreader.live: forged",), "172.18.0.2", "fe80::/64"),
    ((), "garbage", "unknown"),
])
def test_client_key(forwarded, peer, key):
    got = live.client_key(_Headers(*forwarded), peer)
    assert got == key
    assert "\r" not in got and "\n" not in got and "INFO" not in got     # log-safe, always


def _sqr_live(*values):
    """The request headers of a browser that sends one sqr_live cookie per value."""
    msg = HTTPMessage()
    msg["Cookie"] = "; ".join(f"{live.COOKIE}={v}" for v in values)
    return msg


def test_cookie_values_reads_at_most_the_first_eight():
    a = live.Access(PW)
    _, token = a.login(PW, "c", [])
    last_one_read = [f"junk{i}" for i in range(live.MAX_COOKIE_VALUES - 1)] + [token]
    assert a.first_valid(live.cookie_values(_sqr_live(*last_one_read))) == token
    flood = [f"junk{i}" for i in range(20)] + [token]
    values = live.cookie_values(_sqr_live(*flood))
    assert values == flood[:live.MAX_COOKIE_VALUES]
    assert a.first_valid(values) is None                # the valid one came too late
    # The cap is for the whole request, not per Cookie header.
    one_header_each = HTTPMessage()
    for value in flood:
        one_header_each["Cookie"] = f"{live.COOKIE}={value}"
    assert live.cookie_values(one_header_each) == flood[:live.MAX_COOKIE_VALUES]


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


def test_reaching_the_global_limit_is_logged_once(monkeypatch, caplog):
    caplog.set_level("WARNING", logger="sqreader.live")
    monkeypatch.setattr(live, "FAILS_GLOBAL", 3)
    a = live.Access(PW)
    for i in range(6):                  # the third ends the budget; the rest are refused
        a.login(WRONG, f"10.0.0.{i}", [])
    assert caplog.text.count("live: global login limit reached") == 1
    assert "live: login limit reached for" not in caplog.text    # nobody had 5 of their own


def test_failures_stay_bounded_under_forged_client_keys():
    a = live.Access(PW)
    for i in range(1000):
        a.login(WRONG, f"10.{i // 250}.{i % 250}.1", [])
    assert len(a._fails) <= live.FAILS_GLOBAL

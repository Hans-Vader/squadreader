"""SIGHUP revokes every moderator session and re-reads live_password; and the
live map is wired into `sqreader serve`."""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

import pytest

import sqreader
from live_helpers import PW, Stream, cookie, login, request, running
from sqreader import live

NEW = "a-brand-new-password-for-mods"


def _config(tmp_path, monkeypatch, content):
    path = tmp_path / "sqreader.config.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("SQREADER_CONFIG", str(path))
    return path


def _warning(text):
    """How caplog.record_tuples spells a WARNING of the live logger."""
    return ("sqreader.live", logging.WARNING, text)


def test_reload_rotates_the_password_and_revokes_everyone(tmp_path, monkeypatch, caplog):
    _config(tmp_path, monkeypatch, json.dumps({"live_password": NEW}))
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        lm.reload()
        _, _, body = request(port, "GET", "/api/live/session", headers=cookie(token))
        assert json.loads(body) == {"authenticated": False}
        assert login(port, PW)[0] == 401
        assert login(port, NEW)[0] == 200
    assert _warning("live: SIGHUP: all sessions revoked, password reloaded") in caplog.record_tuples


def test_reloading_the_same_password_says_unchanged(tmp_path, monkeypatch, caplog):
    """A Docker single-file bind mount keeps serving the old inode after an editor
    replaced the file: the reload must not claim that it reloaded anything."""
    path = _config(tmp_path, monkeypatch, json.dumps({"live_password": PW}))
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        lm.reload()
        _, _, body = request(port, "GET", "/api/live/session", headers=cookie(token))
        assert json.loads(body) == {"authenticated": False}        # revoked all the same
        assert login(port, PW)[0] == 200
    assert _warning("live: SIGHUP: all sessions revoked; "
                    f"live_password in {path} is UNCHANGED") in caplog.record_tuples
    assert "password reloaded" not in caplog.text


@pytest.mark.parametrize("content, why", [
    ("{not json", "JSONDecodeError"),
    (json.dumps({"live_password": " padded "}),
     "live_password has leading or trailing whitespace"),
    (json.dumps({"live_password": "scrypt:nope"}), "live_password is not a valid scrypt hash"),
    (json.dumps({}), "live_password is not set"),
    (json.dumps(["x"]), "live_password is not set"),
])
def test_a_broken_config_fails_closed_and_says_why(tmp_path, monkeypatch, caplog, content, why):
    path = _config(tmp_path, monkeypatch, content)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        lm.reload()
        assert login(port, PW)[0] == 401
    assert _warning(f"live: SIGHUP: all sessions revoked; NO valid live_password in {path} "
                    f"({why}), logins disabled until fixed") in caplog.record_tuples


def test_sighup_with_an_environment_hash_revokes_and_keeps_it(monkeypatch, caplog):
    h = live.hash_password(PW)
    monkeypatch.setenv(live.ENV_HASH, h)
    monkeypatch.setenv("SQREADER_CONFIG", "/nonexistent/never-read.json")
    lm = live.LiveMap(h)
    _, token = lm.access.login(PW, "c", [])
    lm.reload()
    assert not lm.access.valid(token)
    assert lm.access.login(PW, "c", [])[0] == "ok"
    assert _warning(f"live: SIGHUP: all sessions revoked; password from {live.ENV_HASH} is "
                    "UNCHANGED (environment: change it with a restart between rounds)"
                    ) in caplog.record_tuples


def test_the_log_names_the_error_type_and_never_its_message(tmp_path, monkeypatch, caplog):
    """An exception message can carry pieces of the file, and the file holds a
    credential: the log gets the type name and nothing else."""
    _config(tmp_path, monkeypatch, '{"live_password": "leaked-secret-password-value" oops}')
    live.LiveMap(PW).reload()
    assert "(JSONDecodeError)" in caplog.text
    assert "Expecting" not in caplog.text and "line 1 column" not in caplog.text
    assert "leaked-secret-password-value" not in caplog.text


def test_a_missing_config_file_fails_closed_and_says_why(tmp_path, monkeypatch, caplog):
    gone = tmp_path / "gone.json"
    monkeypatch.setenv("SQREADER_CONFIG", str(gone))
    lm = live.LiveMap(PW)
    lm.reload()
    assert lm.access.login(PW, "c", [])[0] == "wrong"
    assert _warning(f"live: SIGHUP: all sessions revoked; NO valid live_password in {gone} "
                    "(FileNotFoundError), logins disabled until fixed") in caplog.record_tuples


def test_an_unresolvable_config_path_still_revokes_everyone(monkeypatch, caplog):
    """Path.cwd() raises once the process's cwd was deleted (a directory-swap
    deploy). Revoking must not depend on finding the file."""
    lm = live.LiveMap(PW)
    _, token = lm.access.login(PW, "c", [])
    assert lm.access.valid(token)
    cursor, wake, _ = lm.hub.subscribe(1)

    def cwd_is_gone():
        raise FileNotFoundError("the working directory was deleted")

    monkeypatch.setattr(live, "config_path", cwd_is_gone)
    lm.reload()
    assert not lm.access.valid(token)
    assert lm.access.login(PW, "c", [])[0] == "wrong"
    assert lm.hub.wait(cursor, wake, 2)[3] != wake        # kicked: streams re-check
    assert _warning("live: SIGHUP: all sessions revoked; NO valid live_password in None "
                    "(FileNotFoundError), logins disabled until fixed") in caplog.record_tuples
    assert "the working directory was deleted" not in caplog.text    # the type, not the message


def test_sighup_hands_the_reload_to_a_thread(monkeypatch):
    lm = live.LiveMap(PW)
    seen = []
    done = threading.Event()

    def fake_reload():
        seen.append(threading.current_thread().name)
        done.set()

    monkeypatch.setattr(lm, "reload", fake_reload)
    lm.on_sighup(1, None)
    assert done.wait(2)
    assert seen == ["sqreader-live-reload"]


def test_sighup_fails_closed_inline_when_no_thread_can_start(monkeypatch):
    """A signal handler that raises would surface inside the reader's tick loop.
    Out of threads, it revokes on the spot instead."""
    lm = live.LiveMap(PW)
    _, token = lm.access.login(PW, "c", [])
    cursor, wake, _ = lm.hub.subscribe(1)

    def no_thread(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_thread)
    lm.on_sighup(1, None)                                  # must not raise
    assert not lm.access.valid(token)
    assert lm.access.login(PW, "c", [])[0] == "wrong"
    assert lm.hub.wait(cursor, wake, 2)[3] != wake        # kicked: streams re-check


def test_reload_ends_open_streams(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch, json.dumps({"live_password": NEW}))
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st = Stream(port, token)
        try:
            st.head()
            st.event()
            lm.reload()
            assert st.closed_within(2)
        finally:
            st.close()


def test_cli_wires_the_live_map_into_serve():
    """Merge guard: upstream edits cmd_serve often, and a merge that drops one
    of these lines leaves the live map silently dead or silently frozen."""
    src = (Path(sqreader.__file__).parent / "cli.py").read_text(encoding="utf-8")
    uses = ("live.publish(line, full=True)",
            "live.publish(pos_line, full=False)",
            'live.recording = lambda: record_state_box["current"]',
            "signal.signal(signal.SIGHUP, live.on_sighup)")
    for needle in ('live = live_from_config(config.get("live_password"), recordings_dir)',
                   "live=live", *uses):
        assert needle in src, needle
    # A bare `live.<x>` would raise AttributeError in the public build, where
    # live is None: each use must stay behind its guard.
    for use in uses:
        assert re.search(rf"if live is not None:\s*{re.escape(use)}", src), \
            f"{use} lost its `if live is not None:` guard"


def test_the_example_config_documents_live_password():
    example = Path(sqreader.__file__).resolve().parent.parent / "sqreader.config.example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    assert data["live_password"] is None
    text = " ".join(data["_live_comment"])
    for needle in ("chmod 600", "SIGHUP", "?mode=live", "docs/live-map.md"):
        assert needle in text, needle

"""SIGHUP revokes every moderator session and re-reads live_password; and the
live map is wired into `sqreader serve`."""
from __future__ import annotations

import json
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
    assert "live: SIGHUP: all sessions revoked, password reloaded" in caplog.text


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"live_password": "short"}),
    json.dumps({}),
    json.dumps(["x"]),
])
def test_a_broken_config_fails_closed(tmp_path, monkeypatch, caplog, content):
    path = _config(tmp_path, monkeypatch, content)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        lm.reload()
        assert login(port, PW)[0] == 401
    assert f"NO valid live_password in {path}" in caplog.text


def test_a_missing_config_file_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SQREADER_CONFIG", str(tmp_path / "gone.json"))
    lm = live.LiveMap(PW)
    lm.reload()
    assert lm.access.login(PW, "c", [])[0] == "wrong"


def test_config_path_follows_config_py(tmp_path, monkeypatch):
    monkeypatch.delenv("SQREADER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert live.config_path() == tmp_path / "sqreader.config.json"
    monkeypatch.setenv("SQREADER_CONFIG", "/etc/x.json")
    assert live.config_path() == Path("/etc/x.json")


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
    for needle in ('live = live_from_config(config.get("live_password"))',
                   "live=live",
                   "live.publish(line, full=True)",
                   "live.publish(pos_line, full=False)",
                   "signal.signal(signal.SIGHUP, live.on_sighup)"):
        assert needle in src, needle

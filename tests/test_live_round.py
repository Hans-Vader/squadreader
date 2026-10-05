"""The running round, streamed from its growing .sqrx to logged-in moderators:
GET /api/live/round[/<id>[/meta]] (sqreader/live.py)."""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone

import pytest
import zstandard as zstd

from live_helpers import PW, Stream, cookie, login, request, running, wait_for
from sqreader import live
from sqreader.recorder import RecordingState
from sqreader.sqrx import SqrxWriter

STEM = "2026-10-05_120000_Gorodok_RAAS_v1_abcd1234"


def frame(i, pad=0):
    return json.dumps({"timestamp": f"2026-10-05T12:00:{i:02d}+00:00", "tick": i,
                       "players": [], "pad": "x" * pad})


def pos(i):
    return json.dumps({"t": "pos", "tick": i, "timestamp": f"2026-10-05T12:00:{i:02d}.5+00:00",
                       "players": [], "vehicles": []})


class Round:
    """A recording in progress, written and announced the way recorder.py does."""

    def __init__(self, tmp_path):
        path = tmp_path / f"{STEM}.sqrx"
        self.state = RecordingState(match_id="m1", writer=SqrxWriter(path, "srv"), path=path,
                                    started_at=datetime(2026, 10, 5, 11, 59, tzinfo=timezone.utc))
        self.box = {"current": self.state}

    def write(self, line):
        self.state.writer.write_line(line)
        if not line.startswith('{"t": "pos"'):
            ts = json.loads(line)["timestamp"]
            self.state.first_snap_ts = self.state.first_snap_ts or ts
            self.state.last_snap_ts = ts

    def end(self):
        self.box["current"] = None
        self.state.writer.close()


@pytest.fixture
def setup(tmp_path):
    rnd = Round(tmp_path)
    lm = live.LiveMap(PW)
    lm.recording = lambda: rnd.box["current"]
    with running(lm) as port:
        _, token, _ = login(port)
        try:
            yield port, token, rnd, lm
        finally:
            rnd.end()            # every open stream ends within POLL_SEC


def _open(port, token, query=""):
    return Stream(port, token, f"/api/live/round/{STEM}{query}")


# -- who may ask, and what about ------------------------------------------------

@pytest.mark.parametrize("path", ["/api/live/round", f"/api/live/round/{STEM}",
                                  f"/api/live/round/{STEM}/meta", "/api/live/round/x/y"])
def test_every_round_path_asks_for_a_session_first(path):
    with running(live.LiveMap(PW)) as port:          # no round either: 401 must not tell
        st, hdrs, body = request(port, "GET", path)
    assert (st, json.loads(body)) == (401, {"error": "not logged in"})
    assert hdrs["Cache-Control"] == "no-store"


def test_without_a_round_everything_is_404():
    with running(live.LiveMap(PW)) as port:
        _, token, _ = login(port)
        for path in ("/api/live/round", f"/api/live/round/{STEM}",
                     f"/api/live/round/{STEM}/meta"):
            assert request(port, "GET", path, headers=cookie(token))[0] == 404, path


def test_meta_describes_the_running_round(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    rnd.write(pos(1))
    rnd.write(frame(3))
    want = {"id": STEM, "startedAtUtc": "2026-10-05T12:00:01+00:00",
            "latestUtc": "2026-10-05T12:00:03+00:00", "durationSec": 0}
    for path in ("/api/live/round", f"/api/live/round/{STEM}/meta"):
        st, hdrs, body = request(port, "GET", path, headers=cookie(token))
        assert (st, json.loads(body)) == (200, want), path
        assert hdrs["Cache-Control"] == "no-store"
        assert hdrs.get("Access-Control-Allow-Origin") is None
    for path in ("/api/live/round/other/meta", "/api/live/round/other",
                 f"/api/live/round/{STEM}/x", "/api/live/roundabout"):
        assert request(port, "GET", path, headers=cookie(token))[0] == 404, path


def test_meta_before_the_first_frame_falls_back_to_the_start(setup):
    port, token, _, _ = setup
    _, _, body = request(port, "GET", "/api/live/round", headers=cookie(token))
    m = json.loads(body)
    assert m["startedAtUtc"] == m["latestUtc"] == "2026-10-05T11:59:00+00:00"


def test_live_needs_recordings(caplog, tmp_path, monkeypatch):
    monkeypatch.delenv(live.ENV_HASH, raising=False)
    assert live.live_from_config(PW, None) is None
    assert "live map disabled: it needs --recordings-dir" in caplog.text
    assert isinstance(live.live_from_config(PW, tmp_path), live.LiveMap)


# -- the stream -----------------------------------------------------------------

def test_the_stream_sends_what_exists_then_follows(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    rnd.write(pos(1))
    st = _open(port, token)
    try:
        head = st.head()
        assert head.startswith(b"HTTP/1.0 200 ")
        for h in (b"Content-Type: application/x-ndjson", b"Cache-Control: no-store",
                  b"X-Accel-Buffering: no"):
            assert h in head, h
        assert [json.loads(st.line())["tick"] for _ in range(2)] == [1, 1]
        rnd.write(frame(2))
        assert json.loads(st.line())["tick"] == 2
    finally:
        st.close()


def test_a_frame_written_in_two_halves_arrives_whole(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        raw = zstd.ZstdCompressor().compress((frame(2) + "\n").encode())
        with open(rnd.state.path, "ab") as f:
            f.write(raw[: len(raw) // 2])
            f.flush()
            with pytest.raises(TimeoutError):
                st.line(timeout=0.6)
            f.write(raw[len(raw) // 2:])
        assert json.loads(st.line())["tick"] == 2
    finally:
        st.close()


def test_from_starts_at_the_first_full_frame_at_or_after_it(setup):
    port, token, rnd, _ = setup
    for i in range(1, 6):
        rnd.write(frame(i))
        rnd.write(pos(i))
    from_ms = int(datetime(2026, 10, 5, 12, 0, 2, 500000, tzinfo=timezone.utc).timestamp() * 1000)
    st = _open(port, token, f"?from={from_ms}")
    try:
        st.head()
        first = json.loads(st.line())
        assert (first["tick"], first.get("t")) == (3, None)
    finally:
        st.close()


@pytest.mark.parametrize("query", ["?from=abc", "?from=-5", "?from="])
def test_a_bogus_from_streams_from_the_start(setup, query):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token, query)
    try:
        assert st.head().startswith(b"HTTP/1.0 200 ")
        assert json.loads(st.line())["tick"] == 1
    finally:
        st.close()


def test_the_stream_ends_with_the_round_and_keeps_its_last_frame(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        rnd.write(frame(2))
        rnd.end()
        assert json.loads(st.line())["tick"] == 2
        assert st.closed_within(2)
    finally:
        st.close()


@pytest.mark.parametrize("revoke", ["logout", "reset"])
def test_a_revoked_session_ends_the_stream(setup, revoke):
    port, token, rnd, lm = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        if revoke == "logout":
            request(port, "POST", "/api/live/logout", {}, cookie(token))
        else:
            lm.access.reset(PW)
        assert st.closed_within(1)
    finally:
        st.close()


def test_a_revoked_session_ends_the_stream_mid_backlog(setup):
    """The backlog of a long round streams without ever reaching the end of the
    file, so the session is checked per line, not only while waiting there."""
    port, token, rnd, _ = setup
    for _ in range(400):
        rnd.write(frame(1, pad=20_000))           # ~8 MB, beyond any socket buffer
    st = Stream(port, token, f"/api/live/round/{STEM}", rcvbuf=4096)
    try:
        st.head()
        st.line()
        request(port, "POST", "/api/live/logout", {}, cookie(token))
        assert st.closed_within(5)
        assert st.buf.count(b"\n") < 399          # cut short, not the whole backlog
    finally:
        st.close()


def test_the_stream_limit_answers_503_and_gives_slots_back(setup, monkeypatch):
    port, token, rnd, lm = setup
    monkeypatch.setattr(live, "MAX_STREAMS", 2)
    rnd.write(frame(1))
    streams = [_open(port, token) for _ in range(2)]
    try:
        for st in streams:
            st.head()
        code, hdrs, body = request(port, "GET", f"/api/live/round/{STEM}", headers=cookie(token))
        assert (code, hdrs["Retry-After"], json.loads(body)) == (
            503, "30", {"error": "too many live viewers"})
    finally:
        for st in streams:
            st.close()
    # A gone viewer is noticed at the next frame written to it.
    assert wait_for(lambda: (rnd.write(frame(2)), lm._streams == 0)[1], timeout=5)


def test_gzip_carries_the_same_lines_and_flushes_each(setup):
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    rnd.write(pos(1))
    st = Stream(port, token, f"/api/live/round/{STEM}", headers="Accept-Encoding: gzip\r\n")
    try:
        assert b"Content-Encoding: gzip" in st.head()
        d = zlib.decompressobj(31)
        out = d.decompress(st.buf)
        st.s.settimeout(2)
        while out.count(b"\n") < 2:               # before the round ends: flushed per line
            out += d.decompress(st.s.recv(65536))
        rnd.end()
        st.buf = b""
        assert st.closed_within(2)
        out += d.decompress(st.buf) + d.flush()
        assert d.eof                               # a complete gzip stream
        assert [json.loads(x)["tick"] for x in out.decode().splitlines()] == [1, 1]
    finally:
        st.close()


def test_the_stream_is_logged_without_the_token(setup, caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    port, token, rnd, _ = setup
    rnd.write(frame(1))
    st = _open(port, token)
    try:
        st.head()
        st.line()
        rnd.end()
        assert st.closed_within(2)
    finally:
        st.close()
    assert wait_for(lambda: "live: round stream closed from 127.0.0.1 after 0m00s "
                            "(round ended)" in caplog.text)
    assert "live: round stream opened from 127.0.0.1 (1/10)" in caplog.text
    assert token not in caplog.text

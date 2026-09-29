"""The moderator live stream (Server-Sent Events) end to end over a real socket."""
from __future__ import annotations

import http.client
import json
import time

from live_helpers import (
    PW, Stream, cookie, event, full, login, pos, request, running, wait_for,
)
from sqreader import live


def _open(port, token, **kw):
    st = Stream(port, token, **kw)
    return st, st.head()


def test_no_session_means_401_and_no_slot():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        st, hdrs, body = request(port, "GET", "/api/live/stream")
        assert (st, json.loads(body)) == (401, {"error": "not logged in"})
        assert hdrs["Cache-Control"] == "no-store"
        assert request(port, "GET", "/api/live/stream", headers=cookie("forged"))[0] == 401
        assert lm.hub.subscribers == 0


def test_the_head_is_a_plain_http10_event_stream():
    lm = live.LiveMap(PW)
    lm.publish(full(1), full=True)
    with running(lm, cors_origin="*") as port:
        _, token, _ = login(port)
        st, head = _open(port, token)
        try:
            lines = head.split(b"\r\n")
            assert lines[0] == b"HTTP/1.0 200 OK"
            h = {}
            for ln in lines[1:]:
                if ln:
                    name, _, value = ln.decode().partition(": ")
                    h[name.lower()] = value
            assert h["content-type"] == "text/event-stream; charset=utf-8"
            assert h["cache-control"] == "no-store"
            assert h["x-accel-buffering"] == "no"
            assert h["connection"] == "close"
            for absent in ("content-length", "transfer-encoding", "content-encoding",
                           "access-control-allow-origin"):
                assert absent not in h
            assert st.event() == b"retry: 3000\n\n"
            assert st.event() == event(full(1))
        finally:
            st.close()


def test_frames_arrive_in_order_as_they_are_published():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.event() == b"retry: 3000\n\n"
            for line, is_full in ((full(1), True), (pos(2), False), (full(3), True)):
                lm.publish(line, full=is_full)
                assert st.event() == event(line)
        finally:
            st.close()


def test_a_batch_keeps_every_full_frame_and_only_the_newest_position():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.event() == b"retry: 3000\n\n"
            # Holding the hub's lock keeps the stream thread asleep until all six frames
            # are in, so it wakes to ONE batch. pick() must drop the superseded positions.
            with lm.hub._cond:
                for line, is_full in ((pos(1), False), (full(2), True), (pos(3), False),
                                      (full(4), True), (pos(5), False), (pos(6), False)):
                    lm.publish(line, full=is_full)
            assert [st.event() for _ in range(3)] == [event(full(2)), event(full(4)),
                                                      event(pos(6))]
            lm.publish(pos(7), full=False)
            assert st.event() == event(pos(7))      # nothing else was queued behind the batch
        finally:
            st.close()


def test_before_any_frame_the_stream_keeps_alive_then_delivers(monkeypatch):
    monkeypatch.setattr(live, "KEEPALIVE_SEC", 0.3)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.event() == b"retry: 3000\n\n"
            assert st.event(timeout=2) == b": ka\n\n"
            lm.publish(full(1), full=True)
            assert st.event() == event(full(1))
        finally:
            st.close()


def test_a_large_frame_arrives_intact():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            st.event()
            big = full(1, pad=300_000)
            lm.publish(big, full=True)
            assert st.event() == event(big)
        finally:
            st.close()


def test_logout_ends_that_sessions_stream_at_once_and_only_that_one():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, a, _ = login(port)
        _, b, _ = login(port)
        sa, _ = _open(port, a)
        sb, _ = _open(port, b)
        try:
            sa.event()
            sb.event()
            request(port, "POST", "/api/live/logout", {}, cookie(a))
            assert sa.closed_within(2)            # not after the 15 s keepalive
            lm.publish(full(1), full=True)
            assert sb.event() == event(full(1))
        finally:
            sa.close()
            sb.close()


def test_an_expired_session_ends_an_idle_stream(monkeypatch):
    monkeypatch.setattr(live, "SESSION_TTL_SEC", 0.5)
    monkeypatch.setattr(live, "KEEPALIVE_SEC", 0.2)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.closed_within(3)
        finally:
            st.close()


def test_close_ends_every_stream():
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            st.event()
            lm.hub.close()
            assert st.closed_within(2)
        finally:
            st.close()


def test_the_stream_limit_answers_503(monkeypatch):
    monkeypatch.setattr(live, "MAX_STREAMS", 2)
    monkeypatch.setattr(live, "KEEPALIVE_SEC", 0.2)     # so a gone client is noticed fast
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        s1, _ = _open(port, token)
        s2, _ = _open(port, token)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", "/api/live/stream", headers=cookie(token))
            # Status before body: a stream that was wrongly let in never ends, and its
            # keepalives would keep a read-to-EOF alive forever instead of failing here.
            resp = conn.getresponse()
            assert (resp.status, resp.getheader("Retry-After")) == (503, "30")
            assert json.loads(resp.read()) == {"error": "too many live viewers"}
            s1.close()
            assert wait_for(lambda: lm.hub.subscribers == 1)
        finally:
            conn.close()
            s1.close()
            s2.close()


def test_refused_streams_are_logged_at_most_once_a_minute(monkeypatch, caplog):
    monkeypatch.setattr(live, "MAX_STREAMS", 1)
    caplog.set_level("WARNING", logger="sqreader.live")
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            for _ in range(5):
                assert request(port, "GET", "/api/live/stream", headers=cookie(token))[0] == 503
            assert caplog.text.count("live: stream refused from 127.0.0.1 (1/1 in use)") == 1
        finally:
            st.close()


def test_a_reader_that_stops_reading_is_dropped_and_never_slows_publish(monkeypatch):
    monkeypatch.setattr(live, "WRITE_TIMEOUT_SEC", 0.5)
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token, rcvbuf=4096)       # and then never read again
        try:
            big = full(1, pad=256_000)
            worst = 0.0
            for _ in range(60):
                t0 = time.perf_counter()
                lm.publish(big, full=True)
                worst = max(worst, time.perf_counter() - t0)
                time.sleep(0.02)
            assert worst < 0.05
            assert wait_for(lambda: lm.hub.subscribers == 0, timeout=5)
        finally:
            st.close()


def test_a_reader_the_ring_overtook_is_disconnected(monkeypatch, caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    lm = live.LiveMap(PW)
    real_wait = lm.hub.wait
    calls = []

    def gap_once(cursor, wake, timeout):
        if not calls:
            calls.append(1)
            return "gap", [], cursor, wake
        return real_wait(cursor, wake, timeout)

    monkeypatch.setattr(lm.hub, "wait", gap_once)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        try:
            assert st.closed_within(2)
        finally:
            st.close()
    assert wait_for(lambda: "(too slow)" in caplog.text)


def test_streams_are_logged_with_client_count_and_reason(caplog):
    caplog.set_level("INFO", logger="sqreader.live")
    lm = live.LiveMap(PW)
    with running(lm) as port:
        _, token, _ = login(port)
        st, _ = _open(port, token)
        st.event()
        lm.hub.close()
        assert st.closed_within(2)
        st.close()
    assert "live: stream opened from 127.0.0.1 (1/10)" in caplog.text
    assert wait_for(lambda: "live: stream closed from 127.0.0.1 after 0m0" in caplog.text)
    assert "(server stopping)" in caplog.text

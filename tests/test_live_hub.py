"""The live map's frame fan-out (sqreader/live.py: Hub, pick)."""
from __future__ import annotations

import threading
import time

from sqreader import live


def _ev(seq, full):
    return (seq, full, f"{'F' if full else 'P'}{seq}".encode())


def _line(n):
    return f'{{"tick": {n}}}\n'


def test_pick_keeps_every_full_frame_and_only_the_newest_position():
    p1, f2, p3, p4 = _ev(1, False), _ev(2, True), _ev(3, False), _ev(4, False)
    f5, p6, p7 = _ev(5, True), _ev(6, False), _ev(7, False)
    assert live.pick([p1, f2, p3, p4, f5, p6, p7]) == [f2, f5, p7]
    assert live.pick([p1, p3]) == [p3]
    assert live.pick([p1, f2]) == [f2]            # a full frame supersedes the position before it
    assert live.pick([f2]) == [f2]
    assert live.pick([f2, p3]) == [f2, p3]
    assert live.pick([]) == []


def test_a_new_reader_gets_the_newest_full_frame_and_then_everything_new():
    hub = live.Hub()
    hub.publish(_line(1), full=True)
    hub.publish(_line(2), full=False)
    hub.publish(_line(3), full=True)
    cursor, wake, first = hub.subscribe(10)
    assert first == b'data: {"tick": 3}\n\n'
    hub.publish(_line(4), full=False)
    status, events, cursor, wake = hub.wait(cursor, wake, 1)
    assert status == "ok"
    assert [p for _s, _f, p in events] == [b'data: {"tick": 4}\n\n']


def test_nothing_is_buffered_while_nobody_watches():
    hub = live.Hub()
    for n in range(500):
        hub.publish(_line(n), full=False)
    assert len(hub._ring) == 0
    cursor, wake, first = hub.subscribe(10)
    assert first is None                      # no full frame yet
    hub.unsubscribe()
    hub.publish(_line(1), full=True)
    assert len(hub._ring) == 0


def test_a_reader_the_ring_overtook_is_told_so(monkeypatch):
    monkeypatch.setattr(live, "RING_SIZE", 3)
    hub = live.Hub()
    cursor, wake, _ = hub.subscribe(10)
    for n in range(5):
        hub.publish(_line(n), full=True)
    assert hub.wait(cursor, wake, 1)[0] == "gap"


def test_kick_and_close_wake_a_waiting_reader():
    hub = live.Hub()
    cursor, wake, _ = hub.subscribe(10)
    results = []
    t = threading.Thread(target=lambda: results.append(hub.wait(cursor, wake, 10)))
    t.start()
    time.sleep(0.1)
    hub.kick()
    t.join(2)
    assert results
    assert results[0][0] == "ok"
    assert results[0][1] == []
    hub.close()
    assert hub.wait(cursor, results[0][3], 10)[0] == "closed"


def test_the_stream_limit_is_enforced_atomically():
    hub = live.Hub()
    assert hub.subscribe(2) is not None
    assert hub.subscribe(2) is not None
    assert hub.subscribe(2) is None
    hub.unsubscribe()
    assert hub.subscribe(2) is not None
    assert hub.subscribers == 2


def test_publish_never_raises_and_logs_the_first_failure_only(caplog):
    hub = live.Hub()
    hub.publish(None, full=True)          # type: ignore[arg-type]
    hub.publish(None, full=True)          # type: ignore[arg-type]
    assert caplog.text.count("live: publish failed") == 1
    hub.publish(_line(1), full=True)      # and it keeps working
    assert hub.subscribe(10)[2] == b'data: {"tick": 1}\n\n'


def test_publish_stays_fast_with_a_reader_that_never_reads():
    hub = live.Hub()
    hub.subscribe(10)                     # subscribed, never waits
    line = '{"x": "' + "a" * 100_000 + '"}\n'
    t0 = time.perf_counter()
    for _ in range(2000):
        hub.publish(line, full=True)
    assert time.perf_counter() - t0 < 2.0
    assert len(hub._ring) == live.RING_SIZE

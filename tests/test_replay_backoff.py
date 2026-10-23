"""A failing replay waits longer between tries and is retired, never retried
on every flush. Up to 1.4.8 the backlog re-sent every failed replay about every
30 s; an operator's agent re-uploaded 88 refused replays that way and burned
~20 TiB before it was stopped (issue #5). No network: push fns are faked and
the clock is the test's."""
import io
import json
import types
import urllib.error

import pytest

from sqreader import ingest_client as ic

from test_ingest_client import MID, _make_db, _write_replay  # tests/ is on sys.path

CREDS = {"SQREADER_AGENT_ID": "eu1", "SQREADER_AGENT_SECRET_HEX": "ab" * 32,
         "SQREADER_PUSH_URL": "https://c.example"}
FLUSH_EVERY = 30.0       # the serve loop's cadence


@pytest.fixture
def clock(monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(ic, "time", types.SimpleNamespace(
        time=lambda: now[0], sleep=lambda s: None))
    return now


def _setup(tmp_path, monkeypatch, replay_fn):
    db = _make_db(tmp_path)
    rec = tmp_path / "recordings"
    _write_replay(rec, "aa", MID, in_progress=False)
    backlog = tmp_path / "queue"
    monkeypatch.setattr(ic, "push_match", lambda *a, **k: None)
    monkeypatch.setattr(ic, "push_replay", replay_fn)
    ic.enqueue(backlog, MID)
    return db, rec, backlog


def test_retry_schedule_grows_and_is_bounded():
    waits = [ic._replay_retry_delay(n) for n in range(1, ic._REPLAY_MAX_ATTEMPTS)]
    assert waits[:5] == [60, 240, 960, 3840, 15360]
    assert max(waits) == ic._REPLAY_RETRY_MAX_SEC
    assert waits == sorted(waits)
    # long enough to ride out a central that is down overnight
    assert 12 * 3600 < sum(waits) < 36 * 3600


def test_refused_replay_is_sent_a_handful_of_times_not_every_flush(
        tmp_path, monkeypatch, clock):
    uploads = []

    def refuse(*a, **k):
        uploads.append(clock[0])
        raise ic.PushError("central HTTP 502")
    db, rec, backlog = _setup(tmp_path, monkeypatch, refuse)

    results = []
    for _ in range(int(2 * 86400 / FLUSH_EVERY)):     # two days of flushes
        results.append(ic.flush_backlog(backlog, db, rec, CREDS))
        clock[0] += FLUSH_EVERY

    # 5760 flushes; 1.4.8 would have uploaded the file 5760 times
    assert len(uploads) == ic._REPLAY_MAX_ATTEMPTS
    gaps = [b - a for a, b in zip(uploads, uploads[1:], strict=False)]
    waits = [ic._replay_retry_delay(n) for n in range(1, len(uploads))]
    assert all(g >= w for g, w in zip(gaps, waits, strict=True))
    assert list(backlog.glob("*.json")) == []          # retired, queue drained
    assert sum(r["skipped"] for r in results) == 1
    assert sum(r["sent"] for r in results) == 0


def test_waiting_replay_counts_as_pending_and_is_not_sent(
        tmp_path, monkeypatch, clock):
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise ic.PushError("central unreachable")
    db, rec, backlog = _setup(tmp_path, monkeypatch, flaky)

    first = ic.flush_backlog(backlog, db, rec, CREDS)
    assert first["errors"] == 1 and len(calls) == 1
    state = json.loads(next(backlog.glob("*.json")).read_text())
    assert state["replay_attempts"] == 1 and state["stats_done"] is True

    clock[0] += FLUSH_EVERY                              # inside the wait
    mid = ic.flush_backlog(backlog, db, rec, CREDS)
    assert mid["pending"] == 1 and len(calls) == 1

    clock[0] += ic._REPLAY_RETRY_FIRST_SEC               # wait over
    last = ic.flush_backlog(backlog, db, rec, CREDS)
    assert last["sent"] == 1 and len(calls) == 2
    assert list(backlog.glob("*.json")) == []


def test_permanent_refusal_is_retired_at_once(tmp_path, monkeypatch, clock):
    calls = []

    def too_big(*a, **k):
        calls.append(1)
        raise ic.PushError("central HTTP 413", permanent=True)
    db, rec, backlog = _setup(tmp_path, monkeypatch, too_big)

    res = ic.flush_backlog(backlog, db, rec, CREDS)
    clock[0] += 7 * 86400
    again = ic.flush_backlog(backlog, db, rec, CREDS)

    assert len(calls) == 1
    assert res["skipped"] == 1 and again == {
        "sent": 0, "pending": 0, "errors": 0, "skipped": 0}


def test_stats_still_go_while_the_replay_waits(tmp_path, monkeypatch, clock):
    sent_stats = []

    def refuse(*a, **k):
        raise ic.PushError("central HTTP 502")
    db, rec, backlog = _setup(tmp_path, monkeypatch, refuse)
    monkeypatch.setattr(ic, "push_match",
                        lambda c, b, **k: sent_stats.append(b["match"]["match_id"]))
    ic.flush_backlog(backlog, db, rec, CREDS)
    assert sent_stats == [MID]                           # once, not per retry
    clock[0] += 10
    ic.flush_backlog(backlog, db, rec, CREDS)
    assert sent_stats == [MID]


def test_recording_that_never_turns_up_is_retired_after_a_day(
        tmp_path, monkeypatch, clock):
    db = _make_db(tmp_path)
    rec = tmp_path / "recordings"
    rec.mkdir()
    backlog = tmp_path / "queue"
    monkeypatch.setattr(ic, "push_match", lambda *a, **k: None)
    monkeypatch.setattr(ic, "push_replay",
                        lambda *a, **k: pytest.fail("nothing to push"))
    ic.enqueue(backlog, MID)

    assert ic.flush_backlog(backlog, db, rec, CREDS)["pending"] == 1
    clock[0] += 23 * 3600
    assert ic.flush_backlog(backlog, db, rec, CREDS)["pending"] == 1
    clock[0] += 2 * 3600
    res = ic.flush_backlog(backlog, db, rec, CREDS)
    assert res["skipped"] == 1 and list(backlog.glob("*.json")) == []


def test_oversized_replay_is_refused_before_a_byte_is_read(tmp_path, monkeypatch):
    big = tmp_path / "big.sqrx"
    big.write_bytes(b"x" * 64)
    monkeypatch.setattr(ic, "_MAX_REPLAY_BYTES", 32)
    monkeypatch.setattr(ic, "_send", lambda *a, **k: pytest.fail("sent"))
    with pytest.raises(ic.PushError) as e:
        ic.push_replay(CREDS, MID, "eu1", big, seq=1)
    assert e.value.permanent is True


@pytest.mark.parametrize("code,permanent", [(413, True), (400, False), (403, False)])
def test_only_a_413_is_a_permanent_refusal(monkeypatch, code, permanent):
    def urlopen(*a, **k):
        raise urllib.error.HTTPError("https://c.example/ingest/replay", code,
                                     "no", {}, io.BytesIO(b""))
    monkeypatch.setattr(ic.urllib.request, "urlopen", urlopen)
    req = ic.urllib.request.Request("https://c.example/ingest/replay", data=b"x")
    with pytest.raises(ic.PushError) as e:
        ic._send(req, timeout=1)
    assert e.value.permanent is permanent

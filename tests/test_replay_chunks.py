"""A replay larger than the proxy's body cap goes up in parts.

Cloudflare answers any request body over 100 MiB with its own 413 before
central sees it, and a full match recorded at 4 Hz is 250-350 MiB, so every
such replay was given up on. Thresholds are shrunk here so the split is
exercised on a small file.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request

from sqreader import ingest_client as ic
from sqreader.crypto_envelope import open_envelope

SECRET = bytes(range(32))
CREDS = {"SQREADER_AGENT_ID": "eu1", "SQREADER_AGENT_SECRET_HEX": SECRET.hex(),
         "SQREADER_PUSH_URL": "https://central.test"}


class _Resp:
    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


def _capture(monkeypatch) -> list[urllib.request.Request]:
    sent: list[urllib.request.Request] = []

    def _urlopen(req, timeout=None):
        sent.append(req)
        return _Resp()

    monkeypatch.setattr(ic.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(ic, "_push_base", lambda creds: "https://central.test")
    return sent


def _manifest(req: urllib.request.Request) -> dict:
    env = json.loads(req.get_header("X-sqr-manifest"))
    return json.loads(open_envelope(env, secret=SECRET))


def test_a_large_replay_is_sent_in_parts_that_rebuild_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_THRESHOLD", 1000)
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_BYTES", 400)
    data = bytes(range(256)) * 10            # 2560 bytes -> 7 parts of <= 400
    f = tmp_path / "m.sqrx"
    f.write_bytes(data)
    sent = _capture(monkeypatch)

    ic.push_replay(CREDS, "M1", "eu1", f, seq=7)

    assert len(sent) == 7
    assert all(r.full_url.endswith("/ingest/replay") for r in sent)
    assert b"".join(r.data for r in sent) == data
    whole = hashlib.sha256(data).hexdigest()
    for i, r in enumerate(sent):
        m = _manifest(r)
        assert m["match_id"] == "M1" and m["server_id"] == "eu1"
        assert m["size"] == len(data) and m["sha256"] == whole
        assert m["chunk_index"] == i and m["chunk_count"] == 7
        assert m["chunk_size"] == len(r.data) <= 400
        assert m["chunk_sha256"] == hashlib.sha256(r.data).hexdigest()


def test_a_replay_under_the_threshold_still_goes_in_one_request(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_THRESHOLD", 1000)
    data = b"x" * 999
    f = tmp_path / "m.sqrx"
    f.write_bytes(data)
    sent = _capture(monkeypatch)

    ic.push_replay(CREDS, "M1", "eu1", f, seq=7)

    assert len(sent) == 1 and sent[0].data == data
    m = _manifest(sent[0])
    assert "chunk_count" not in m and m["size"] == 999


def test_a_failed_part_stops_the_upload(tmp_path, monkeypatch):
    """The backlog retries the whole replay on the next flush; central keeps
    the parts it already has, so nothing is lost by stopping."""
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_THRESHOLD", 10)
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_BYTES", 10)
    monkeypatch.setattr(ic.time, "sleep", lambda s: None)
    f = tmp_path / "m.sqrx"
    f.write_bytes(b"y" * 35)
    calls = {"n": 0}

    def _urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
        return _Resp()

    monkeypatch.setattr(ic.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(ic, "_push_base", lambda creds: "https://central.test")
    try:
        ic.push_replay(CREDS, "M1", "eu1", f, seq=7)
    except ic.PushError as exc:
        assert "400" in str(exc)
    else:
        raise AssertionError("a refused part must surface as a PushError")
    assert calls["n"] == 2, "no part is sent after one was refused"


def test_a_last_part_central_does_not_call_complete_is_not_success(tmp_path, monkeypatch):
    """If central answers the final part with "still missing", the backlog
    must keep the replay, not retire one central does not have."""
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_THRESHOLD", 10)
    monkeypatch.setattr(ic, "_REPLAY_CHUNK_BYTES", 10)
    f = tmp_path / "m.sqrx"
    f.write_bytes(b"z" * 25)

    class _Answer(_Resp):
        def __init__(self, body: bytes) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body

    answers = [b'{"ok":true,"complete":false,"missing":2}',
               b'{"ok":true,"complete":false,"missing":1}',
               b'{"ok":true,"complete":false,"missing":1}']
    monkeypatch.setattr(ic.urllib.request, "urlopen",
                        lambda req, timeout=None: _Answer(answers.pop(0)))
    monkeypatch.setattr(ic, "_push_base", lambda creds: "https://central.test")
    try:
        ic.push_replay(CREDS, "M1", "eu1", f, seq=7)
    except ic.PushError as exc:
        assert "missing 1" in str(exc)
    else:
        raise AssertionError("an incomplete replay must not count as published")

    answers[:] = [b'{"ok":true,"complete":false,"missing":2}',
                  b'{"ok":true,"complete":false,"missing":1}',
                  b'{"ok":true,"match_id":"M1","complete":true}']
    ic.push_replay(CREDS, "M1", "eu1", f, seq=8)   # whole: no error

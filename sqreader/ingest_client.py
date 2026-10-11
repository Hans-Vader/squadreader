"""Outbound push client: send finished-match stats + .sqrx replays to a central.

This is the ONLY part of sqreader that talks to the network, and only when the
box is enrolled (``.env.agent`` present) and push is enabled. It:

  * builds a self-contained JSON bundle for one finished match (stats/scoreboard
    + kill events + vehicle sessions + ELO) straight from the stats DB,
  * seals it with the shared crypto envelope and POSTs it to ``/ingest/match``,
  * uploads the match's ``.sqrx`` replay to ``/ingest/replay`` (raw zstd body +
    a sealed manifest header), and
  * keeps a small on-disk BACKLOG so nothing is lost if the central is down or
    the replay file has not finished closing yet.

Idempotency is by ``match_id`` (the central dedups), so a re-push is harmless.
HTTP uses stdlib ``urllib`` — no new dependency beyond the opt-in ``cryptography``
that the envelope needs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__, agent_creds, build_config
from .crypto_envelope import EnvelopeError, seal_json

SCHEMA_MATCH = "sqr-match-1"
SCHEMA_REPLAY = "sqr-replay-1"
SCHEMA_CHECKIN = "sqr-checkin-1"
SCHEMA_OFFSETS_REQ = "sqr-offsets-req-1"
SCHEMA_MANIFEST_REQ = "sqr-manifest-req-1"
# Version + platform ride on the UA of EVERY request, so central learns an
# agent's version from an ordinary match push — no separate call needed. Central
# parses `sqreader-push/<version> (<platform>)`.
_UA = f"sqreader-push/{__version__} ({platform.system()})"
_STATS_TABLES = ("matches", "player_matches", "kill_events", "vehicle_session")
# MUST match central's MAX_REPLAY_BYTES. Checked before anything is read or
# sent, so a replay central would refuse anyway costs nothing.
_MAX_REPLAY_BYTES = 2 * 1024 * 1024 * 1024
# A replay that fails is tried again later, each time later still, and not
# forever. Up to 1.4.8 the backlog retried every failed replay on every flush,
# about every 30 s, sending the whole file again each time: an operator whose
# uploads were being refused watched the same 88 replays go up over and over
# and lost ~20 TiB of bandwidth before stopping the agent. The wait starts at a
# minute and grows fourfold to a ceiling of six hours, and the eighth failure
# retires the replay - about 18 hours in all. A central that is down overnight
# still gets its replays; a refusal that will never change costs eight uploads.
# The match's stats are sent separately and are not affected.
_REPLAY_RETRY_FIRST_SEC = 60
_REPLAY_RETRY_FACTOR = 4
_REPLAY_RETRY_MAX_SEC = 6 * 3600
_REPLAY_MAX_ATTEMPTS = 8
# How long to keep looking for a recording that has not turned up. A recorder
# closes its file within seconds of a match ending, so a day without one means
# it was pruned or never kept, not that we are early.
_REPLAY_MISSING_MAX_SEC = 24 * 3600

log = logging.getLogger(__name__)


class PushError(Exception):
    """Central unreachable, refused the push, or a bad response.

    ``permanent`` marks a refusal that retrying cannot change - a replay over
    central's size limit, a 413 - so the backlog retires it at once instead of
    sending the same bytes again later.
    """

    def __init__(self, *args: object, permanent: bool = False) -> None:
        super().__init__(*args)
        self.permanent = permanent


# --------------------------------------------------------------------------
# match bundle (read-only, straight from the stats DB)
# --------------------------------------------------------------------------

def build_match_bundle(db_path: str | Path, match_id: str) -> dict[str, Any] | None:
    """Read one finished match into a JSON-able bundle. None if unknown."""
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        match = _one(conn, "SELECT * FROM matches WHERE match_id=?", (match_id,))
        if match is None:
            return None
        players = _all(conn, "SELECT * FROM player_matches WHERE match_id=?",
                       (match_id,))
        kills = _all(conn, "SELECT * FROM kill_events WHERE match_id=?",
                     (match_id,))
        vehicles = _all(conn, "SELECT * FROM vehicle_session WHERE match_id=?",
                        (match_id,))
        eos = sorted({str(p["eos_id"]) for p in players if p.get("eos_id")})
        elo: list[dict[str, Any]] = []
        if eos:
            marks = ",".join("?" * len(eos))
            elo = _all(conn, f"SELECT * FROM player_elo WHERE eos_id IN ({marks})",
                       tuple(eos))
        return {
            "schema": SCHEMA_MATCH,
            "server_id": match.get("server_id"),
            "match": match,
            "players": players,
            "kills": kills,
            "vehicles": vehicles,
            "elo": elo,
        }
    finally:
        conn.close()


def _one(conn: sqlite3.Connection, sql: str, args: tuple) -> dict[str, Any] | None:
    try:
        row = conn.execute(sql, args).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row is not None else None


def _all(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []  # table absent on an older DB — skip, don't fail the push


# --------------------------------------------------------------------------
# enrollment + push (HTTP)
# --------------------------------------------------------------------------

def enroll(central_url: str, token: str, hw_fp: str, *,
           timeout: float = 30.0) -> dict[str, str]:
    """Redeem an enrollment token → the agent's credentials.

    POSTs {token, hw_fp, platform} to ``{central}/api/enroll`` and returns
    ``{agent_id, secret_hex, push_url, community}``. Raises PushError on any
    failure (the central masks unknown/expired/consumed as the same error).
    """
    body = {"token": token, "hw_fp": hw_fp, "platform": platform.system()}
    resp = _post_json(central_url.rstrip("/") + "/api/enroll", body,
                      timeout=timeout)
    secret = str(resp.get("secret_hex", ""))
    if len(secret) != 64:
        raise PushError("enroll response missing a 32-byte secret_hex")
    return {
        "SQREADER_AGENT_ID": str(resp["agent_id"]),
        "SQREADER_AGENT_SECRET_HEX": secret,
        "SQREADER_PUSH_URL": str(resp.get("push_url") or central_url),
        "SQREADER_COMMUNITY": str(resp.get("community", "")),
    }


def push_match(creds: dict[str, str], bundle: dict[str, Any], *, seq: int,
               timeout: float = 30.0) -> None:
    env = seal_json(bundle, secret=_secret(creds),
                    agent_id=creds["SQREADER_AGENT_ID"], seq=seq)
    _post_json(_push_base(creds) + "/ingest/match", env, timeout=timeout)


def checkin(creds: dict[str, str], payload: dict[str, Any], *, seq: int,
            timeout: float = 15.0) -> dict[str, Any]:
    """Seal + POST a fleet check-in to ``/agent/checkin``.

    Same per-agent AES envelope + auth as a match push (central authenticates by
    decrypting under the agent's secret). Returns the plain-JSON ack. Phase 0's
    ack is just ``{ok:true}``; later phases may add an ADVISORY update hint the
    agent independently verifies (Ed25519) before trusting — so the ack itself
    never needs to be trusted. Best-effort: callers wrap this, a failure is
    logged and forgotten, the live reader is untouched.
    """
    env = seal_json(payload, secret=_secret(creds),
                    agent_id=creds["SQREADER_AGENT_ID"], seq=seq)
    return _post_json(_push_base(creds) + "/agent/checkin", env, timeout=timeout)


def fetch_offsets(creds: dict[str, str], squad_build: str, channel: str, *,
                  seq: int, timeout: float = 15.0) -> dict[str, Any] | None:
    """Ask central for the current SIGNED offset pack for our Squad build +
    channel. Returns the raw (still-UNVERIFIED) signed pack dict, or None if none
    is offered. The caller (offset_client.accept) verifies the Ed25519 signature
    before trusting it — the sealed request authenticates us, but the response is
    only trusted via its own signature."""
    req = {"schema": SCHEMA_OFFSETS_REQ, "squad_build": squad_build,
           "channel": channel}
    env = seal_json(req, secret=_secret(creds),
                    agent_id=creds["SQREADER_AGENT_ID"], seq=seq)
    resp = _post_json(_push_base(creds) + "/agent/offsets", env, timeout=timeout)
    pack = resp.get("pack") if isinstance(resp, dict) else None
    return pack if isinstance(pack, dict) else None


def fetch_manifest(creds: dict[str, str], platform_tok: str, channel: str, *,
                   seq: int, timeout: float = 15.0) -> dict[str, Any] | None:
    """Ask central for the current SIGNED release manifest for our platform +
    channel. Returns the raw (still-UNVERIFIED) manifest dict, or None. The caller
    (updater.accept) verifies the Ed25519 signature before trusting it."""
    req = {"schema": SCHEMA_MANIFEST_REQ, "platform": platform_tok,
           "channel": channel}
    env = seal_json(req, secret=_secret(creds),
                    agent_id=creds["SQREADER_AGENT_ID"], seq=seq)
    resp = _post_json(_push_base(creds) + "/agent/manifest", env, timeout=timeout)
    m = resp.get("manifest") if isinstance(resp, dict) else None
    return m if isinstance(m, dict) else None


def download_auth_header(creds: dict[str, str], file: str, *,
                         seq: int = 0) -> str:
    """Build the `X-Sqr-Auth` value proving this agent may download `file`.

    A sealed envelope over {schema, file}, keyed by the per-agent secret — the
    same primitive /ingest/replay uses. Bound to the filename so a captured
    header cannot be replayed onto a different artifact. Returned as compact
    JSON for the header; `sqreader download-auth` prints exactly this.
    """
    env = seal_json({"schema": "sqr-download-1", "file": file},
                    secret=_secret(creds), agent_id=creds["SQREADER_AGENT_ID"],
                    seq=seq)
    return json.dumps(env, separators=(",", ":"))


def download_artifact(creds: dict[str, str], file: str, *,
                      timeout: float = 300.0) -> bytes | None:
    """GET a release artifact by filename from central. Raw bytes (the caller
    verifies sha256). Sends the agent's X-Sqr-Auth so the download works when
    central runs the gate; on an ungated central the header is simply ignored.
    The artifact stays sha256-pinned + Ed25519-signed, so integrity never
    depends on this header."""
    url = (_push_base(creds) + "/agent/download?file="
           + urllib.parse.quote(file, safe=""))
    headers = {"User-Agent": _UA}
    try:
        headers["X-Sqr-Auth"] = download_auth_header(creds, file)
    except (KeyError, PushError):
        pass    # unenrolled / no secret — fall through as a bare GET
    req = urllib.request.Request(url, method="GET", headers=headers)
    return _send(req, timeout=timeout)


#: A replay above this goes up in parts. The proxy in front of central
#: (Cloudflare) answers any request body over 100 MiB with its own 413 before
#: central ever sees it, and a full match recorded at 4 Hz is 250-350 MiB: every
#: one of those replays was being given up on. Parts keep each request well
#: under the cap, whatever address central is reached at.
_REPLAY_CHUNK_THRESHOLD = 90 * 1024 * 1024
_REPLAY_CHUNK_BYTES = 64 * 1024 * 1024


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def _push_replay_parts(creds: dict[str, str], match_id: str, server_id: str,
                       path: Path, size: int, *, seq: int, timeout: float) -> None:
    """Publish a replay too large for one request as numbered parts. Central
    keeps them until all are in and joins them only if the result is exactly
    `size` bytes with this sha256. Each part is read, sealed and sent on its
    own, so neither the whole file nor an envelope gone stale is held for the
    length of the upload; a part that fails is simply sent again next flush.

    The last part's answer is checked, not assumed: central says whether the
    replay is now whole, and a "still missing parts" answer must not let the
    backlog retire a replay central does not have."""
    whole = _file_sha256(path)
    count = (size + _REPLAY_CHUNK_BYTES - 1) // _REPLAY_CHUNK_BYTES
    raw = b""
    with path.open("rb") as f:
        for index in range(count):
            part = f.read(_REPLAY_CHUNK_BYTES)
            manifest = seal_json(
                {"schema": SCHEMA_REPLAY, "server_id": server_id,
                 "match_id": match_id, "size": size, "sha256": whole,
                 "chunk_index": index, "chunk_count": count,
                 "chunk_size": len(part),
                 "chunk_sha256": hashlib.sha256(part).hexdigest()},
                secret=_secret(creds), agent_id=creds["SQREADER_AGENT_ID"],
                seq=seq)
            req = urllib.request.Request(
                _push_base(creds) + "/ingest/replay", data=part, method="POST",
                headers={"Content-Type": "application/octet-stream",
                         "X-Sqr-Manifest": json.dumps(manifest,
                                                      separators=(",", ":")),
                         "User-Agent": _UA})
            raw = _send(req, timeout=timeout)
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    if isinstance(answer, dict) and answer.get("complete") is False:
        raise PushError(f"central is still missing {answer.get('missing', '?')} "
                        f"part(s) of the replay for {match_id}")


def push_replay(creds: dict[str, str], match_id: str, server_id: str,
                sqrx_path: str | Path, *, seq: int, timeout: float = 300.0) -> None:
    path = Path(sqrx_path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PushError(f"replay unreadable: {exc}") from exc
    if size > _MAX_REPLAY_BYTES:
        raise PushError(
            f"replay too large to publish: {size} bytes > {_MAX_REPLAY_BYTES} "
            f"({path.name})", permanent=True)
    if size > _REPLAY_CHUNK_THRESHOLD:
        _push_replay_parts(creds, match_id, server_id, path, size,
                           seq=seq, timeout=timeout)
        return
    data = path.read_bytes()
    manifest = seal_json(
        {"schema": SCHEMA_REPLAY, "server_id": server_id, "match_id": match_id,
         "size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        secret=_secret(creds), agent_id=creds["SQREADER_AGENT_ID"], seq=seq)
    req = urllib.request.Request(
        _push_base(creds) + "/ingest/replay", data=data, method="POST",
        headers={"Content-Type": "application/octet-stream",
                 "X-Sqr-Manifest": json.dumps(manifest, separators=(",", ":")),
                 "User-Agent": _UA})
    _send(req, timeout=timeout)


# --------------------------------------------------------------------------
# backlog queue (durable, idempotent, survives a down central)
# --------------------------------------------------------------------------

def enqueue(backlog_dir: str | Path, match_id: str) -> None:
    """Drop a marker for a just-finalized match. Cheap + crash-safe."""
    d = Path(backlog_dir)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{_safe_id(match_id)}.json"
    if marker.exists():
        return
    _atomic_json(marker, {"match_id": match_id, "created_at": int(time.time()),
                          "stats_done": False, "replay_done": False})


def flush_backlog(backlog_dir: str | Path, db_path: str | Path,
                  recordings_dir: str | Path | None, creds: dict[str, str], *,
                  push_replay_files: bool = True) -> dict[str, int]:
    """Push every queued match (stats first, then replay once its .sqrx has
    closed). Idempotent; leaves a marker in place on failure to retry later.

    A failed replay waits before it is tried again, longer after each failure,
    and is retired after _REPLAY_MAX_ATTEMPTS or at once on a permanent
    refusal (see _REPLAY_RETRY_FIRST_SEC). A replay that is waiting, or whose
    recording has not closed yet, counts as `pending`; one that was retired
    counts as `skipped`. Returns counts {sent, pending, errors, skipped}."""
    d = Path(backlog_dir)
    sent = pending = errors = skipped = 0
    if not d.is_dir():
        return {"sent": 0, "pending": 0, "errors": 0, "skipped": 0}
    for marker in sorted(d.glob("*.json")):
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        mid = state.get("match_id")
        if not mid:
            marker.unlink(missing_ok=True)
            continue
        try:
            if not state.get("stats_done"):
                bundle = build_match_bundle(db_path, mid)
                if bundle is None:            # match vanished from the DB
                    marker.unlink(missing_ok=True)
                    continue
                push_match(creds, bundle, seq=_next_seq(d))
                state["stats_done"] = True
                _atomic_json(marker, state)

            if push_replay_files and not state.get("replay_done"):
                if time.time() < float(state.get("replay_next_at") or 0):
                    pending += 1              # failed before; its wait is not over
                    continue
                sqrx = (_find_replay(recordings_dir, mid)
                        if recordings_dir else None)
                if sqrx is None:
                    if not _replay_never_coming(marker, state, mid):
                        pending += 1          # .sqrx not closed yet — retry next
                        continue
                    marker.unlink(missing_ok=True)
                    skipped += 1
                    continue
                try:
                    push_replay(creds, mid, _server_id(db_path, mid),
                                sqrx, seq=_next_seq(d))
                except PushError as exc:
                    if not _replay_failed(marker, state, mid, exc):
                        raise                 # counted below; waits, then retries
                    marker.unlink(missing_ok=True)
                    skipped += 1
                    continue
                state["replay_done"] = True
                _atomic_json(marker, state)

            marker.unlink(missing_ok=True)
            sent += 1
        except PushError as exc:
            errors += 1                        # central down/refused — keep marker
            log.warning("push failed for match %s: %s", mid, exc)
        except (EnvelopeError, OSError, sqlite3.Error) as exc:
            errors += 1
            log.warning("push failed for match %s: %r", mid, exc)
    return {"sent": sent, "pending": pending, "errors": errors,
            "skipped": skipped}


def _replay_retry_delay(attempts: int) -> float:
    """Seconds to wait after the `attempts`-th failure of a replay."""
    return float(min(_REPLAY_RETRY_MAX_SEC,
                     _REPLAY_RETRY_FIRST_SEC
                     * _REPLAY_RETRY_FACTOR ** max(0, attempts - 1)))


def _replay_failed(marker: Path, state: dict[str, Any], match_id: str,
                   exc: PushError) -> bool:
    """Record a failed replay push. True once the replay is retired.

    Otherwise the marker carries when to try again, and the flush leaves it
    alone until then: a central that is down or refusing sees each replay a
    handful of times over a day instead of every half minute."""
    attempts = int(state.get("replay_attempts") or 0) + 1
    state["replay_attempts"] = attempts
    if exc.permanent or attempts >= _REPLAY_MAX_ATTEMPTS:
        state["replay_done"] = True
        state["replay_skipped"] = str(exc)
        _atomic_json(marker, state)
        log.error("giving up on the replay for match %s after %d attempt(s): %s"
                  " - its stats are published, the replay is not",
                  match_id, attempts, exc)
        return True
    wait = _replay_retry_delay(attempts)
    state["replay_next_at"] = time.time() + wait
    _atomic_json(marker, state)
    log.warning("replay push failed for match %s (attempt %d/%d), next try in "
                "%.0f min: %s", match_id, attempts, _REPLAY_MAX_ATTEMPTS,
                wait / 60, exc)
    return False


def _replay_never_coming(marker: Path, state: dict[str, Any],
                         match_id: str) -> bool:
    """Whether to stop waiting for a recording that has not turned up.

    A match whose .sqrx was pruned, or never kept, leaves a marker nothing will
    ever satisfy, and it would read as `pending` on every flush for good. Timed
    from when the file was first seen missing, not from when the match ended:
    an agent that was off for two days comes back to recordings that are still
    there, and dating the wait from the match would drop them on the first
    flush after it woke up."""
    now = time.time()
    since = state.get("replay_missing_since")
    if not isinstance(since, (int, float)):
        state["replay_missing_since"] = now
        _atomic_json(marker, state)
        return False
    if now - since < _REPLAY_MISSING_MAX_SEC:
        return False
    log.error("no recording for match %s after %.0f h of looking - retiring it;"
              " its stats are published, the replay is not",
              match_id, (now - since) / 3600)
    return True


def _server_id(db_path: str | Path, match_id: str) -> str:
    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT server_id FROM matches WHERE match_id=?", (match_id,)
        ).fetchone()
        return str(row[0]) if row and row[0] else "squad"
    except sqlite3.OperationalError:
        return "squad"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------

def _find_replay(recordings_dir: str | Path, match_id: str) -> Path | None:
    """The finalized .sqrx for match_id, or None if still open / absent.

    Filenames carry only the last-8 of match_id; the full id + the inProgress
    flag live in the ``.meta.json`` sidecar, so we match on those.
    """
    for sqrx in Path(recordings_dir).glob("*.sqrx"):
        meta = sqrx.with_suffix(".meta.json")
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("matchId") == match_id and m.get("inProgress") is False:
            return sqrx
    return None


def _secret(creds: dict[str, str]) -> bytes:
    try:
        return bytes.fromhex(creds["SQREADER_AGENT_SECRET_HEX"])
    except (KeyError, ValueError) as exc:
        raise PushError("missing/invalid agent secret") from exc


def _push_base(creds: dict[str, str]) -> str:
    """Base URL for every outbound request — and the one place the build lock
    is enforced.

    All six agent→central calls (ingest/match, ingest/replay, agent/checkin,
    agent/offsets, agent/manifest, agent/download) route through here, so a
    locked build cannot be redirected by editing ``.env.agent`` or by a central
    that answers ``enroll`` with someone else's push_url.
    """
    base = creds.get("SQREADER_PUSH_URL") or creds.get("SQREADER_CENTRAL_URL")
    locked = build_config.locked_base()
    if locked:
        if base and not build_config.same_origin(base, locked):
            raise PushError(
                f"this build is locked to {locked}; refusing to send to {base}")
        # Keep the credential URL when it agrees (it may carry a path prefix);
        # fall back to the baked one when the creds carry no URL at all.
        return base.rstrip("/") if base else locked
    if not base:
        raise PushError("no push_url configured")
    return base.rstrip("/")


def _next_seq(backlog_dir: Path) -> int:
    """Monotonic advisory counter (AAD/freshness only — match_id is the real
    idempotency key). Persisted so a restart resumes it."""
    f = backlog_dir / ".push.seq"
    try:
        n = int(f.read_text(encoding="ascii").strip()) + 1
    except (OSError, ValueError):
        n = 1
    try:
        f.write_text(str(n), encoding="ascii")
    except OSError:
        pass
    return n


def _post_json(url: str, obj: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": _UA})
    raw = _send(req, timeout=timeout)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise PushError("central returned non-JSON") from exc
    return parsed if isinstance(parsed, dict) else {}


_RETRY_BACKOFFS: tuple[float, ...] = (1.0, 2.0, 4.0)


def _send(req: urllib.request.Request, *, timeout: float) -> bytes:
    """POST/GET with exponential-backoff retry on transient failure.

    Retries on network errors (URLError/OSError/TimeoutError) and 5xx
    server responses up to len(_RETRY_BACKOFFS) times, with the listed
    delays between attempts. A 4xx returns immediately — those are
    typically malformed requests (a client-side bug, not transient).

    Complements the backlog-layer marker retry in flush_backlog(): that
    layer catches anything we ultimately fail (marker stays, next
    flush cycle retries ~30 s later). This retry exists to shorten
    recovery from short-lived hiccups (a dropped packet, a proxy
    reboot) so the marker doesn't sit for the full flush interval.
    """
    max_retries = len(_RETRY_BACKOFFS)
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return resp.read()
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600 and attempt < max_retries:
                time.sleep(_RETRY_BACKOFFS[attempt])
                continue
            # 413 is a verdict on the payload, not a hiccup: the agent would
            # build the identical request again on every later try.
            raise PushError(f"central HTTP {exc.code}",
                            permanent=exc.code == 413) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if attempt < max_retries:
                time.sleep(_RETRY_BACKOFFS[attempt])
                continue
            raise PushError(f"central unreachable: {exc}") from exc
    # Defensive: the loop always exits via `return` or `raise`.
    raise PushError("send exhausted all retries")


def _atomic_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _safe_id(match_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in match_id)[:80]


def default_backlog_dir(db_path: str | Path) -> Path:
    """Push queue next to the stats DB by default."""
    return Path(db_path).parent / "push_queue"


def current_creds() -> dict[str, str] | None:
    """The enrolled credentials, or None (opt-in: absent → push disabled)."""
    creds = agent_creds.load()
    if creds and creds.get("SQREADER_AGENT_ID") and creds.get("SQREADER_AGENT_SECRET_HEX"):
        return creds
    return None


__all__ = [
    "PushError", "SCHEMA_MATCH", "SCHEMA_REPLAY", "SCHEMA_CHECKIN",
    "SCHEMA_OFFSETS_REQ", "SCHEMA_MANIFEST_REQ",
    "build_match_bundle", "enroll", "push_match", "push_replay", "checkin",
    "fetch_offsets", "fetch_manifest", "download_artifact", "enqueue",
    "flush_backlog", "default_backlog_dir", "current_creds",
]


"""Serve the viewer with a fake live round; no Squad server needed.

    .venv/bin/python scripts/live_dev_server.py                  # synthetic players
    .venv/bin/python scripts/live_dev_server.py --sqrx R         # replay a recording as live
    .venv/bin/python scripts/live_dev_server.py --round-sec 120  # a new round every 2 min
    .venv/bin/python scripts/live_dev_server.py --no-live        # the public build, to compare

Then open http://localhost:8090/, click Moderator-Login and log in with the
printed password. It serves frontend/dist, icons and sqmaps the way
`sqreader serve` does, and writes the round into a temporary .sqrx the way the
recorder does: a full frame every 2 s plus 4 Hz position frames. With
--round-sec the round ends after that many seconds and the next one starts
10 s later. SIGHUP exercises the revoke path: the password is re-read from
$SQREADER_CONFIG, which this script points at a temporary file holding
--password unless it is set already.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqreader.httpsrv import _TickBeat, serve_in_background   # noqa: E402
from sqreader.live import LiveMap                             # noqa: E402
from sqreader.recorder import RecordingState                  # noqa: E402
from sqreader.sqrx import SqrxReader, SqrxWriter              # noqa: E402

DEV_PASSWORD = "dev-password-for-the-live-map"
PLAYERS = 20
RADIUS = 150_000.0      # UE units; Gorodok spans about +-203 000
BETWEEN_ROUNDS_SEC = 10.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _positions(t: float) -> list[tuple[str, float, float]]:
    out = []
    for i in range(PLAYERS):
        a = t / 20.0 + i * math.tau / PLAYERS
        r = RADIUS * (0.3 + 0.035 * i)
        out.append((f"eos-{i:02d}", r * math.cos(a), r * math.sin(a)))
    return out


def full_frame(tick: int, t: float) -> dict:
    players = [{
        "name": f"Player{i:02d}", "eosId": eos, "playerId": i, "teamId": 1 + i % 2,
        "roleId": None, "score": 0, "ping": 30, "isBot": False, "clanTag": None,
        "squadStateAddr": None, "teamStateAddr": None, "stats": {"kills": 0, "deaths": 0},
        "soldier": {"addr": f"0x{i + 1:x}", "classShort": None, "health": 100.0,
                    "breathHoldStamina": None, "stance": "standing", "yaw": 0.0,
                    "attached": False, "position": {"x": x, "y": y, "z": 0.0}},
    } for i, (eos, x, y) in enumerate(_positions(t))]
    return {
        "timestamp": _now(), "server": "live-dev", "schemaVersion": "1", "tick": tick,
        "counts": {"playerStatesNonCDO": PLAYERS, "soldiersLive": PLAYERS,
                   "vehicleSeatsLive": 0, "totalUObjects": 0},
        "gameState": {"serverName": "live dev server", "mapName": "Gorodok",
                      "gameModeName": "RAAS", "matchState": "InProgress",
                      "matchId": "live-dev"},
        "teams": [{"id": 1, "factionId": "USA", "tickets": 300},
                  {"id": 2, "factionId": "RUS", "tickets": 300}],
        "squads": [], "players": players, "vehicles": [], "captureZones": [],
        "markers": [], "deployables": [], "vehicleSpawners": [], "rallyPoints": [],
        "projectiles": [], "damageEvents": [],
    }


def pos_frame(tick: int, t: float) -> dict:
    return {"t": "pos", "tick": tick, "timestamp": _now(), "vehicles": [],
            "players": [{"id": eos, "x": x, "y": y} for eos, x, y in _positions(t)]}


def synthetic_lines(stop: threading.Event) -> Iterator[tuple[str, bool]]:
    tick = 0
    while not stop.wait(0.25):
        tick += 1
        t = tick * 0.25
        if tick % 8 == 1:
            yield json.dumps(full_frame(tick, t)), True
        else:
            yield json.dumps(pos_frame(tick, t)), False


def recording_lines(stop: threading.Event, path: Path) -> Iterator[tuple[str, bool]]:
    """A .sqrx at its own pace (gaps capped at 2 s), once through."""
    last = None
    with SqrxReader(path) as reader:
        for line in reader:
            if stop.is_set():
                return
            frame = json.loads(line)
            ts = datetime.fromisoformat(frame["timestamp"]).timestamp()
            if last is not None:
                stop.wait(min(2.0, max(0.0, ts - last)))
            last = ts
            yield line, frame.get("t") != "pos"


def feed(live: LiveMap, stop: threading.Event, out_dir: Path, sqrx: Optional[Path],
         round_sec: float) -> None:
    """One .sqrx per round, written and announced the way recorder.py and
    cli.py do it."""
    box: dict = {"current": None}
    live.recording = lambda: box["current"]
    n = 0
    while not stop.is_set():
        n += 1
        path = out_dir / f"dev-round-{n}.sqrx"
        state = RecordingState(match_id=f"dev-{n}", writer=SqrxWriter(path, "live-dev"),
                               path=path, started_at=datetime.now(timezone.utc))
        box["current"] = state
        began = time.monotonic()
        for line, full in recording_lines(stop, sqrx) if sqrx else synthetic_lines(stop):
            state.writer.write_line(line)
            if full:
                state.last_snap_ts = json.loads(line)["timestamp"]
                state.first_snap_ts = state.first_snap_ts or state.last_snap_ts
            if round_sec and time.monotonic() - began >= round_sec:
                break
        box["current"] = None
        state.writer.close()
        print(f"round {n} over: {path}", file=sys.stderr)
        stop.wait(BETWEEN_ROUNDS_SEC)


def main() -> int:
    ap = argparse.ArgumentParser(description="Viewer with a fake live round.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--password", default=DEV_PASSWORD)
    ap.add_argument("--sqrx", type=Path, help="replay this recording as the live round")
    ap.add_argument("--round-sec", type=float, default=0.0,
                    help="end each round after this many seconds and start the next")
    ap.add_argument("--no-live", action="store_true", help="serve without the live map")
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s: %(message)s")

    live = None if args.no_live else LiveMap(args.password)
    srv = serve_in_background(args.host, args.port, _TickBeat(), live=live,
                              frontend_dir=ROOT / "frontend" / "dist",
                              icons_dir=ROOT / "icons", sqmaps_dir=ROOT / "sqmaps")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if live is not None:
        if not os.environ.get("SQREADER_CONFIG"):
            cfg = Path(tempfile.mkdtemp()) / "sqreader.config.json"
            cfg.write_text(json.dumps({"live_password": args.password}), encoding="utf-8")
            os.environ["SQREADER_CONFIG"] = str(cfg)
        signal.signal(signal.SIGHUP, live.on_sighup)
        threading.Thread(target=feed, daemon=True,
                         args=(live, stop, Path(tempfile.mkdtemp()), args.sqrx,
                               args.round_sec)).start()
        print(f"http://localhost:{args.port}/  password: {args.password}  "
              f"(pid {os.getpid()})", file=sys.stderr)
    else:
        print(f"http://localhost:{args.port}/  (no live map)", file=sys.stderr)
    stop.wait()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

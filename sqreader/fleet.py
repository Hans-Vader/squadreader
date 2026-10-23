"""Fleet telemetry — what each enrolled agent tells central about itself.

Phase 0 of the remote-update system. On a periodic sealed check-in the agent
reports its version, the Squad BUILD it is attached to, and its reader HEALTH
(the `doctor` drift signal from `health.run_doctor`). Central records this so an
operator can answer, in one glance after a Squad patch, "which of my enrolled
servers just broke, and what are they running?" — the prerequisite for targeting
an offset pack (Phase 1) or a code release (Phase 2) at the servers that need it.

This module only ASSEMBLES the telemetry dict and keeps the restart counter;
`ingest_client.checkin()` seals + sends it over the same per-agent envelope as a
match push. Same opt-in boundary as push (only enrolled, push-enabled boxes
report), and best-effort — a failed check-in never touches the live reader.
"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from . import __version__, health, squad_build

SCHEMA_CHECKIN = "sqr-checkin-1"
# 5-minute cadence: frequent enough to see a fleet-wide breakage within minutes
# of a Squad patch, rare enough to be free. Covers IDLE servers too — a box that
# never finishes a match still reports its health on this timer.
CHECKIN_INTERVAL_SEC = 300.0
# Live-match cadence. The FULL check-in walks class layouts (`run_doctor`) and
# hashes the Squad build, so it stays on the 5-minute timer; the live block is
# free — it is read straight off the snapshot this tick already built — and a
# match list that claims "playing now" is only honest if it is minutes fresh.
# Every LIVE_INTERVAL_SEC we send a LIGHT check-in (no health), and every fifth
# one is the full one, so the doctor's cost per hour does not change.
LIVE_INTERVAL_SEC = 60.0


def bump_restarts(state_dir: str | Path) -> int:
    """Increment + return a persistent boot counter. Each serve start is a
    restart under systemd `Restart=always`, so a climbing count is the signal of
    a crash-looping reader. Best-effort; never raises."""
    p = Path(state_dir) / ".sqr_restarts"
    try:
        n = int(p.read_text(encoding="ascii").strip()) + 1
    except (OSError, ValueError):
        n = 1
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(n), encoding="ascii")
    except OSError:
        pass
    return n


def live_match(snap: dict[str, Any] | None, *,
               recording: bool = False) -> dict[str, Any] | None:
    """The in-progress match as central should show it, or None when nothing is
    being played right now.

    Read straight off the snapshot the reader already built this tick, so it
    costs nothing beyond the dict walk — that is what lets the live block ride a
    1-minute timer while the doctor stays on 5. Deliberately conservative: only
    a genuine `InProgress` with a real match id counts, because central renders
    this as "playing now" and a stale/torn read there is worse than no row.
    """
    gs = (snap or {}).get("gameState") or {}
    match_id = gs.get("matchId")
    if not isinstance(match_id, str) or not match_id:
        return None
    if gs.get("matchState") != "InProgress":
        return None
    by_id = {t.get("id"): t for t in ((snap or {}).get("teams") or [])
             if isinstance(t, dict)}
    t1 = by_id.get(1) or {}
    t2 = by_id.get(2) or {}
    players = (snap or {}).get("players")
    elapsed = gs.get("elapsedSec")
    return {
        "match_id": match_id,
        # gameState carries ONE name and it is already the layer ("Fallujah AAS
        # v1"), so both fields are fed from it — central's match rows have the
        # same value in map_name and layer_name for exactly this reason.
        "map_name": gs.get("mapName"),
        "layer_name": gs.get("mapName"),
        "game_mode": gs.get("gameModeName") or gs.get("gameModeId"),
        "elapsed_sec": int(elapsed) if isinstance(elapsed, (int, float)) else None,
        "players": len(players) if isinstance(players, list) else None,
        "max_players": gs.get("maxPlayers"),
        "team1_faction": t1.get("factionId"),
        "team2_faction": t2.get("factionId"),
        "team1_tickets": t1.get("tickets"),
        "team2_tickets": t2.get("tickets"),
        # Whether a .sqrx is open for this match — so the UI can promise a
        # replay afterwards only when one is actually being written.
        "recording": bool(recording),
    }


def gather_offline(*, reason: str, build_sha: str | None, restarts: int,
                   uptime_sec: float, channel: str) -> dict[str, Any]:
    """Telemetry for an agent that could NOT attach to the game.

    This exists because the check-in used to live behind the attach: a reader
    that could not open the process died before saying so, systemd restarted
    it, and it died again — silently, forever, with nothing reaching central.
    That is the exact shape of a Squad patch breaking the fleet, and it took out
    the one channel that could have fixed it.

    `health="down"` is deliberately a fourth value beside ok/drift/unknown. An
    agent that cannot see the game is not the same as one whose offsets have
    drifted, and treating them alike would hide a bricked box among the noisy
    ones.
    """
    return {
        "schema": SCHEMA_CHECKIN,
        "agent_version": __version__,
        "platform": platform.system(),
        "squad_build": build_sha,
        "engine": None,
        "health": "down",
        "drift": [reason[:200]],
        "restarts": int(restarts),
        "uptime_sec": int(uptime_sec),
        "channel": channel,
    }


def gather(pm: Any, arr: Any, alloc: Any, *, build_sha: str | None,
           restarts: int, uptime_sec: float, channel: str,
           live: dict[str, Any] | None = None,
           with_health: bool = True) -> dict[str, Any]:
    """Assemble the check-in telemetry over the reader's own open handles.

    `health.run_doctor` walks the live class layouts (cheap) and classifies the
    reader as ok / drift / unknown; a `drift` server is one a Squad patch broke.
    The drift list is capped so a totally-broken reader can't send a huge body.

    `with_health=False` is the LIGHT check-in that carries only the live block.
    It omits the health keys entirely rather than sending them as null, because
    central's `report_state` writes only the fields that are present — so a
    light report can never clobber the last full one.
    """
    out: dict[str, Any] = {
        "schema": SCHEMA_CHECKIN,
        "agent_version": __version__,
        "platform": platform.system(),
        "restarts": int(restarts),
        "uptime_sec": int(uptime_sec),
        "channel": channel,
        "live": live,
    }
    if with_health:
        doc = health.run_doctor(pm, arr, alloc)
        out["squad_build"] = build_sha
        out["engine"] = squad_build.engine_version(pm, arr, alloc)
        out["health"] = doc.get("state", "unknown")     # ok | drift | unknown
        out["drift"] = (doc.get("drift") or [])[:20]
    return out

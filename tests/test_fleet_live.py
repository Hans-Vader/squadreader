"""The match a server is playing right now, as the fleet check-in reports it.

Only altai's private build sent this, so on squadreader.com/servers every
other server read "Last match ..." while it was mid-game. Central shows the
block as "Playing now", where a torn read is worse than no row at all.
"""
from __future__ import annotations

from sqreader import fleet


def _snap(**gs):
    base = {"matchId": "M1", "matchState": "InProgress", "mapName": "Narva RAAS v1",
            "gameModeName": "RAAS", "elapsedSec": 754.6, "maxPlayers": 100}
    base.update(gs)
    return {"gameState": base,
            "teams": [{"id": 1, "factionId": "USA_LO_CombinedArms", "tickets": 212},
                      {"id": 2, "factionId": "RGF_LO_CombinedArms", "tickets": 187}],
            "players": [{"name": f"p{i}"} for i in range(87)]}


def test_a_match_in_progress_is_reported():
    live = fleet.live_match(_snap(), recording=True)
    assert live == {
        "match_id": "M1", "map_name": "Narva RAAS v1", "layer_name": "Narva RAAS v1",
        "game_mode": "RAAS", "elapsed_sec": 754, "players": 87, "max_players": 100,
        "team1_faction": "USA_LO_CombinedArms", "team2_faction": "RGF_LO_CombinedArms",
        "team1_tickets": 212, "team2_tickets": 187, "recording": True,
    }


def test_nothing_is_reported_unless_a_real_match_is_in_progress():
    assert fleet.live_match(None) is None
    assert fleet.live_match({}) is None
    assert fleet.live_match(_snap(matchId="")) is None
    assert fleet.live_match(_snap(matchId=None)) is None
    for state in ("WaitingToStart", "WaitingPostMatch", "EndState", None):
        assert fleet.live_match(_snap(matchState=state)) is None


def test_the_light_check_in_carries_no_health_keys():
    """Central writes only the fields present, so a light report must never
    send health as null over the last full one. It also must not walk the
    class layouts: it goes out every minute."""
    out = fleet.gather(None, None, None, build_sha="abc", restarts=2, uptime_sec=61.9,
                       channel="stable", live={"match_id": "M1"}, with_health=False)
    assert out["live"] == {"match_id": "M1"} and out["uptime_sec"] == 61
    for key in ("health", "drift", "squad_build", "engine"):
        assert key not in out


def test_the_full_check_in_still_reports_health(monkeypatch):
    monkeypatch.setattr(fleet.health, "run_doctor",
                        lambda pm, arr, alloc: {"state": "ok", "drift": []})
    monkeypatch.setattr(fleet.squad_build, "engine_version", lambda pm, arr, alloc: "5.1")
    out = fleet.gather(None, None, None, build_sha="abc", restarts=0, uptime_sec=0,
                       channel="stable", live=None)
    assert out["health"] == "ok" and out["squad_build"] == "abc" and out["engine"] == "5.1"
    assert "live" in out and out["live"] is None

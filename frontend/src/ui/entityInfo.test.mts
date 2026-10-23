// Who placed a marker. After a Squad update renamed FireTeamIndex to
// FireteamIndex, the reader stopped filling fireTeamIndex and every SL marker
// read "offline / unresolved". Framework-free, as the tests beside it are.
import { findPlacer } from "./entityInfo.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}

const P = (name: string, addr: string, squad: number, idx: number | null, pos: number | null, team = 1): any =>
  ({ name, _addr: addr, teamId: team, squadId: squad,
     stats: { fireTeamIndex: idx, fireTeamPosition: pos } });
const marker = (squad: number, ft: number | null, team = 1): any =>
  ({ id: "m", team, squad, fireTeamId: ft, type: "BP_MapMarker_Option_FOB" });

// --- the reported case: no fire-team index anywhere, the squad names its leader
{
  const players = [P("Omer", "0xa1", 2, null, 0), P("Rifle", "0xa2", 2, null, 1),
                   P("Other", "0xb1", 3, null, 0)];
  const squads: any[] = [{ id: 2, teamId: 1, leaderStateAddr: "0xa1" },
                         { id: 3, teamId: 1, leaderStateAddr: "0xb1" }];
  ok(findPlacer(marker(2, -1), players, squads)?.name === "Omer",
     "SL marker resolves through the squad's leader");
  ok(findPlacer(marker(2, null), players, squads)?.name === "Omer",
     "a marker with no fireteam is the SL's too");
  ok(findPlacer(marker(2, -1), players, [])?.name == null,
     "without the leader and without the index it stays unresolved, not guessed");
  ok(findPlacer(marker(2, -1, 2), players, squads) == null,
     "the other team's squad 2 is a different squad");
}

// --- the index still works when present (and for fireteam leaders) -------------
{
  const players = [P("SL", "0x1", 4, 0, 0), P("FTL1", "0x2", 4, 1, 0), P("Mate", "0x3", 4, 1, 1)];
  ok(findPlacer(marker(4, -1), players)?.name === "SL", "SL by the stats encoding");
  ok(findPlacer(marker(4, 1), players)?.name === "FTL1", "Bravo FTL by the stats encoding");
  ok(findPlacer(marker(4, 2), players) == null, "no Charlie FTL, none found");
}

// --- a leader pointer to nobody on the server falls back to the index ----------
{
  const players = [P("SL", "0x1", 5, 0, 0)];
  const squads: any[] = [{ id: 5, teamId: 1, leaderStateAddr: "0xdead" }];
  ok(findPlacer(marker(5, -1), players, squads)?.name === "SL",
     "a stale leader pointer does not hide the index match");
}

console.log(`entityInfo: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

// Rally burns: who began a siege, what counts as burned and what does not, and
// that chunked input gives the same records as one pass.

import {
  BURNED_SHOW_MS, burnedAt, burnersAt, createRallyState, extendRallies,
  publishRallies, type RallyRecord,
} from "./rallyBurns.ts";
import type { Snapshot } from "./types.ts";

let passed = 0, failed = 0;
function ok(cond: unknown, msg: string) {
  if (cond) passed++;
  else { failed++; console.error("FAIL:", msg); }
}

const T0 = Date.parse("2026-10-10T19:00:00Z");

interface R { id: string; team?: number; sq?: string; sieged?: boolean }
interface P { name: string; team: number; x: number; y?: number; health?: number;
              life?: "incapacitated"; cls?: string }
interface F { rallies?: R[]; players?: P[]; state?: string }

// Rallies sit at the origin unless they say otherwise; players are placed by
// their distance from it in metres, along +x.
function frame(sec: number, f: F = {}): Snapshot {
  return {
    timestamp: new Date(T0 + sec * 1000).toISOString(),
    gameState: { matchState: f.state ?? "InProgress" },
    rallyPoints: (f.rallies ?? []).map((r) => ({
      id: r.id, team: r.team ?? 1, squadTeamId: r.team ?? 1, squadId: 3,
      squadName: null, squadStateAddr: r.sq ?? `sq-${r.id}`, sieged: !!r.sieged,
      spawningEnabled: true, spawnsRemaining: -1, classShort: null, actorName: null,
      position: { x: 0, y: 0, z: 0 },
    })),
    players: (f.players ?? []).map((p) => ({
      eosId: `eos-${p.name}`, playerId: null, name: p.name, clanTag: null,
      teamId: p.team,
      soldier: {
        addr: `a-${p.name}`, classShort: p.cls ?? "BP_Soldier_C",
        health: p.health ?? 100, lifeState: p.life ?? null, attached: false,
        position: { x: p.x * 100, y: (p.y ?? 0) * 100, z: 0 },
      },
    })),
  } as unknown as Snapshot;
}

function run(frames: Snapshot[], chunk = frames.length): RallyRecord[] {
  const st = createRallyState();
  const grown: Snapshot[] = [];
  for (let i = 0; i < frames.length; i += chunk) {
    grown.push(...frames.slice(i, i + chunk));
    extendRallies(st, grown);
  }
  return publishRallies(st);
}

const rec = (rs: RallyRecord[], id: string) => rs.find((r) => r.id === id)!;

// --- a burn: the enemy who walked in first is named, later arrivals follow --
{
  const fr = [
    frame(0, { rallies: [{ id: "r1" }], players: [{ name: "far", team: 2, x: 80 }] }),
    // 31 m: outside, and the game agrees (not sieged).
    frame(1, { rallies: [{ id: "r1" }], players: [{ name: "far", team: 2, x: 31 }] }),
    frame(2, { rallies: [{ id: "r1", sieged: true }],
               players: [{ name: "far", team: 2, x: 27 }, { name: "mate", team: 1, x: 2 }] }),
    frame(3, { rallies: [{ id: "r1", sieged: true }],
               players: [{ name: "far", team: 2, x: 20 }, { name: "late", team: 2, x: 0, y: 12 }] }),
    frame(4, { players: [{ name: "far", team: 2, x: 15 }] }),
  ];
  const r = rec(run(fr), "r1");
  ok(r.burnedMs === T0 + 4000, "burned on the first frame it is missing");
  ok(r.sieges.length === 1 && r.sieges[0]!.startMs === T0 + 2000, "siege starts where the game raised the flag");
  const b = r.sieges[0]!.burners;
  ok(b.length === 2 && b[0]!.name === "far" && b[1]!.name === "late",
     `burners in arrival order, friendlies excluded: ${b.map((x) => x.name)}`);
  ok(b[0]!.distM === 27 && b[0]!.at.x === 2700, "burner keeps where they stood on entry");
  ok(burnersAt(r, T0 + 2500).map((x) => x.name).join() === "far", "burnersAt hides who had not arrived yet");
  ok(burnersAt(r, T0 + 9000).length === 2, "after the burn, the whole siege is named");
  ok(burnedAt([r], T0 + 4000).length === 1 && burnedAt([r], T0 + 3999).length === 0
     && burnedAt([r], T0 + 4000 + BURNED_SHOW_MS).length === 0, "shown for BURNED_SHOW_MS after it went");

  // Same frames, any chunking: same answer.
  for (const c of [1, 2, 3]) {
    ok(JSON.stringify(run(fr, c)) === JSON.stringify(run(fr)), `chunk size ${c} matches one pass`);
  }
}

// --- gone without a siege: not burned ---------------------------------------
{
  const r = rec(run([
    frame(0, { rallies: [{ id: "r2" }], players: [{ name: "e", team: 2, x: 10 }] }),
    frame(1, { players: [{ name: "e", team: 2, x: 10 }] }),
  ]), "r2");
  ok(r.burnedMs === null, "a rally that was never sieged is not burned, enemy or not");
}

// --- replaced by its own squad while under siege: not burned ----------------
{
  const r = rec(run([
    frame(0, { rallies: [{ id: "old", sq: "S", sieged: true }], players: [{ name: "e", team: 2, x: 20 }] }),
    frame(1, { rallies: [{ id: "new", sq: "S" }] }),
  ]), "old");
  ok(r.burnedMs === null, "a new rally of the same squad in the same frame is a replacement");
}

// --- the round ended: nothing burns -----------------------------------------
{
  const r = rec(run([
    frame(0, { rallies: [{ id: "r3", sieged: true }], players: [{ name: "e", team: 2, x: 20 }] }),
    frame(1, { state: "WaitingPostMatch" }),
  ]), "r3");
  ok(r.burnedMs === null, "a rally that vanishes as the round ends was not burned");
}

// --- a dropped read: it comes back -------------------------------------------
{
  const r = rec(run([
    frame(0, { rallies: [{ id: "r4", sieged: true }], players: [{ name: "e", team: 2, x: 20 }] }),
    frame(1, {}),
    frame(2, { rallies: [{ id: "r4", sieged: true }] }),
  ]), "r4");
  ok(r.burnedMs === null, "a rally that reappears was never burned");
}

// --- who does not count -------------------------------------------------------
{
  const r = rec(run([
    frame(0, { rallies: [{ id: "r5", sieged: true }], players: [
      { name: "downed", team: 2, x: 5, health: 0 },
      { name: "incap", team: 2, x: 5, life: "incapacitated" },
      { name: "cam", team: 2, x: 5, cls: "BP_DeveloperAdminCam_C" },
      { name: "friend", team: 1, x: 5 },
      { name: "up", team: 2, x: 29 },
    ] }),
  ]), "r5");
  ok(r.sieges[0]!.burners.map((b) => b.name).join() === "up",
     `downed, incapacitated, admin cam and friendlies are not burners: ${r.sieges[0]!.burners.map((b) => b.name)}`);
}

// --- two sieges: the burn is credited to the last one ------------------------
{
  const r = rec(run([
    frame(0, { rallies: [{ id: "r6", sieged: true }], players: [{ name: "first", team: 2, x: 25 }] }),
    frame(1, { rallies: [{ id: "r6" }], players: [{ name: "first", team: 2, x: 60 }] }),
    frame(2, { rallies: [{ id: "r6", sieged: true }], players: [{ name: "second", team: 2, x: 28 }] }),
    frame(3, {}),
  ]), "r6");
  ok(r.sieges.length === 2 && r.sieges[0]!.endMs === T0 + 1000, "a lifted siege is closed");
  ok(burnersAt(r, T0 + 500).map((b) => b.name).join() === "first", "the first siege, while it lasted");
  ok(burnersAt(r, T0 + 1500).length === 0, "no siege in between");
  ok(burnersAt(r, T0 + 3000).map((b) => b.name).join() === "second", "the burn belongs to the last siege");
}

console.log(`rallyBurns: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

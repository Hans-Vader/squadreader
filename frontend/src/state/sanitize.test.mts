// node test: the b0db7147 first-frame row and its neighbours.
import assert from "node:assert/strict";
import { sanitizeSnapshot, isIdent, plausibleTeam } from "./sanitize";
import { passengersOf } from "../canvas/passengers";
import type { Snapshot } from "./types";

const GARBAGE = "\u656a\u7463\u7250\u706f\u7265\u7974\u0304\u614e\u656d\u7250";

const snap = {
  players: [
    { name: "MaG", eosId: "e1", teamId: 1, roleId: "USA_Rifleman_01",
      soldier: { attached: true, position: { x: 10, y: 10, z: 0 } } },
    { name: "\u7038\ub1ab", eosId: "", teamId: 21159352, roleId: GARBAGE,
      soldier: { attached: true, position: { x: 0, y: 0, z: 0 } } },
    { name: "Joining", eosId: "e2", teamId: 0, roleId: GARBAGE, soldier: null },
  ],
  vehicleSpawners: [{ vehicleClass: "BP_LAV25_C" }, { vehicleClass: GARBAGE }],
} as unknown as Snapshot;

sanitizeSnapshot(snap);
assert.deepEqual(snap.players.map((p) => p.name), ["MaG", "Joining"]);
assert.equal(snap.players[1]!.roleId, null);
assert.equal(snap.players[0]!.roleId, "USA_Rifleman_01");
assert.deepEqual(snap.vehicleSpawners.map((s) => s.vehicleClass), ["BP_LAV25_C", null]);

assert.ok(isIdent("BP_ZU23_Emplacement_C") && !isIdent(GARBAGE) && !isIdent("x".repeat(129)));
assert.ok(plausibleTeam(2) && plausibleTeam(-1) && plausibleTeam(null) && !plausibleTeam(3));

// An emplacement base never placed in the world: no position, so not drawn.
const ghostZu = { players: [], vehicles: [
  { id: "z", classShort: "BP_Emplaced_ZU23-2_Antiaircannon_Base_C", position: { x: 0, y: 0, z: 0 } },
  { id: "t", classShort: "BP_LAV25_C", position: { x: 0, y: 5, z: 0 } }] } as unknown as Snapshot;
sanitizeSnapshot(ghostZu);
assert.equal(ghostZu.vehicles[0]!.position, null);
assert.deepEqual(ghostZu.vehicles[1]!.position, { x: 0, y: 5, z: 0 });

// A soldier "attached" at exactly the origin is not seated in a vehicle there.
const atOrigin = {
  players: [{ name: "ghost", teamId: 1,
              soldier: { attached: true, position: { x: 0, y: 0, z: 0 } } }],
} as unknown as Snapshot;
const zu = { position: { x: 120, y: -80, z: 0 } } as never;
assert.equal(passengersOf(atOrigin, zu).length, 0);

// The same frame's markers and projectiles, and a template at the origin.
const mk = {
  players: [],
  markers: [
    { id: "a", type: "BP_MapMarker_Action_MoveSL", squad: 3, position: { x: 5, y: 6, z: 0 } },
    { id: "b", type: "None", squad: 5017313, position: { x: -21.4, y: -21.5, z: -80 } },
    { id: "c", type: "BP_MapMarker_EnemyDirector", squad: 911659, position: { x: 1, y: 1, z: 0 } },
  ],
  projectiles: [{ id: "p1", classShort: "BP_Projectile_762_C" }, { id: "p2", classShort: "None" }],
  deployables: [{ id: "d", classShort: "BP_Ammocrate_C", position: { x: 0, y: 0, z: 0 } }],
  vehicleSpawners: [{ id: "s", vehicleClass: null, position: { x: 0, y: 0, z: 6.9e-310 } }],
} as unknown as Snapshot;
sanitizeSnapshot(mk);
assert.deepEqual(mk.markers.map((m) => m.id), ["a"]);
assert.deepEqual(mk.projectiles.map((p) => p.id), ["p1"]);
assert.equal(mk.deployables[0]!.position, null);
assert.equal(mk.vehicleSpawners[0]!.position, null);

console.log("sanitize: ok");

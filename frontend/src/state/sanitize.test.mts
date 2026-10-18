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

console.log("sanitize: ok");

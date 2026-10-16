// Standalone unit test for the per-team objective head count. Run by
// scripts/run-tests.mjs (every *.test.mts under src/).
import { capOccupancy, countsOnPoint, insideCapGeometry } from "./capOccupancy.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) { ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`); }

const A = { x: 1000, y: 2000 };

// sphere: inside / on the edge / outside, offset by dx,dy
{
  const g = [{ type: "sphere" as const, dx: 100, dy: 0, radius: 500 }];
  ok(insideCapGeometry(1100, 2000, A, g), "sphere centre");
  ok(insideCapGeometry(1600, 2000, A, g), "sphere edge");
  ok(!insideCapGeometry(1601, 2000, A, g), "just past the edge");
  ok(!insideCapGeometry(1000 + 100 + 400, 2000 + 400, A, g), "corner of the bounding box is outside");
}

// box: half-extents, and rotation the way drawCapGeometry turns it
{
  const box = { type: "box" as const, dx: 0, dy: 0, extX: 1000, extY: 100, rot: 0 };
  ok(insideCapGeometry(A.x + 900, A.y + 50, A, [box]), "unrotated box, long side");
  ok(!insideCapGeometry(A.x + 50, A.y + 900, A, [box]), "unrotated box, outside the short side");
  const turned = { ...box, rot: 90 };
  // rotated a quarter turn the long side lies along world Y
  ok(insideCapGeometry(A.x + 50, A.y + 900, A, [turned]), "90° box, along Y");
  ok(!insideCapGeometry(A.x + 900, A.y + 50, A, [turned]), "90° box, no longer along X");
  // R(rot)·(lx, ly): local (+900, 0) at 90° is world (0, +900), not (0, -900)
  ok(insideCapGeometry(A.x, A.y + 900, A, [turned]) && insideCapGeometry(A.x, A.y - 900, A, [turned]),
     "symmetric box covers both ends");
  const diag = { ...box, rot: 45 };
  const d = 700 / Math.SQRT2;
  ok(insideCapGeometry(A.x + d, A.y + d, A, [diag]), "45° box along the +x+y diagonal");
  ok(!insideCapGeometry(A.x + d, A.y - d, A, [diag]), "45° box, not along the other diagonal");
}

// capsule: half-length = length - radius along local Y, rounded ends
{
  const cap = { type: "capsule" as const, dx: 0, dy: 0, radius: 200, length: 1000, rot: 0 };
  ok(insideCapGeometry(A.x, A.y + 800, A, [cap]), "segment end");
  ok(insideCapGeometry(A.x, A.y + 1000, A, [cap]), "tip of the rounded end");
  ok(!insideCapGeometry(A.x, A.y + 1001, A, [cap]), "past the tip");
  ok(!insideCapGeometry(A.x + 150, A.y + 990, A, [cap]), "outside the rounding near the tip");
  ok(insideCapGeometry(A.x + 199, A.y, A, [cap]), "side, middle");
}

// several shapes: any one of them counts
{
  const g = [
    { type: "sphere" as const, dx: 0, dy: 0, radius: 100 },
    { type: "sphere" as const, dx: 5000, dy: 0, radius: 100 },
  ];
  ok(insideCapGeometry(A.x + 5000, A.y, A, g), "second shape");
  ok(!insideCapGeometry(A.x + 2500, A.y, A, g), "between the shapes");
}

// who counts: placed, alive, up, on a team
function P(team: number | null, x: number, y: number, o: any = {}): any {
  return { name: "p", teamId: team,
           soldier: { position: { x, y, z: 0 }, health: 100, stale: false, ...o } };
}
{
  ok(countsOnPoint(P(1, 0, 0)), "alive, team 1");
  ok(!countsOnPoint(P(1, 0, 0, { lifeState: "incapacitated" })), "downed does not count");
  ok(countsOnPoint(P(2, 0, 0, { lifeState: "bleeding" })), "bleeding but up counts");
  ok(!countsOnPoint(P(1, 0, 0, { health: 0 })), "no life state, no health: does not count");
  ok(!countsOnPoint(P(1, 0, 0, { stale: true })), "stale soldier");
  ok(!countsOnPoint(P(0, 0, 0)), "not on a team yet");
  ok(!countsOnPoint({ name: "p", teamId: 1, soldier: null } as any), "no soldier (dead, spawning)");
  ok(!countsOnPoint(P(1, 0, 0, { position: null })), "no position");
}

// the count
{
  const g = [{ type: "sphere" as const, dx: 0, dy: 0, radius: 1000 }];
  const players = [
    P(1, A.x, A.y), P(1, A.x + 500, A.y), P(1, A.x, A.y, { lifeState: "incapacitated" }),
    P(2, A.x - 900, A.y), P(2, A.x + 5000, A.y), P(null, A.x, A.y),
  ];
  const c = capOccupancy(players, A, g);
  eq(c.t1, 2, "two living team 1 players inside");
  eq(c.t2, 1, "one team 2 player inside, one outside");
  const none = capOccupancy(players, A, []);
  eq(none.t1 + none.t2, 0, "no geometry, no count");
}

console.log(`\ncapOccupancy: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

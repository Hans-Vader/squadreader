// Who is standing on an objective, per team.
//
// The game does not keep this anywhere the reader can see. Probed live on
// altai (Al Basrah RAAS v2, 2026-10-11): SQCaptureZoneComponent reflects only
// the owner, the capture state, a net PlayerAdvantage and the capture rules
// (CurrentCaptureZoneStats = MinSoldiersToCaptureHeldPoint 3, ...); the zone
// actor, its state component and that component's DataObject (null) carry no
// soldier list or count either. The overlap bookkeeping lives in native code.
//
// So the count is taken the way a viewer would: the players whose position is
// inside the zone's shape as the map draws it (static SquadCalc geometry), and
// only the ones the game would count — alive, not downed. A zone with no
// static geometry gets no count: its ring on the map is a 75 m guess, and a
// number derived from a guess would read as a fact.
//
// The test is flat (x, y). Zone volumes are 3D, so a helicopter flying over a
// flag counts here and not in the game; on foot the two agree.

import type { CapGeometry, Player } from "../state/types";

/** Is world point (px, py) inside the zone whose shapes hang off `anchor`?
 *  Rotations follow drawCapGeometry: `rot` degrees, applied as a positive
 *  canvas rotation, and world maps to screen with no flip — so a local point
 *  (lx, ly) sits at world offset R(rot)·(lx, ly), and the inverse brings a
 *  world offset back into the shape's frame. */
export function insideCapGeometry(px: number, py: number,
                                  anchor: { x: number; y: number },
                                  geometry: CapGeometry[]): boolean {
  for (const g of geometry) {
    const wx = px - (anchor.x + g.dx);
    const wy = py - (anchor.y + g.dy);
    if (g.type === "sphere") {
      if (wx * wx + wy * wy <= g.radius * g.radius) return true;
      continue;
    }
    const a = ((g.rot || 0) * Math.PI) / 180;
    const c = Math.cos(a), s = Math.sin(a);
    const lx = c * wx + s * wy;
    const ly = -s * wx + c * wy;
    if (g.type === "box") {
      if (Math.abs(lx) <= g.extX && Math.abs(ly) <= g.extY) return true;
    } else {
      // capsule along local Y, half-length (length - radius) — as drawn.
      const half = Math.max(0, g.length - g.radius);
      const cy = Math.max(-half, Math.min(half, ly));
      const dy = ly - cy;
      if (lx * lx + dy * dy <= g.radius * g.radius) return true;
    }
  }
  return false;
}

/** Would the game count this player toward a capture: placed, alive, up. */
export function countsOnPoint(p: Player): boolean {
  const s = p.soldier;
  if (!s || s.stale || !s.position) return false;
  if (p.teamId !== 1 && p.teamId !== 2) return false;
  if (s.lifeState != null) return s.lifeState !== "incapacitated";
  return (s.health ?? 0) > 0;
}

export interface CapOccupancy { t1: number; t2: number }

export function capOccupancy(players: Player[], anchor: { x: number; y: number },
                             geometry: CapGeometry[]): CapOccupancy {
  const out = { t1: 0, t2: 0 };
  if (!geometry.length) return out;
  for (const p of players) {
    if (!countsOnPoint(p)) continue;
    const pos = p.soldier!.position!;
    if (!insideCapGeometry(pos.x, pos.y, anchor, geometry)) continue;
    if (p.teamId === 1) out.t1++; else out.t2++;
  }
  return out;
}

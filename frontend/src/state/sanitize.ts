// Rows a recording carries that never described anything in the match.
//
// On a map change the reader can still hold an address whose memory is no
// longer what it was. Read as a player state it gives a nonsense row: an empty
// id, a name of stray characters, a team number in the millions, a role id
// hundreds of CJK-looking characters long (a garbage name index decoding plain
// ASCII as UTF-16), and a soldier "attached" at the world origin, which
// passengersOf() then seated in whatever vehicle stood there. b0db7147's
// first frame drew one as the passenger of an emplaced ZU-23. The reader no
// longer writes these; this keeps every recording made before that clean.

import type { Snapshot } from "./types";

/** A role id or class name: letters, digits, "_", "-", "." and nothing else. */
const IDENT = /^[A-Za-z0-9_.\-]{1,128}$/;

export function isIdent(s: unknown): s is string {
  return typeof s === "string" && IDENT.test(s);
}

/** A class or type name that names something ("None" is the empty FName). */
function named(s: unknown): boolean {
  return isIdent(s) && s !== "None";
}

/** A real player is in team 1 or 2, or 0 / -1 while still joining. */
export function plausibleTeam(team: unknown): boolean {
  return team == null || (typeof team === "number" && team >= -1 && team <= 2);
}

/** x and y exactly 0: a transform nobody set. z is ignored, since a garbage
 *  one can hold a denormal there (b0db7147's spawner: z = 6.9e-310). */
function atOrigin(p: { x: number; y: number; z?: number | null } | null | undefined): boolean {
  return !!p && p.x === 0 && p.y === 0;
}

function plausibleMarker(m: { type?: unknown; squad?: unknown }): boolean {
  return named(m.type)
    && !(typeof m.squad === "number" && (m.squad < 0 || m.squad > 100));
}

/** Drop impossible player rows and blank garbage identifiers, in place.
 *  Returns the snapshot for chaining. Allocates only when something is wrong,
 *  which is a frame or two per recording at most. */
export function sanitizeSnapshot(snap: Snapshot): Snapshot {
  const players = snap.players;
  if (players && players.some((p) => !plausibleTeam(p.teamId))) {
    snap.players = players.filter((p) => plausibleTeam(p.teamId));
  }
  for (const p of snap.players ?? []) {
    if (p.roleId != null && !isIdent(p.roleId)) p.roleId = null;
  }
  // x and y exactly 0 is a transform nobody set: an emplacement base sat there
  // for the whole of b0db7147, drawn in the middle of Fallujah.
  for (const v of snap.vehicles ?? []) {
    if (atOrigin(v.position)) v.position = null;
  }
  for (const p of snap.players ?? []) {
    if (p.soldier && atOrigin(p.soldier.position)) p.soldier.position = null;
  }
  for (const sp of snap.vehicleSpawners ?? []) {
    if (sp.vehicleClass != null && !isIdent(sp.vehicleClass)) sp.vehicleClass = null;
  }
  // The same frame's markers were all of type "None" with squads such as
  // 5017313, and its projectiles of class "None". Over two full recordings
  // (137k markers, 26k projectiles) every real marker has a BP_MapMarker_*
  // type and every real projectile a class.
  const markers = snap.markers;
  if (markers && markers.some((m) => !plausibleMarker(m))) {
    snap.markers = markers.filter(plausibleMarker);
  }
  const shots = snap.projectiles;
  if (shots && shots.some((p) => !named(p.classShort))) {
    snap.projectiles = shots.filter((p) => named(p.classShort));
  }
  // Templates such as the *_GEN_VARIABLE_* ammo crates sit at the origin in
  // every frame; with no position they are simply not drawn.
  for (const rows of [snap.deployables, snap.vehicleSpawners, snap.rallyPoints,
                      snap.markers, snap.projectiles]) {
    for (const r of (rows ?? []) as { position?: unknown }[]) {
      if (atOrigin(r.position as Parameters<typeof atOrigin>[0])) r.position = null;
    }
  }
  return snap;
}

// Rally points the enemy burned, and who burned them.
//
// The game decides this, not the viewer. Squad raises a rally's `sieged` flag
// the moment an enemy comes within 30 m of it, and the rally is gone within
// about a minute of that. Measured on an altai Narva recording (~4 Hz): all
// nine siege onsets had the nearest enemy at 24.6-29.8 m, and no enemy was ever
// closer when the flag was still down. So nothing here guesses at a radius or a
// cause. A siege begins on the frame the game says it does, and the enemies
// inside 30 m on that frame are the ones who began it. Whoever walks in later,
// while it is still under siege, is listed after them.
//
// A rally that disappears without being under siege on its last frame was not
// burned (the squad leader put down a new one, the squad disbanded, the round
// ended) and gets no name. Neither does one replaced in the same frame by a new
// rally of the same squad, even if it was under siege at the time.
//
// Incremental, like replayMarkers: a cursor over the growing frame array, with
// everything that spans a chunk boundary kept in the state, so the same frames
// fed in one chunk or in ten produce the same records.

import type { Player, RallyPoint, Snapshot, Vec3 } from "./types";

/** Squad's rally siege radius, in world units (cm). */
export const RALLY_RADIUS_CM = 3_000;

/** How long a burned rally stays drawn on the map after it went, in ms. */
export const BURNED_SHOW_MS = 60_000;

export interface RallyBurner {
  /** Same identity scheme as the rest of the viewer: eosId, pid:<id>, name:<name>. */
  key: string;
  name: string;
  clanTag: string | null;
  teamId: number | null;
  /** Match clock (epoch ms) of the first frame they were seen inside 30 m. */
  enteredMs: number;
  /** Where they were on that frame, world cm. */
  at: { x: number; y: number };
  distM: number;
}

export interface RallySiege {
  startMs: number;
  /** First frame the flag was down again. Null while it is still up. */
  endMs: number | null;
  /** In the order they came inside 30 m; the first is the one who began it. */
  burners: RallyBurner[];
}

export interface RallyRecord {
  id: string;
  team: number | null;
  squadId: number | null;
  squadName: string | null;
  squadStateAddr: string | null;
  position: Vec3;
  sieges: RallySiege[];
  /** First frame it was missing, when it went while under siege. */
  burnedMs: number | null;
}

export interface RallyState {
  /** Frames [0, upTo) have been looked at. */
  upTo: number;
  /** The previous frame's rally array. Two-tier recordings share it between a
   *  full frame and its position frames, so an unchanged identity means there
   *  is nothing new to diff. */
  prevRallies: RallyPoint[] | null;
  prevLive: boolean;
  /** Whether the rally was under siege on the last frame it was present. */
  siegedNow: Map<string, boolean>;
  records: Map<string, RallyRecord>;
}

export function createRallyState(): RallyState {
  return { upTo: 0, prevRallies: null, prevLive: false,
           siegedNow: new Map(), records: new Map() };
}

export function playerKey(p: { eosId: string | null; playerId: number | null;
                               name: string | null }): string {
  return p.eosId ?? (p.playerId != null ? `pid:${p.playerId}` : `name:${p.name ?? "?"}`);
}

export function rallyTeam(rp: { squadTeamId?: number | null; team: number | null }): number | null {
  return rp.squadTeamId ?? rp.team ?? null;
}

/** An enemy soldier that can count toward a siege: on the ground or in a
 *  vehicle, not downed, and not the admin free-cam. */
function contender(p: Player, team: number | null): Vec3 | null {
  const s = p.soldier;
  if (!s || s.stale || !s.position) return null;
  if (p.teamId == null || team == null || p.teamId === team) return null;
  if ((s.classShort ?? "").includes("DeveloperAdminCam")) return null;
  const down = s.lifeState != null ? s.lifeState === "incapacitated"
                                   : s.health != null && s.health <= 0;
  return down ? null : s.position;
}

/** Enemy soldiers inside the radius of `rp` in `snap`, nearest first. */
export function enemiesInside(snap: Snapshot, rp: { position: Vec3 | null;
                              squadTeamId?: number | null; team: number | null }):
                              { player: Player; distM: number }[] {
  const c = rp.position;
  if (!c) return [];
  const team = rallyTeam(rp);
  const out: { player: Player; distM: number }[] = [];
  for (const p of snap.players ?? []) {
    const q = contender(p, team);
    if (!q) continue;
    const d = Math.hypot(q.x - c.x, q.y - c.y);
    if (d <= RALLY_RADIUS_CM) out.push({ player: p, distM: d / 100 });
  }
  return out.sort((a, b) => a.distM - b.distM);
}

/**
 * Look at frames [state.upTo, frames.length). Returns true when any record
 * changed, so the caller knows whether there is anything to publish.
 */
export function extendRallies(state: RallyState, frames: Snapshot[]): boolean {
  let changed = false;
  for (let i = state.upTo; i < frames.length; i++) {
    const f = frames[i]!;
    const t = Date.parse(f.timestamp ?? "");
    if (!Number.isFinite(t)) continue;
    const live = f.gameState?.matchState === "InProgress";
    const rallies = f.rallyPoints ?? [];

    if (rallies !== state.prevRallies) {
      const present = new Set<string>();
      for (const rp of rallies) {
        if (!rp.position) continue;
        present.add(rp.id);
        let rec = state.records.get(rp.id);
        if (!rec) {
          rec = { id: rp.id, team: rallyTeam(rp), squadId: rp.squadId ?? null,
                  squadName: rp.squadName ?? null,
                  squadStateAddr: rp.squadStateAddr ?? null,
                  position: rp.position, sieges: [], burnedMs: null };
          state.records.set(rp.id, rec);
          changed = true;
        } else if (rec.burnedMs != null) {
          // Back again: that was a dropped read, not a burn.
          rec.burnedMs = null;
          changed = true;
        }
        if (rec.squadId == null && rp.squadId != null) { rec.squadId = rp.squadId; changed = true; }
        if (rec.squadName == null && rp.squadName) { rec.squadName = rp.squadName; changed = true; }
        const was = state.siegedNow.get(rp.id) ?? false;
        const now = rp.sieged === true;
        if (now && !was) {
          rec.sieges.push({ startMs: t, endMs: null, burners: [] });
          changed = true;
        } else if (!now && was) {
          const s = rec.sieges[rec.sieges.length - 1];
          if (s && s.endMs == null) { s.endMs = t; changed = true; }
        }
        state.siegedNow.set(rp.id, now);
      }
      for (const rp of state.prevRallies ?? []) {
        if (!rp.position || present.has(rp.id)) continue;
        const rec = state.records.get(rp.id);
        if (!rec || rec.burnedMs != null || !state.siegedNow.get(rp.id)) continue;
        // A round that ended or had not begun burns nothing.
        if (!live || !state.prevLive) continue;
        const replaced = rec.squadStateAddr != null && rallies.some((r) =>
          r.id !== rp.id && r.squadStateAddr === rec.squadStateAddr
          && !(state.prevRallies ?? []).some((q) => q.id === r.id));
        if (replaced) continue;
        rec.burnedMs = t;
        changed = true;
      }
      state.prevRallies = rallies;
    }

    // Every frame, not only when the rally array changed: positions move at
    // 4 Hz between the full frames that carry the rallies.
    for (const rp of rallies) {
      if (!state.siegedNow.get(rp.id)) continue;
      const rec = state.records.get(rp.id);
      const siege = rec?.sieges[rec.sieges.length - 1];
      if (!rec || !siege || siege.endMs != null) continue;
      for (const { player, distM } of enemiesInside(f, rp)) {
        const key = playerKey(player);
        if (siege.burners.some((b) => b.key === key)) continue;
        const q = player.soldier!.position!;
        siege.burners.push({
          // A blank tag reads back as a single space.
          key, name: player.name ?? "?", clanTag: player.clanTag?.trim() || null,
          teamId: player.teamId ?? null, enteredMs: t,
          at: { x: q.x, y: q.y }, distM: Math.round(distM * 10) / 10,
        });
        changed = true;
      }
    }
    state.prevLive = live;
  }
  state.upTo = frames.length;
  return changed;
}

/** A copy safe to hand to React: new objects all the way down. */
export function publishRallies(state: RallyState): RallyRecord[] {
  return [...state.records.values()].map((r) => ({
    ...r, sieges: r.sieges.map((s) => ({ ...s, burners: [...s.burners] })),
  }));
}

/** The siege in progress at `nowMs`, if any. */
export function siegeAt(rec: RallyRecord, nowMs: number): RallySiege | null {
  for (let i = rec.sieges.length - 1; i >= 0; i--) {
    const s = rec.sieges[i]!;
    if (s.startMs > nowMs) continue;
    return s.endMs == null || nowMs < s.endMs ? s : null;
  }
  return null;
}

/** Who had come inside by `nowMs`, for the siege that burned or is burning it. */
export function burnersAt(rec: RallyRecord, nowMs: number): RallyBurner[] {
  const s = rec.burnedMs != null && nowMs >= rec.burnedMs
    ? rec.sieges[rec.sieges.length - 1] ?? null
    : siegeAt(rec, nowMs);
  return s ? s.burners.filter((b) => b.enteredMs <= nowMs) : [];
}

/** Burned rallies to draw at `nowMs`: gone, but not long ago. */
export function burnedAt(records: RallyRecord[], nowMs: number): RallyRecord[] {
  return records.filter((r) => r.burnedMs != null && nowMs >= r.burnedMs
                               && nowMs < r.burnedMs + BURNED_SHOW_MS);
}

/** The match clock the map is showing: the replay playhead when there is one,
 *  else the frame's own timestamp. */
export function clockMs(snap: Snapshot | null, replay: { ms: number; valid: boolean }): number {
  if (replay.valid) return replay.ms;
  return Date.parse(snap?.timestamp ?? "") || 0;
}

/** A burned rally as the RallyPoint it was, so hover, tooltip and the info
 *  panel can treat it like one. */
export function ghostRally(rec: RallyRecord): RallyPoint {
  return {
    id: rec.id, classShort: null, actorName: null, team: rec.team,
    spawningEnabled: false, sieged: false, spawnsRemaining: null,
    squadStateAddr: rec.squadStateAddr, position: rec.position,
    squadId: rec.squadId, squadName: rec.squadName, squadTeamId: rec.team,
  };
}

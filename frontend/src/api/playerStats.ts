// Thin fetch helpers for the agent's persistent player-stats API
// (/api/players, /api/players/<eos>, /api/leaderboard). Same-origin relative
// URLs + no-store, mirroring api/recordings.ts.

import type {
  PlayerSummary, PlayerProfile, LeaderRow, StatsPeriod,
  WeaponMetaRow, MatchHeatmap, LayerHeatmap, HeatmapLayerOption,
  MatchSummaryRow, MatchDetail,
} from "../state/types";

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

export async function searchPlayers(q: string, limit = 30): Promise<PlayerSummary[]> {
  return getJson(`./api/players?q=${encodeURIComponent(q)}&limit=${limit}`);
}

export async function fetchProfile(eosId: string): Promise<PlayerProfile> {
  return getJson(`./api/players/${encodeURIComponent(eosId)}`);
}

export async function fetchLeaderboard(
  stat: string, limit = 50, period: StatsPeriod = "alltime",
): Promise<LeaderRow[]> {
  return getJson(
    `./api/leaderboard?stat=${encodeURIComponent(stat)}` +
    `&limit=${limit}&period=${period}`);
}

export async function fetchWeaponMeta(
  period: StatsPeriod = "alltime", limit = 40,
): Promise<WeaponMetaRow[]> {
  return getJson(`./api/weapons?period=${period}&limit=${limit}`);
}

export async function fetchMatchHeatmap(matchId: string): Promise<MatchHeatmap> {
  return getJson(`./api/heatmap?match=${encodeURIComponent(matchId)}`);
}

/** Only layers that have something to draw: a positioned kill. */
export async function fetchHeatmapLayers(): Promise<HeatmapLayerOption[]> {
  return getJson("./api/layers");
}

export async function fetchMatches(
  period: StatsPeriod = "alltime", limit = 50,
): Promise<MatchSummaryRow[]> {
  return getJson(`./api/matches?period=${period}&limit=${limit}`);
}

export async function fetchMatchDetail(matchId: string): Promise<MatchDetail> {
  return getJson(`./api/match/${encodeURIComponent(matchId)}`);
}

export async function fetchLayerHeatmap(
  layer: string, period: StatsPeriod = "alltime", cellCm = 5000,
): Promise<LayerHeatmap> {
  return getJson(
    `./api/heatmap?layer=${encodeURIComponent(layer)}` +
    `&period=${period}&cell=${cellCm}`);
}

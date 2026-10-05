// Moderator live map: the few server calls and the rules of the live edge.
// Fork-only; see docs/superpowers/specs/2026-10-05-live-replay-design.md.
import { LIVE_DELAY_MS } from "../api/recordings";

// off = the server has no live map (404), anon = logged out, ok = logged in,
// unknown = could not tell (network or server error).
export type LiveAccessState = "unknown" | "off" | "anon" | "ok";

export async function probeSession(): Promise<LiveAccessState> {
  try {
    const r = await fetch("./api/live/session", { cache: "no-store" });
    if (r.status === 404) return "off";
    if (!r.ok) return "unknown";
    const body = (await r.json()) as { authenticated?: unknown };
    return body.authenticated === true ? "ok" : "anon";
  } catch {
    return "unknown";
  }
}

export type LoginResult = { ok: true } | { ok: false; message: string };

export function retryAfterMinutes(header: string | null): number {
  const sec = Number(header);
  return Number.isFinite(sec) && sec > 0 ? Math.max(1, Math.ceil(sec / 60)) : 1;
}

export async function login(password: string): Promise<LoginResult> {
  let r: Response;
  try {
    r = await fetch("./api/live/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } catch {
    return { ok: false, message: "Server nicht erreichbar." };
  }
  if (r.ok) return { ok: true };
  if (r.status === 401) return { ok: false, message: "Falsches Passwort." };
  if (r.status === 429) {
    const n = retryAfterMinutes(r.headers.get("Retry-After"));
    return { ok: false, message: `Zu viele Fehlversuche – bitte in ${n} Min. erneut versuchen.` };
  }
  return { ok: false, message: `Anmeldung fehlgeschlagen (HTTP ${r.status}).` };
}

export async function logout(): Promise<void> {
  try {
    await fetch("./api/live/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    // The session still expires on the server on its own.
  }
}

export interface LiveRound {
  /** The recording's file name without .sqrx. */
  id: string;
  /** Timestamp of its newest full frame, on the SERVER's clock. */
  latestMs: number;
}

/** The round being recorded right now; "anon" if logged out, null if none. */
export async function fetchLiveRound(): Promise<LiveRound | "anon" | null> {
  try {
    const r = await fetch("./api/live/round", { cache: "no-store" });
    if (r.status === 401) return "anon";
    if (!r.ok) return null;
    const m = (await r.json()) as { id?: unknown; latestUtc?: unknown };
    const latestMs = Date.parse(String(m.latestUtc));
    if (typeof m.id !== "string" || !Number.isFinite(latestMs)) return null;
    return { id: m.id, latestMs };
  } catch {
    return null;
  }
}

export interface EdgeInput {
  playing: boolean;
  stalled: boolean;
  speed: number;
  /** Newest held frame minus the playhead, in ms. */
  lagMs: number;
  /** The live edge itself paused playback to rebuffer. */
  pausedForBuffer: boolean;
}

export type EdgeAction = "slow" | "buffer" | "resume" | "unstall" | null;

/**
 * What the live edge does next, looked at four times a second.
 *
 * slow:    caught up faster than 1x: play on at 1x, which is live.
 * buffer:  the playhead hit the newest frame: pause until LIVE_DELAY_MS is
 *          held again, instead of stuttering frame by frame at the edge.
 * resume:  enough is held again (or the user went back): play on.
 * unstall: the user paused far back during a stall: their pause stays, the
 *          "buffering" banner goes.
 */
export function edgeStep(s: EdgeInput): EdgeAction {
  if (s.stalled && s.lagMs < LIVE_DELAY_MS) return s.playing ? "buffer" : null;
  if (s.stalled) return s.playing || s.pausedForBuffer ? "resume" : "unstall";
  if (s.playing && s.speed > 1 && s.lagMs <= LIVE_DELAY_MS) return "slow";
  return null;
}

/** At the live edge: playing, and no more than 3 s further back than it. */
export function isAtLive(playing: boolean, lagMs: number): boolean {
  return playing && lagMs <= LIVE_DELAY_MS + 3000;
}

/**
 * The round is over: move on once it has played out, or at once if none of it
 * ever arrived (the stream answered 404 because the round had just changed).
 */
export function shouldAdvanceRound(frameCount: number, currentIdx: number,
                                   playing: boolean): boolean {
  return frameCount === 0 || (!playing && currentIdx >= frameCount - 1);
}

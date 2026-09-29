// Moderator live map: the few server calls and the per-connection frame
// decoder. Fork-only; see docs/superpowers/specs/2026-09-29-live-moderation-design.md.
import type { Snapshot } from "../state/types";
import { ReplayReconstructor, type RecordingLine } from "../state/replayReconstruct";

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

export interface LiveFeed {
  push(data: string): Snapshot | null;
}

// One per connection: the stream always starts with a full frame, and the
// reconstructor folds the 4 Hz position frames onto the last one.
export function createLiveFeed(): LiveFeed {
  const recon = new ReplayReconstructor();
  return {
    push(data: string): Snapshot | null {
      let v: unknown;
      try { v = JSON.parse(data); } catch { return null; }
      if (!v || typeof v !== "object" || Array.isArray(v)) return null;
      return recon.push(v as RecordingLine);
    },
  };
}

// Moderator live map: login dialog, the Home + TopBar buttons, and the live
// edge of the replay player. Fork-only. The live map IS a replay, of the round
// being recorded right now, which keeps growing while it plays. Design:
// docs/superpowers/specs/2026-10-05-live-replay-design.md; login and sessions:
// docs/superpowers/specs/2026-09-29-live-moderation-design.md.
// Renders nothing unless the server has the live map enabled: without it,
// GET ./api/live/session answers 404 and the public UI stays as it is.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { create } from "zustand";
import { useViewerStore } from "../state/viewerStore";
import { replayClock } from "../state/replayClock";
import { isLiveId, LIVE_DELAY_MS, LIVE_ID_PREFIX } from "../api/recordings";
import {
  edgeStep, fetchLiveRound, isAtLive, login, logout, probeSession, shouldAdvanceRound,
  type LiveAccessState,
} from "./client";
import "./live.css";

const RETRY_MS = 5000;       // between "is a round being recorded yet?" asks
const RECONNECT_MS = 3000;   // before re-opening a stream that dropped mid-round
const TICK_MS = 250;         // how often the live edge looks at the player

interface LiveStore {
  access: LiveAccessState;
  loginOpen: boolean;
  notice: string | null;
  /** In the live map, but no round is being recorded right now. */
  waiting: boolean;
  /** The playhead is at the live edge. */
  atLive: boolean;
}

const useLive = create<LiveStore>(() => ({
  access: "unknown", loginOpen: false, notice: null, waiting: false, atLive: false,
}));

let retryTimer = 0;

function setUrlMode(mode: "live" | null): void {
  const url = new URL(window.location.href);
  if (mode) url.searchParams.set("mode", mode);
  else url.searchParams.delete("mode");
  url.searchParams.delete("id");
  window.history.replaceState(null, "", url.toString());
}

function openLogin(notice: string | null = null): void {
  useLive.setState({ loginOpen: true, notice });
}

/** Still waiting for a round, and not gone off to watch a finished match meanwhile. */
function stillWaiting(): boolean {
  const s = useViewerStore.getState();
  const pastMatch = s.mode === "replay" && s.replay.id !== null && !isLiveId(s.replay.id);
  return useLive.getState().waiting && !pastMatch;
}

export function exitLive(): void {
  window.clearTimeout(retryTimer);
  useLive.setState({ waiting: false });
  useViewerStore.getState().openReplay(null);
  useViewerStore.getState().setMode("home");
  setUrlMode(null);
}

function sessionLost(): void {
  useLive.setState({ access: "anon" });
  exitLive();
  openLogin("Sitzung abgelaufen – bitte neu anmelden.");
}

/**
 * To the live edge of the running round. One way in for everything: the Home
 * button, a reload on ?mode=live, the LIVE button, and the next round.
 */
export async function goLive(): Promise<void> {
  window.clearTimeout(retryTimer);
  const round = await fetchLiveRound();
  if (round === "anon") { sessionLost(); return; }
  const v = useViewerStore.getState();
  setUrlMode("live");
  if (!round) {
    // Between rounds. Whatever is on screen stays: the start page, or the end
    // of the last round. The banner says why nothing happens.
    useLive.setState({ waiting: true });
    retryTimer = window.setTimeout(() => { if (stillWaiting()) void goLive(); }, RETRY_MS);
    return;
  }
  useLive.setState({ waiting: false });
  const id = LIVE_ID_PREFIX + round.id;
  const from = round.latestMs - LIVE_DELAY_MS;
  const r = v.replay;
  if (r.id !== id) {
    // Four store updates in one task, which React renders once. Even if it did
    // not, restartReplayAt's nonce bump aborts a load that began at minute zero.
    v.openReplay(id);
    v.setReplay((x) => ({ ...x, playing: true }));
    v.restartReplayAt(from);
    v.setMode("replay");
  } else if (r.loading && r.bufferedMs >= round.latestMs - 3000) {
    // The edge is already held: go there without loading anything.
    const target = r.bufferedMs - LIVE_DELAY_MS;
    let i = r.frameCount - 1;
    while (i > 0 && Date.parse(r.frames[i]!.timestamp ?? "") > target) i--;
    v.setReplay((x) => ({ ...x, currentIdx: i, speed: 1, playing: true,
                          baseWallMs: 0, baseSnapMs: 0 }));
    if (v.mode !== "replay") v.setMode("replay");
  } else {
    v.setReplay((x) => ({ ...x, speed: 1, playing: true }));
    v.restartReplayAt(from);
    if (v.mode !== "replay") v.setMode("replay");
  }
}

/**
 * The live edge, for as long as a live round is open: 1x once caught up,
 * rebuffer at the edge, light the LIVE button, and when the stream closes find
 * out why: logged out, dropped, or the round is over.
 */
function useLiveEdge(): void {
  const id = useViewerStore((s) => s.replay.id);
  useEffect(() => {
    if (!isLiveId(id)) { useLive.setState({ atLive: false }); return; }
    let pausedForBuffer = false;
    let wasLoading = true;
    let roundOver = false;
    let timer = 0;
    const tick = () => {
      const s = useViewerStore.getState();
      const r = s.replay;
      if (r.id !== id) return;
      const lagMs = replayClock.valid ? r.bufferedMs - replayClock.ms : Infinity;
      switch (edgeStep({ playing: r.playing, stalled: r.stalled, speed: r.speed, lagMs,
                         pausedForBuffer })) {
        case "slow":
          s.setReplay((x) => ({ ...x, speed: 1, baseWallMs: 0, baseSnapMs: 0 }));
          break;
        case "buffer":
          pausedForBuffer = true;
          s.setReplay((x) => ({ ...x, playing: false }));
          break;
        case "resume":
          pausedForBuffer = false;
          s.setReplay((x) => ({ ...x, playing: true, stalled: false,
                                baseWallMs: 0, baseSnapMs: 0 }));
          break;
        case "unstall":
          s.setReplay((x) => ({ ...x, stalled: false }));
          break;
      }
      if (!r.stalled) pausedForBuffer = false;
      const atLive = isAtLive(r.playing, lagMs);
      if (useLive.getState().atLive !== atLive) useLive.setState({ atLive });

      if (wasLoading && !r.loading) {
        void fetchLiveRound().then((round) => {
          if (useViewerStore.getState().replay.id !== id) return;
          if (round === "anon") {
            sessionLost();
          } else if (round && LIVE_ID_PREFIX + round.id === id) {
            // Same round, so the connection dropped: carry on where the playhead is.
            timer = window.setTimeout(() => {
              const now = useViewerStore.getState();
              if (now.replay.id !== id || now.replay.loading) return;
              if (now.replay.frameCount) now.restartReplayAt(replayClock.ms);
              else void goLive();
            }, RECONNECT_MS);
          } else {
            roundOver = true;
          }
        });
      }
      wasLoading = r.loading;
      if (roundOver && shouldAdvanceRound(r.frameCount, r.currentIdx, r.playing)) {
        roundOver = false;
        void goLive();
      }
    };
    const iv = window.setInterval(tick, TICK_MS);
    return () => { window.clearInterval(iv); window.clearTimeout(timer); };
  }, [id]);
}

function LoginDialog() {
  const open = useLive((s) => s.loginOpen);
  const notice = useLive((s) => s.notice);
  const dlgRef = useRef<HTMLDialogElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const secure = window.isSecureContext;

  useEffect(() => {
    const dlg = dlgRef.current;
    if (!dlg) return;
    const onClose = () => useLive.setState({ loginOpen: false });
    dlg.addEventListener("close", onClose);
    return () => dlg.removeEventListener("close", onClose);
  }, []);

  useEffect(() => {
    const dlg = dlgRef.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      setError(null);
      dlg.showModal();
      pwRef.current?.focus();
    } else if (!open && dlg.open) {
      dlg.close();
    }
  }, [open]);

  const close = () => useLive.setState({ loginOpen: false });

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const input = pwRef.current;
    if (!input || pending) return;
    const password = input.value;
    input.value = "";
    setPending(true);
    const res = await login(password);
    setPending(false);
    if (!res.ok) {
      setError(res.message);
      input.focus();
      return;
    }
    useLive.setState({ access: "ok", loginOpen: false, notice: null });
    void goLive();
  };

  return (
    <dialog id="live-login" ref={dlgRef} onKeyDown={(e) => e.stopPropagation()}>
      <form method="post" onSubmit={(e) => { void submit(e); }}>
        <h2>Moderator-Login</h2>
        <p className="ll-sub">Live-Karte der laufenden Runde – nur für das Moderationsteam.</p>
        {notice && <p className="ll-notice">{notice}</p>}
        {secure ? (
          <>
            <input className="ll-hidden" type="text" name="username" autoComplete="username"
                   value="moderator" readOnly tabIndex={-1} aria-hidden="true" />
            <label className="ll-field">
              <span>Passwort</span>
              <input ref={pwRef} type="password" name="password"
                     autoComplete="current-password" required />
            </label>
            {error && <p className="ll-error" role="alert">{error}</p>}
            <div className="ll-actions">
              <button type="button" className="btn btn-ghost" onClick={close}>Abbrechen</button>
              <button type="submit" className="btn btn-primary" disabled={pending}>Anmelden</button>
            </div>
          </>
        ) : (
          <>
            <p className="ll-error" role="alert">Anmeldung nur über HTTPS möglich.</p>
            <div className="ll-actions">
              <button type="button" className="btn btn-ghost" onClick={close}>Abbrechen</button>
            </div>
          </>
        )}
      </form>
    </dialog>
  );
}

function WaitingBanner() {
  const waiting = useLive((s) => s.waiting);
  const mode = useViewerStore((s) => s.mode);
  const id = useViewerStore((s) => s.replay.id);
  if (!waiting || !(mode === "home" || (mode === "replay" && isLiveId(id)))) return null;
  return (
    <div id="live-waiting" className="buf-banner" role="status">
      <span className="buf-spin" />
      <span>Warte auf die nächste Runde…</span>
    </div>
  );
}

// Mounted once in App, outside the mode branch.
export function LiveAccess() {
  const access = useLive((s) => s.access);
  useLiveEdge();

  useEffect(() => {
    let cancelled = false;
    void probeSession().then((a) => {
      if (cancelled) return;
      useLive.setState({ access: a });
      if (new URL(window.location.href).searchParams.get("mode") !== "live") return;
      if (a === "ok") void goLive();
      else if (a === "anon") openLogin();
      else if (a === "off") setUrlMode(null);
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <WaitingBanner />
      {(access === "anon" || access === "ok") && <LoginDialog />}
    </>
  );
}

// Home nav: the way in. Nothing at all when the server has no live map.
export function LiveEntry() {
  const access = useLive((s) => s.access);
  if (access === "anon") {
    return <button className="btn btn-ghost" onClick={() => openLogin()}>Moderator-Login</button>;
  }
  if (access !== "ok") return null;
  return <button className="btn btn-ghost" onClick={() => { void goLive(); }}>Live-Karte</button>;
}

// TopBar, while the live map is shown.
export function LiveControls() {
  const mode = useViewerStore((s) => s.mode);
  const id = useViewerStore((s) => s.replay.id);
  const atLive = useLive((s) => s.atLive);
  if (mode !== "replay" || !isLiveId(id)) return null;
  const signOut = async () => {
    await logout();
    useLive.setState({ access: "anon" });
    exitLive();
  };
  return (
    <>
      <button className="tb-back" onClick={exitLive} title="zur Startseite">← Zurück</button>
      <button className={"live-edge" + (atLive ? " on" : "")} disabled={atLive}
              onClick={() => { void goLive(); }}
              title={atLive ? "live" : "zum Live-Rand springen"}>● LIVE</button>
      <button onClick={() => { void signOut(); }} title="Live-Sitzung beenden">Abmelden</button>
    </>
  );
}

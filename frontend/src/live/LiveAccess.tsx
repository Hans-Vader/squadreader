// Moderator live map: login dialog, stream hook, and the Home + TopBar buttons.
// Fork-only (docs/superpowers/specs/2026-09-29-live-moderation-design.md).
// Renders nothing unless the server has the live map enabled: without it,
// GET ./api/live/session answers 404 and the public UI stays as it is.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { create } from "zustand";
import { useViewerStore } from "../state/viewerStore";
import { createLiveFeed, login, logout, probeSession, type LiveAccessState } from "./client";
import "./live.css";

const RECONNECT_MS = 5000;

interface LiveStore {
  access: LiveAccessState;
  loginOpen: boolean;
  notice: string | null;
}

const useLive = create<LiveStore>(() => ({ access: "unknown", loginOpen: false, notice: null }));

// Frames of whatever was shown before, a replay say, must not seed the live
// kill-feed diff: useKillFeed reads curSnap on the mode flip.
const EMPTY_FRAMES = { curSnap: null, prevSnap: null, lastInProgressTeams: null, curArrivalMs: 0 };

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

export function enterLive(): void {
  useViewerStore.setState({ ...EMPTY_FRAMES, status: "connecting" });
  useViewerStore.getState().setMode("live");
  setUrlMode("live");
}

export function exitLive(): void {
  useViewerStore.getState().setMode("home");
  setUrlMode(null);
}

function useLiveStream(): void {
  const mode = useViewerStore((s) => s.mode);
  useEffect(() => {
    if (mode !== "live") return;
    const store = useViewerStore.getState;
    let es: EventSource | null = null;
    let timer = 0;
    let stopped = false;

    const connect = (): void => {
      const feed = createLiveFeed();
      const source = new EventSource("./api/live/stream");
      es = source;
      source.onopen = () => store().setStatus("live");
      source.onmessage = (ev: MessageEvent) => {
        if (store().mode !== "live") return;
        const snap = feed.push(String(ev.data));
        if (snap) store().ingestLive(snap);
      };
      source.onerror = () => {
        store().setStatus("reconnecting");
        if (source.readyState !== EventSource.CLOSED) return;   // the browser retries itself
        source.close();
        es = null;
        void probeSession().then((access) => {
          if (stopped) return;
          if (access === "anon") {
            useLive.setState({ access: "anon" });
            exitLive();
            openLogin("Sitzung abgelaufen – bitte neu anmelden.");
            return;
          }
          // "ok", "unknown" (proxy or server hiccup) and "off" (Traefik's 404
          // while the container restarts) all mean: try again shortly.
          timer = window.setTimeout(connect, RECONNECT_MS);
        });
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      es?.close();
      useViewerStore.setState({ ...EMPTY_FRAMES });
    };
  }, [mode]);
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
    enterLive();
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

// Mounted once in App, outside the mode branch.
export function LiveAccess() {
  const access = useLive((s) => s.access);
  useLiveStream();

  useEffect(() => {
    let cancelled = false;
    void probeSession().then((a) => {
      if (cancelled) return;
      useLive.setState({ access: a });
      if (new URL(window.location.href).searchParams.get("mode") !== "live") return;
      if (a === "ok") enterLive();
      else if (a === "anon") openLogin();
      else if (a === "off") setUrlMode(null);
    });
    return () => { cancelled = true; };
  }, []);

  if (access !== "anon" && access !== "ok") return null;
  return <LoginDialog />;
}

// Home nav: only for a logged-in moderator.
export function LiveEntry() {
  const access = useLive((s) => s.access);
  if (access !== "ok") return null;
  return <button className="btn btn-ghost" onClick={enterLive}>Live-Karte</button>;
}

// TopBar, live mode only.
export function LiveControls() {
  const mode = useViewerStore((s) => s.mode);
  if (mode !== "live") return null;
  const signOut = async () => {
    await logout();
    useLive.setState({ access: "anon" });
    exitLive();
  };
  return (
    <>
      <button className="tb-back" onClick={exitLive} title="zur Startseite">← Zurück</button>
      <button onClick={() => { void signOut(); }} title="Live-Sitzung beenden">Abmelden</button>
    </>
  );
}

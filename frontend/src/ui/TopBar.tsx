// Top-left HUD: server, status, map/mode, tick + rate + latency.
// Top-right: zoom indicator + fit / scoreboard / Live·Rewind toggle
// + Open Recordings dialog.

import { useEffect, useState } from "react";
import { useViewerStore } from "../state/viewerStore";
import { ClipRecorder } from "./ClipRecorder";
import { SettingsMenu } from "./SettingsMenu";

export function TopBar() {
  const status = useViewerStore((s) => s.status);
  const curSnap = useViewerStore((s) => s.curSnap);
  const view = useViewerStore((s) => s.view);
  const resetView = useViewerStore((s) => s.resetView);
  const avgTickMs = useViewerStore((s) => s.avgTickMs);
  const toggleScoreboard = useViewerStore((s) => s.toggleScoreboard);
  const mode = useViewerStore((s) => s.mode);
  const replayId = useViewerStore((s) => s.replay.id);
  const setMode = useViewerStore((s) => s.setMode);
  const timelineVisible = useViewerStore((s) => s.timelineVisible);
  const toggleTimeline = useViewerStore((s) => s.toggleTimeline);

  // Rate + latency are derived display state, recomputed cheaply on each
  // store tick (player viewer is desktop, no need for memoization).
  const gs = curSnap?.gameState ?? null;
  const serverName = gs?.serverName ?? curSnap?.server ?? "—";
  const rate = avgTickMs > 0 ? (1000 / avgTickMs).toFixed(2) + " Hz" : "— Hz";
  // Date of the frame being rendered, for the "what am I watching" line.
  const matchDate = (() => {
    const ts = curSnap?.timestamp;
    if (!ts) return null;
    const d = new Date(ts);
    return isNaN(d.getTime())
      ? null
      : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  })();

  // How long since data last ARRIVED. Measured on our own clock (arrival time,
  // not the snapshot's server timestamp) so it can't be thrown off by clock skew.
  //
  // It ticks on its own interval rather than off curSnap: when the producer
  // freezes, curSnap stops changing, so an age derived from it would freeze too —
  // the staleness indicator would be the last thing to notice the data went stale.
  const [ageSec, setAgeSec] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => {
      const s = useViewerStore.getState();
      setAgeSec(s.curSnap && s.curArrivalMs
        ? Math.max(0, (performance.now() - s.curArrivalMs) / 1000)
        : null);
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);
  // At 0.5 Hz a healthy age oscillates 0-2 s. 8 s is four missed ticks (odd), 30 s
  // is fifteen (the producer is wedged, not merely slow).
  const ageClass = ageSec == null ? ""
                 : ageSec > 30 ? "bad"
                 : ageSec > 8  ? "warn" : "";

  const statusClass = status === "live" ? "live"
                    : status === "reconnecting" ? "bad"
                    : status === "replay" ? "warn"
                    : "warn";
  const STATUS_TR: Record<string, string> = {
    connecting: "connecting", live: "live", reconnecting: "reconnecting",
    replay: "recording", idle: "idle",
  };

  const openPicker = () => {
    const dlg = document.getElementById("recording-picker") as
      HTMLDialogElement | null;
    dlg?.showModal();
  };

  // Exit the replay to the landing page. Here that page is a view of this
  // same app, so flip the store instead of navigating: no reload, the picker
  // and stats dialogs stay mounted, and useReplayLoader drops the frames on
  // its own once mode leaves "replay". Inverse of Home's playRecording, down
  // to clearing the params it wrote.
  const goBack = () => {
    setMode("home");
    const url = new URL(window.location.href);
    url.searchParams.delete("mode");
    url.searchParams.delete("id");
    window.history.replaceState(null, "", url.toString());
  };

  return (
    <>
      <div id="hud">
        <div><b>{serverName}</b>
          <span className="pill beta-pill"
                title="System is in beta — feedback: reach out to your server admin">
            BETA
          </span>
          <span className={"pill " + statusClass}>
            {mode === "replay" ? "recording" : (STATUS_TR[status] ?? status)}
          </span></div>
        <div>tick <b>{curSnap?.tick ?? "—"}</b>
          {" · "}<span>{rate}</span>
          {" · "}
          {/* Which match am I watching? The opaque match id used to be the only
              answer the player chrome gave — unreadable, unmemorable, and no
              help at all once you had opened three of them. The layer and the
              date are already in the frame we are rendering. */}
          {mode === "replay"
            ? <span title={replayId ?? undefined}>
                {gs?.mapName ?? replayId ?? "—"}
                {matchDate ? " · " + matchDate : ""}
              </span>
            : <span className={"age " + ageClass}
                    title="time since last data">
                {ageSec == null ? "No data" : `data ${ageSec.toFixed(0)}s`}
              </span>}
        </div>
      </div>
      <div id="controls">
        {/* Exit-replay: back to the landing page. Only while watching. */}
        {mode === "replay" && (
          <button className="tb-back" onClick={goBack}
                  title="back to the landing page">← Back</button>
        )}
        <button onClick={openPicker} title="watch past matches">
          Past Matches
        </button>
        <span>zoom <b>{view.zoom.toFixed(1)}x</b></span>
        <button onClick={() => resetView()} title="reset view (F)">Fit</button>
        <button onClick={() => toggleScoreboard()}
                title="scoreboard (Tab)">score</button>
        {mode === "replay" && (
          <button className={timelineVisible ? "on" : ""}
                  onClick={() => toggleTimeline()}
                  title="ticket-loss timeline (G)">Tickets</button>
        )}
        <ClipRecorder />
        <SettingsMenu />
      </div>
    </>
  );
}

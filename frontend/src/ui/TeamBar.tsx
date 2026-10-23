// Top-center match strip: both teams (faction, tickets, K/D, players)
// flanking a centre block (map · mode + match timer + state). The single
// highest-value at-a-glance addition for a tactical map — tickets and
// who's ahead without opening the scoreboard.

import { useViewerStore } from "../state/viewerStore";
import type { TeamState } from "../state/types";

function fmtClock(sec: number | null | undefined): string {
  if (sec == null || sec < 0) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// "USMC_LO_Motorized" -> "USMC"; "PLA_LO_Motorized" -> "PLA".
function shortFaction(id: string | null | undefined): string {
  if (!id) return "—";
  return id.split("_")[0] || id;
}

// The game's own phase names, as a viewer would say them. Unknown phases show
// as they come rather than vanish.
const STATE_LABEL: Record<string, string> = {
  InProgress: "In progress",
  EnteringMap: "Loading map",
  WaitingToStart: "Starting",
  Warmup: "Warmup",
  WaitingPostMatch: "Match over",
  PostMatch: "Match over",
  EndState: "Match over",
  LeavingMap: "Changing map",
  Aborted: "Cancelled",
};

const PersonIcon = () => (
  <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true" fill="currentColor">
    <circle cx="8" cy="5" r="2.6" />
    <path d="M3 14c0-2.9 2.2-4.8 5-4.8s5 1.9 5 4.8z" />
  </svg>
);

function TeamCol({ team, align }: { team: TeamState | undefined; align: "left" | "right" }) {
  const id = team?.id ?? (align === "left" ? 1 : 2);
  const tickets = team?.tickets;
  return (
    <div className={"tb-team " + (align === "left" ? "tb-left" : "tb-right")}
         style={{ ["--tc" as string]: `var(--team${id})` }}>
      <div className="tb-team-main">
        <span className="tb-faction">{shortFaction(team?.factionId)}</span>
        <span className="tb-tickets">{tickets ?? "—"}</span>
      </div>
      <div className="tb-team-sub">
        <span className="tb-players" title="Players">
          <PersonIcon />{team?.playerCount ?? "—"}</span>
        <span className="tb-kd" title="Kills / deaths">
          <i>K/D</i> {team?.kills ?? 0}/{team?.deaths ?? 0}</span>
      </div>
    </div>
  );
}

export function TeamBar() {
  const curSnap = useViewerStore((s) => s.curSnap);
  const gs = curSnap?.gameState ?? null;
  const teams = curSnap?.teams ?? [];
  const t1 = teams.find((t) => t.id === 1) ?? teams[0];
  const t2 = teams.find((t) => t.id === 2) ?? teams[1];

  // Relative ticket share for the split bar under the centre block.
  const a = Math.max(0, t1?.tickets ?? 0);
  const b = Math.max(0, t2?.tickets ?? 0);
  const total = a + b;
  const pct1 = total > 0 ? (a / total) * 100 : 50;

  const mapLine = [gs?.mapName, gs?.gameModeName].filter(Boolean).join(" · ") || "—";
  const state = gs?.matchState ?? "";
  const live = state === "InProgress";

  return (
    <div id="team-bar">
      <TeamCol team={t1} align="left" />
      <div className="tb-centre">
        <div className="tb-map">{mapLine}</div>
        <div className="tb-clock">{fmtClock(gs?.elapsedSec)}
          {state && <span className={"tb-state " + (live ? "on" : "")}>
            {STATE_LABEL[state] ?? state}</span>}
        </div>
        <div className="tb-ticketbar">
          <div className="tb-tb-fill tb-tb-1" style={{ width: `${pct1}%` }} />
          <div className="tb-tb-fill tb-tb-2" style={{ width: `${100 - pct1}%` }} />
        </div>
      </div>
      <TeamCol team={t2} align="right" />
    </div>
  );
}

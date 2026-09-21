// Landing / selection page. Brand bar (status + stats + recordings) + recent
// matches as minimap tiles (click = watch replay) + top players (click =
// stats). Rendered instead of the map when mode === "home". Data comes from
// the existing stats/recordings API — this build has no live stream.
import { useEffect, useState } from "react";
import { useViewerStore } from "../state/viewerStore";
import { listRecordings } from "../api/recordings";
import { fetchLeaderboard } from "../api/playerStats";
import { fallbackMap } from "../canvas/mapFallback";
import logoUrl from "../assets/dcn.png";
import type { RecordingMeta, LeaderRow } from "../state/types";

// The backend serves the SPA at `/` and its build output under `/assets/` —
// and 404s everything else (httpsrv.py's do_GET ladder). So the landing
// images have to go THROUGH the bundler rather than sit in a public/ dir:
// this hands back the hashed dist/assets/ URL Vite emitted for each one.
// Keyed by the fallback table's `texture`, which is what names the files
// scripts/gen_map_assets.py writes.
const THUMBS = import.meta.glob("../assets/thumbs/*.webp", {
  eager: true, query: "?url", import: "default",
}) as Record<string, string>;

function fmtDate(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(+d)) return "—";
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) +
    " " + d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
function fmtDur(sec: number | null): string {
  if (!sec || sec <= 0) return "—";
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}

/** Minimap thumbnail for a tile, or undefined when we ship no image for that
 *  map. Same lookup the canvas uses for recordings without a layer block, but
 *  pointed at the small copies from scripts/gen_map_thumbs.py — the real
 *  sqmaps are 2.5-6.4 MB each and six of them are on this page.
 *
 *  Covers modded maps too — fallbackMap merges custom_maps.json. A map in
 *  neither table resolves to nothing and its tile just shows the layer name
 *  on the dark card. */
function thumbUrl(r: RecordingMeta): string | undefined {
  const m = fallbackMap(r.mapName) ?? fallbackMap(r.layerName);
  return m ? THUMBS[`../assets/thumbs/${m.texture}.webp`] : undefined;
}

const showModal = (id: string) =>
  (document.getElementById(id) as HTMLDialogElement | null)?.showModal();

export function Home() {
  const canLive = useViewerStore((s) => s.canLive);
  const setMode = useViewerStore((s) => s.setMode);
  const setReplay = useViewerStore((s) => s.setReplay);

  const [recs, setRecs] = useState<RecordingMeta[] | null>(null);
  const [top, setTop] = useState<LeaderRow[] | null>(null);

  useEffect(() => {
    listRecordings().then(setRecs).catch(() => setRecs([]));
    fetchLeaderboard("kills", 6, "alltime").then(setTop).catch(() => setTop([]));
  }, []);

  const online = canLive !== false; // null (still probing) or true → assume online
  // No in-progress branch: /api/recordings lists a match only once it is
  // finalized, so this build never sees one still in play.
  const tiles = (recs ?? []).slice(0, 6);

  const playRecording = (id: string) => {
    setReplay((r) => ({ ...r, id, frames: [], currentIdx: 0, playing: false,
                        speed: 1, baseWallMs: 0, baseSnapMs: 0 }));
    setMode("replay");
    const url = new URL(window.location.href);
    url.searchParams.set("mode", "replay");
    url.searchParams.set("id", id);
    window.history.replaceState(null, "", url.toString());
  };

  return (
    <div id="home">
      <div className="hm-wrap">
        <header className="hm-bar">
          <div className="hm-brand">
            <img src={logoUrl} alt="" width={38} height={38} />
            <span className="hm-word">Dach Community Night</span>
          </div>
          <nav className="hm-nav">
            <span className={"hm-status " + (online ? "is-on" : "is-off")}>
              <span className="hm-status-dot" />
              {online ? "Server online" : "Archiv"}
            </span>
            <button className="btn btn-ghost" onClick={() => showModal("player-stats")}>Statistiken</button>
            <button className="btn btn-primary" onClick={() => showModal("recording-picker")}>Alle Aufzeichnungen</button>
          </nav>
        </header>

        <div className="hm-sect-h">
          <span className="hm-lbl">Letzte Matches</span>
          <span className="hm-lbl">{recs?.length ?? 0} Aufzeichnungen</span>
        </div>
        <div className="hm-grid">
          {recs === null && <div className="hm-empty">Laden…</div>}
          {recs !== null && tiles.length === 0 && <div className="hm-empty">Noch keine Aufzeichnungen.</div>}
          {tiles.map((r) => {
            const thumb = thumbUrl(r);
            return (
              <button key={r.id} className="hm-tile" onClick={() => playRecording(r.id)}>
                <span className="hm-th">
                  {thumb && <img src={thumb} alt="" loading="lazy" />}
                  <span className="hm-th-ov" />
                  <span className="chip hm-th-mode">{r.gameMode ?? "—"}</span>
                  {/* The game spells some layers with underscores; the tile
                      reads better without them, and no map name needs one. */}
                  <span className="hm-th-name">
                    {(r.layerName ?? r.mapName ?? r.filename).replace(/_/g, " ")}
                  </span>
                </span>
                <span className="hm-meta">
                  <span>{fmtDur(r.durationSec)}</span>
                  <span>{r.peakPlayers ?? "?"} Spieler</span>
                  <span className="hm-meta-sp" />
                  <span>{fmtDate(r.endedAtUtc ?? r.startedAtUtc)}</span>
                </span>
              </button>
            );
          })}
        </div>

        <section className="hm-strip">
          <div className="hm-sect-h">
            <span className="hm-lbl">Top Spieler</span>
            <span className="hm-lbl">Allzeit</span>
          </div>
          <div className="hm-players">
            {top === null && <div className="hm-empty">Laden…</div>}
            {top !== null && top.length === 0 && <div className="hm-empty">Keine Daten.</div>}
            {(top ?? []).map((p) => (
              <button key={p.eos_id} className="hm-p" onClick={() => showModal("player-stats")}>
                <span className="hm-pname">
                  {p.last_clan_tag && <span className="hm-clan">[{p.last_clan_tag}]</span>}
                  {p.last_name ?? "?"}
                </span>
                <span className="hm-pval">{p.value}<span className="hm-pval-k"> Kills</span></span>
              </button>
            ))}
          </div>
        </section>

        <footer className="hm-foot">
          <a href="https://github.com/cagrianilokumus/squadreader"
             target="_blank" rel="noreferrer">sqreader</a>
          {" · AGPLv3 + Commons Clause · "}
          <a href="https://github.com/Hans-Vader/squadreader"
             target="_blank" rel="noreferrer">Quellcode dieser Instanz</a>
        </footer>
      </div>
    </div>
  );
}

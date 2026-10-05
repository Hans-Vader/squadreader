// Standalone unit test for the live map's client side. Bundled with esbuild and
// run under node, no framework (matches replayReconstruct.test.mts).
import { edgeStep, isAtLive, retryAfterMinutes, shouldAdvanceRound } from "./client.ts";
import { useViewerStore } from "../state/viewerStore.ts";
import { isLiveId, LIVE_DELAY_MS, recordingUrl } from "../api/recordings.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

// 5. Retry-After seconds become whole minutes for the message.
eq(retryAfterMinutes("600"), 10, "600 s is 10 min");
eq(retryAfterMinutes("61"), 2, "61 s rounds up");
eq(retryAfterMinutes("1"), 1, "at least one minute");
eq(retryAfterMinutes(null), 1, "missing header means 1 min");
eq(retryAfterMinutes("soon"), 1, "garbage means 1 min");

// 6. Live ids reach the live round, every other id the archive. Both encoded.
eq(recordingUrl("2026-05-25_002432_Gorodok_RAAS_v1_40323435"),
   "./api/recording/2026-05-25_002432_Gorodok_RAAS_v1_40323435", "plain id");
eq(recordingUrl("@live:2026-10-05_120000_X"), "./api/live/round/2026-10-05_120000_X", "live id");
eq(recordingUrl("@live:a b/c"), "./api/live/round/a%20b%2Fc", "live id is encoded");
eq(recordingUrl("a b"), "./api/recording/a%20b", "plain id is encoded");
ok(isLiveId("@live:x") && !isLiveId("x") && !isLiveId(null) && !isLiveId(""), "isLiveId");

// 7. The live edge.
{
  const base = { playing: true, stalled: false, speed: 1, lagMs: LIVE_DELAY_MS,
                 pausedForBuffer: false };
  eq(edgeStep(base), null, "steady at the edge: nothing to do");
  eq(edgeStep({ ...base, speed: 4 }), "slow", "caught up at 4x: back to 1x");
  eq(edgeStep({ ...base, speed: 4, lagMs: 60_000 }), null, "far behind at 4x: keep going");
  eq(edgeStep({ ...base, speed: 4, playing: false }), null, "paused at 4x: leave the speed");
  eq(edgeStep({ ...base, stalled: true, lagMs: 0 }), "buffer", "hit the edge: rebuffer");
  eq(edgeStep({ ...base, playing: false, stalled: true, pausedForBuffer: true, lagMs: 2000 }),
     null, "still rebuffering");
  eq(edgeStep({ ...base, playing: false, stalled: true, pausedForBuffer: true }),
     "resume", "8 s ahead again: play on");
  eq(edgeStep({ ...base, playing: false, stalled: true, lagMs: 60_000 }),
     "unstall", "a pause the user pressed stays; only the banner goes");
  eq(edgeStep({ ...base, stalled: true, lagMs: 60_000 }),
     "resume", "playing from far back: clear the stale stall");
  ok(isAtLive(true, LIVE_DELAY_MS + 3000), "within 3 s of the edge is live");
  ok(!isAtLive(true, LIVE_DELAY_MS + 3001), "further back is not");
  ok(!isAtLive(false, 0), "paused is not live");
}

// 8. When the round is over: at its end, or at once if nothing of it ever came.
ok(shouldAdvanceRound(0, 0, true), "no frames (the stream was 404): go now");
ok(shouldAdvanceRound(10, 9, false), "stopped at the last frame: go");
ok(!shouldAdvanceRound(10, 9, true), "still playing out: wait");
ok(!shouldAdvanceRound(10, 3, false), "paused further back: leave the moderator be");

// 9. The live axis is "match start to newest frame", never the loaded window.
{
  const t0 = Date.parse("2026-10-05T12:00:00Z");
  const fr = (ms: number[]) => ms.map((m) => ({ timestamp: new Date(m).toISOString() })) as any[];
  const st = () => useViewerStore.getState();
  st().openReplay("@live:r");
  st().restartReplayAt(t0 + 60_000);
  st().setReplayTiming({ startMs: t0, durationMs: 0 });
  st().appendReplayFrames(fr([t0 + 61_000, t0 + 70_000]));
  st().finishReplayLoad({ truncated: true });
  eq(st().replay.totalMs, 0, "live: no inferred total after a drop");
  eq(st().replay.matchStartMs, t0, "live: match start kept");
  const before = st().replay.bufferedMs - st().replay.matchStartMs;
  st().restartReplayAt(t0 + 80_000);
  st().appendReplayFrames(fr([t0 + 81_000, t0 + 90_000]));
  ok(st().replay.bufferedMs - st().replay.matchStartMs > before, "live: axis keeps growing after reconnect");

  st().openReplay("2026-01-01_000000_X");
  st().setReplayTiming({ startMs: t0, durationMs: 0 });
  st().restartReplayAt(t0 + 60_000);
  st().appendReplayFrames(fr([t0 + 61_000, t0 + 90_000]));
  st().finishReplayLoad();
  eq(st().replay.totalMs, 90_000, "recording: inferred total is measured from match start");
}

console.log(`\nlive client tests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

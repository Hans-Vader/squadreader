// Standalone unit test for the live stream decoder. Bundled with esbuild and
// run under node, no framework (matches replayReconstruct.test.mts).
import { createLiveFeed, edgeStep, isAtLive, retryAfterMinutes, shouldAdvanceRound } from "./client.ts";
import { isLiveId, LIVE_DELAY_MS, recordingUrl } from "../api/recordings.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) {
  ok(a === b, `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);
}

const full = (tick: number, x: number) => JSON.stringify({
  timestamp: `2026-01-01T00:00:0${tick}+00:00`, tick,
  players: [{ name: "Alice", eosId: "eos-a", teamId: 1,
              soldier: { addr: "0x1", position: { x, y: 0, z: 0 }, health: 100, yaw: 0 } }],
  vehicles: [], damageEvents: [{ killed: true }], gameState: { matchState: "InProgress" },
});
const pos = (tick: number, x: number) => JSON.stringify({
  t: "pos", tick, timestamp: `2026-01-01T00:00:0${tick}.5+00:00`,
  players: [{ id: "eos-a", x, y: 0 }], vehicles: [],
});

// 1. Garbage and non-objects are dropped.
{
  const f = createLiveFeed();
  eq(f.push("not json"), null, "garbage dropped");
  eq(f.push("[1,2]"), null, "array dropped");
  eq(f.push("42"), null, "number dropped");
  eq(f.push("null"), null, "null dropped");
}

// 2. A position frame before the first full frame is dropped.
{
  const f = createLiveFeed();
  eq(f.push(pos(1, 5)), null, "orphan position frame dropped");
}

// 3. Full frames pass through; position frames after them move players and
//    carry no kill events (those were delivered on the full frame).
{
  const f = createLiveFeed();
  const a = f.push(full(1, 10));
  eq(a?.tick, 1, "full frame returned");
  eq(a?.damageEvents.length, 1, "full frame keeps its kill events");
  const b = f.push(pos(2, 12));
  eq(b?.players[0]?.soldier?.position?.x, 12, "position frame moves Alice");
  eq(b?.damageEvents.length, 0, "position frame carries no kill events");
}

// 4. A new feed (a reconnect) starts without a base frame.
{
  const f1 = createLiveFeed();
  f1.push(full(1, 10));
  const f2 = createLiveFeed();
  eq(f2.push(pos(2, 12)), null, "fresh feed has no base frame");
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

console.log(`\nlive client tests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

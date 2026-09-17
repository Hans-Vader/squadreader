// Standalone unit test for the cap-zone label. Bundled with esbuild and run
// under node — no test framework needed.
import { capLabel } from "./draw.ts";

let passed = 0, failed = 0;
function eq(a: any, b: any, msg: string) {
  if (a === b) { passed++; }
  else { failed++; console.error(`  FAIL: ${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`); }
}

// The game's own flag text wins over everything else it is shipped with.
eq(capLabel({ name: "04-NivaUpper_0", flagName: "Border Crossing",
              staticName: "Niva Upper" }), "Border Crossing",
   "flagName beats a stale actor name and the static name derived from it");
eq(capLabel({ name: "A1-BP_CaptureZoneCluster_0", flagName: "Kuznica Station" }),
   "Kuznica Station", "flagName beats a placeholder actor name");

// Without it, nothing changes: SquadCalc's name, then the cleaned actor name.
eq(capLabel({ name: "E1-TrainStation", staticName: "Train Station" }),
   "Train Station", "staticName is still used when there is no flagName");
eq(capLabel({ name: "E1-TrainStation_0", flagName: "" }), "TrainStation_0",
   "an empty flagName falls through to the live name");
eq(capLabel({ name: "A1-BP_CaptureZoneCluster_0" }), "A1",
   "the UE class suffix is still stripped");
eq(capLabel({ name: null, flagName: null, staticName: null }), "",
   "nothing at all is an empty label, not a crash");

console.log(`\ncap label tests: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

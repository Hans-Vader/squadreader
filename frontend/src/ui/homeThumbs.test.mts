// The landing grid's thumbnail URL is built from the fallback table's
// `texture` (Home.tsx) and the file is written by scripts/gen_map_assets.py
// from the same value. Nothing enforces that contract at build time: Vite's
// glob just yields undefined for a key it has no file for, and the tile
// quietly renders as a blank dark card. So check it here — rename a texture
// or forget to re-run the generator and this fails instead of production.
import { existsSync, statSync } from "node:fs";
import { join } from "node:path";
import { MAP_FALLBACKS } from "../canvas/mapFallbackData.ts";
import { CUSTOM_MAPS } from "../canvas/mapCustomData.ts";
import { fallbackMap } from "../canvas/mapFallback.ts";

// Stock maps and the operator's modded ones are one lookup, so one check.
const ALL = [...MAP_FALLBACKS, ...CUSTOM_MAPS] as [string, { texture: string }][];

let passed = 0, failed = 0;
function ok(cond: unknown, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}

// cwd is frontend/ (npm test). Fail loudly rather than pass vacuously if not.
const dir = join(process.cwd(), "src", "assets", "thumbs");
if (!existsSync(dir)) {
  console.error(`missing thumbnail dir ${dir} — run scripts/gen_map_thumbs.py`);
  process.exit(1);
}

// --- every map the lookup can name has a thumbnail --------------------------
for (const [id, v] of ALL) {
  const f = join(dir, `${v.texture}.webp`);
  ok(existsSync(f), `${id}: no thumbnail for texture "${v.texture}"`);
}

// --- and they are thumbnails, not copies of the 2.5-6.4 MB sqmaps -----------
for (const [id, v] of ALL) {
  const f = join(dir, `${v.texture}.webp`);
  if (!existsSync(f)) continue;
  const kb = statSync(f).size / 1024;
  ok(kb < 100, `${id}: thumbnail is ${kb.toFixed(0)} KB — regenerate it`);
}

// --- the chain a real recording takes: layer name → texture → file ----------
for (const [layer, texture] of [
  ["Narva RAAS v1", "Narva"],
  ["SEC 26 Sumari AAS v1", "Sumari"],
  ["Gorodok Invasion v1", "Gorodok_fmodel"],
  ["Hrodna Border RAAS v1", "HrodnaBorder"],   // modded, from custom_maps.json
  ["Hrodna_Border_Invasion_v2", "HrodnaBorder"],
] as [string, string][]) {
  const m = fallbackMap(layer);
  ok(m?.texture === texture, `${layer} → ${m?.texture ?? "null"}, expected ${texture}`);
  ok(m && existsSync(join(dir, `${m.texture}.webp`)), `${layer}: thumbnail missing`);
}

console.log(`home thumbnails: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

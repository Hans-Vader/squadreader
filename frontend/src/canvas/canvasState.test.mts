// Standalone unit test for the renderer's save()/restore() replacement. Run by
// scripts/run-tests.mjs (every *.test.mts under src/).
import { restoreState, resetStateStack, saveState, textWidth } from "./canvasState.ts";

let passed = 0, failed = 0;
function ok(cond: any, msg: string) {
  if (cond) { passed++; } else { failed++; console.error("  FAIL:", msg); }
}
function eq(a: any, b: any, msg: string) { ok(JSON.stringify(a) === JSON.stringify(b), `${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`); }

function fakeCtx(): any {
  let dash: number[] = [];
  return {
    fillStyle: "#000000", strokeStyle: "#000000", lineWidth: 1, lineCap: "butt",
    lineJoin: "miter", globalAlpha: 1, globalCompositeOperation: "source-over",
    shadowColor: "rgba(0, 0, 0, 0)", shadowBlur: 0, shadowOffsetX: 0, shadowOffsetY: 0,
    font: "10px sans-serif", textAlign: "start", textBaseline: "alphabetic",
    lineDashOffset: 0, transforms: [] as number[][], measured: 0,
    getLineDash() { return dash.slice(); },
    setLineDash(d: number[]) { dash = d.slice(); },
    setTransform(...m: number[]) { this.transforms.push(m); },
    measureText(t: string) { this.measured++; return { width: t.length * 7 }; },
  };
}

// one level: everything the block changed comes back, and the transform resets
{
  const c = fakeCtx();
  c.fillStyle = "#d84a54"; c.font = "bold 11px system-ui";
  saveState(c);
  c.fillStyle = "#ffffff"; c.globalAlpha = 0.4; c.shadowBlur = 9; c.shadowColor = "rgba(0,0,0,0.55)";
  c.setLineDash([6, 4]); c.lineWidth = 3; c.font = "12px x"; c.textAlign = "center";
  c.globalCompositeOperation = "multiply";
  restoreState(c);
  eq([c.fillStyle, c.globalAlpha, c.shadowBlur, c.shadowColor, c.lineWidth, c.font,
      c.textAlign, c.globalCompositeOperation],
     ["#d84a54", 1, 0, "rgba(0, 0, 0, 0)", 1, "bold 11px system-ui", "start", "source-over"],
     "state restored");
  eq(c.getLineDash(), [], "line dash restored");
  eq(c.transforms.at(-1), [1, 0, 0, 1, 0, 0], "transform reset to identity");
}

// nested, like the marker arrows: inner restore returns to the outer block's state
{
  const c = fakeCtx();
  saveState(c);
  c.fillStyle = "#111111";
  saveState(c);
  c.fillStyle = "#222222";
  restoreState(c);
  eq(c.fillStyle, "#111111", "inner restore");
  restoreState(c);
  eq(c.fillStyle, "#000000", "outer restore");
}

// an unbalanced restore is ignored, and a frame starts from an empty stack
{
  const c = fakeCtx();
  restoreState(c);
  eq(c.fillStyle, "#000000", "restore with nothing saved changes nothing");
  saveState(c); c.fillStyle = "#333333";
  resetStateStack();
  restoreState(c);
  eq(c.fillStyle, "#333333", "after a reset there is nothing to restore");
}

// text widths are measured once per font and text
{
  const c = fakeCtx();
  c.font = "bold 11px system-ui";
  eq(textWidth(c, "Shadow +1"), 63, "width");
  textWidth(c, "Shadow +1"); textWidth(c, "Shadow +1");
  eq(c.measured, 1, "measured once");
  c.font = "bold 12px system-ui";
  textWidth(c, "Shadow +1");
  eq(c.measured, 2, "a new font is measured again");
}

console.log(`\ncanvasState: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);

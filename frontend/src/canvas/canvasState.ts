// Canvas state and text widths for the map renderer, kept off the Blink heap.
// ---- canvas state without ctx.save() --------------------------------------
// Chrome copies the whole 2D state into a new garbage-collected object on every
// save(): 772 bytes of Blink heap per save/restore pair, measured. The map
// draws a few hundred pairs per frame at 60 fps, so playback produced ~22 MB/s
// of garbage that Chrome collected late - a fast-forward through a dense
// recording grew the tab past 2.4 GB and ended in an out-of-memory page. The
// state the renderer changes is kept on a reused plain-JS stack instead
// (~50 bytes per pair). The base transform is identity everywhere in this
// renderer, so restoring it is resetting it. A block that clips still uses the
// real save(): a clip cannot be undone any other way.
interface SavedState {
  fill: string | CanvasGradient | CanvasPattern;
  stroke: string | CanvasGradient | CanvasPattern;
  lineWidth: number;
  lineCap: CanvasLineCap;
  lineJoin: CanvasLineJoin;
  alpha: number;
  composite: GlobalCompositeOperation;
  shadowColor: string;
  shadowBlur: number;
  shadowX: number;
  shadowY: number;
  font: string;
  textAlign: CanvasTextAlign;
  textBaseline: CanvasTextBaseline;
  dash: number[];
  dashOffset: number;
}
const STATE_STACK: SavedState[] = [];
let stateDepth = 0;

export function saveState(ctx: CanvasRenderingContext2D): void {
  const s = (STATE_STACK[stateDepth] ??= {} as SavedState);
  stateDepth++;
  s.fill = ctx.fillStyle; s.stroke = ctx.strokeStyle;
  s.lineWidth = ctx.lineWidth; s.lineCap = ctx.lineCap; s.lineJoin = ctx.lineJoin;
  s.alpha = ctx.globalAlpha; s.composite = ctx.globalCompositeOperation;
  s.shadowColor = ctx.shadowColor; s.shadowBlur = ctx.shadowBlur;
  s.shadowX = ctx.shadowOffsetX; s.shadowY = ctx.shadowOffsetY;
  s.font = ctx.font; s.textAlign = ctx.textAlign; s.textBaseline = ctx.textBaseline;
  s.dash = ctx.getLineDash(); s.dashOffset = ctx.lineDashOffset;
}

export function restoreState(ctx: CanvasRenderingContext2D): void {
  if (stateDepth === 0) return;
  const s = STATE_STACK[--stateDepth]!;
  ctx.fillStyle = s.fill; ctx.strokeStyle = s.stroke;
  ctx.lineWidth = s.lineWidth; ctx.lineCap = s.lineCap; ctx.lineJoin = s.lineJoin;
  ctx.globalAlpha = s.alpha; ctx.globalCompositeOperation = s.composite;
  ctx.shadowColor = s.shadowColor; ctx.shadowBlur = s.shadowBlur;
  ctx.shadowOffsetX = s.shadowX; ctx.shadowOffsetY = s.shadowY;
  ctx.font = s.font; ctx.textAlign = s.textAlign; ctx.textBaseline = s.textBaseline;
  ctx.setLineDash(s.dash); ctx.lineDashOffset = s.dashOffset;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

// measureText() allocates a TextMetrics object on the Blink heap (226 bytes,
// measured) for every label, every frame. The widths repeat - the same names,
// numbers and squad labels in the same few fonts - so they are kept.
const TEXT_WIDTH = new Map<string, number>();
/** A frame always starts from an empty stack, whatever an earlier one left. */
export function resetStateStack(): void {
  stateDepth = 0;
}

export function textWidth(ctx: CanvasRenderingContext2D, text: string): number {
  const key = ctx.font + "\u0000" + text;
  let w = TEXT_WIDTH.get(key);
  if (w === undefined) {
    if (TEXT_WIDTH.size > 4000) TEXT_WIDTH.clear();
    w = ctx.measureText(text).width;
    TEXT_WIDTH.set(key, w);
  }
  return w;
}

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { spanAt } from "../lib/spans.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

/**
 * Renders a PDF page with PDF.js and overlays clickable span boxes.
 *
 * Props:
 *   data        ArrayBuffer | Uint8Array — PDF bytes to render
 *   pageIndex   0-based page to show
 *   spans       array of span objects (with .id, .page, .bbox)
 *   selectedId  currently selected span id (indigo outline)
 *   editedIds   Set of edited span ids (green outline)
 *   onSelect    (spanId|null) => void  — fired on click
 *   maxWidth    optional cap on rendered width (px)
 *   highlightRects  array of { key, x0,y0,x1,y1, page, variant } in PDF points —
 *                   full-area highlights (e.g. a whole table row), drawn over the page
 */
export default function PdfCanvas({
  data,
  pageIndex = 0,
  spans = [],
  selectedId = null,
  editedIds = new Set(),
  onSelect = () => {},
  maxWidth = 900,
  highlightRects = [],
  // ---- Added-content overlays (free text + signatures) ----
  overlays = [],
  overlaySelectedId = null,
  onOverlaySelect = () => {},
  onOverlayChange = () => {},
  onOverlayDelete = () => {},
  fonts = [],
  placement = null, // null | {kind:'text'} | {kind:'sign', data, ratio}
  onPlace = () => {},
  // ---- Move existing text spans to a new position ----
  moves = {}, // { spanId: {x,y} } new top-left in PDF points
  edits = {}, // { spanId: newText } — so a moved+edited span shows the new text
  onSpanMove = () => {},
  onSpanMoveClear = () => {},
}) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const renderTaskRef = useRef(null);
  const drag = useRef(null); // { id, mode:'move'|'resize', sx, sy, ox, oy, ow, ratio }
  const spanDrag = useRef(null); // { id, sx, sy, ox, oy } — dragging an existing span
  const [scale, setScale] = useState(1); // CSS px per PDF point
  const [dims, setDims] = useState({ w: 0, h: 0 }); // CSS pixel size
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!data) return;

    async function render() {
      setErr(null);
      setLoading(true);
      try {
        // Clone the bytes: pdf.js may detach the underlying ArrayBuffer.
        const bytes =
          data instanceof Uint8Array ? data.slice() : new Uint8Array(data.slice(0));
        const pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
        const page = await pdf.getPage(pageIndex + 1);

        const unscaled = page.getViewport({ scale: 1 });
        const fit = Math.min(maxWidth / unscaled.width, 2.2);
        const cssScale = Math.max(fit, 0.2);
        const viewport = page.getViewport({ scale: cssScale });
        const dpr = window.devicePixelRatio || 1;

        if (cancelled) return;
        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = Math.floor(viewport.width * dpr);
        canvas.height = Math.floor(viewport.height * dpr);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;

        const ctx = canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        if (renderTaskRef.current) {
          try { renderTaskRef.current.cancel(); } catch { /* noop */ }
        }
        const task = page.render({ canvasContext: ctx, viewport });
        renderTaskRef.current = task;
        await task.promise;

        if (cancelled) return;
        setScale(cssScale);
        setDims({ w: viewport.width, h: viewport.height });
      } catch (e) {
        if (!cancelled && e?.name !== "RenderingCancelledException") {
          setErr("Couldn't render this PDF page.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    render();
    return () => {
      cancelled = true;
      if (renderTaskRef.current) {
        try { renderTaskRef.current.cancel(); } catch { /* noop */ }
      }
    };
  }, [data, pageIndex, maxWidth]);

  // Drag / resize overlays. Listeners live for the component's life and read
  // the drag ref, so there's no add/remove churn or stale-closure leak.
  useEffect(() => {
    function move(e) {
      if (!scale) return;
      const sd = spanDrag.current;
      if (sd) {
        const dx = (e.clientX - sd.sx) / scale;
        const dy = (e.clientY - sd.sy) / scale;
        if (!sd.moved && Math.hypot(e.clientX - sd.sx, e.clientY - sd.sy) < 3) return; // ignore tiny jitter
        sd.moved = true;
        onSpanMove(sd.id, sd.ox + dx, sd.oy + dy);
        return;
      }
      const d = drag.current;
      if (!d) return;
      const dx = (e.clientX - d.sx) / scale;
      const dy = (e.clientY - d.sy) / scale;
      if (d.mode === "move") onOverlayChange(d.id, { x: d.ox + dx, y: d.oy + dy });
      else {
        const w = Math.max(16, d.ow + dx);
        onOverlayChange(d.id, { w, h: w / (d.ratio || 1) });
      }
    }
    function up() {
      drag.current = null;
      spanDrag.current = null;
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [scale, onOverlayChange, onSpanMove]);

  // Read the rendered-canvas background colour at a PDF point (to preview the
  // erase over a span's original spot the same way the backend samples it).
  const sampleBg = (px, py) => {
    const canvas = canvasRef.current;
    if (!canvas || !scale) return "#ffffff";
    const dpr = window.devicePixelRatio || 1;
    const x = Math.max(0, Math.min(canvas.width - 1, Math.round(px * scale * dpr)));
    const y = Math.max(0, Math.min(canvas.height - 1, Math.round(py * scale * dpr)));
    try {
      const d = canvas.getContext("2d").getImageData(x, y, 1, 1).data;
      return `rgb(${d[0]},${d[1]},${d[2]})`;
    } catch {
      return "#ffffff";
    }
  };

  const startMove = (e, o) => {
    e.stopPropagation();
    onOverlaySelect(o.id);
    drag.current = { id: o.id, mode: "move", sx: e.clientX, sy: e.clientY, ox: o.x, oy: o.y };
  };
  const startResize = (e, o) => {
    e.stopPropagation();
    drag.current = {
      id: o.id, mode: "resize", sx: e.clientX, sy: e.clientY,
      ow: o.w, ratio: o.h ? o.w / o.h : 1,
    };
  };
  const startSpanMove = (e, s) => {
    e.stopPropagation();
    const cur = moves[s.id];
    const [x0, y0] = s.bbox;
    spanDrag.current = {
      id: s.id, sx: e.clientX, sy: e.clientY,
      ox: cur ? cur.x : x0, oy: cur ? cur.y : y0, moved: false,
    };
    onSelect(s.id);
  };
  const spanStyle = (s) => {
    const fl = s.flags || 0;
    const fn = (s.font || "").toLowerCase();
    const c = Array.isArray(s.color) ? s.color : [0.1, 0.1, 0.1];
    return {
      fontWeight: (fl & 16) !== 0 || /bold|black|heavy|semibold/.test(fn) ? 700 : 400,
      fontStyle: (fl & 2) !== 0 || /italic|oblique/.test(fn) ? "italic" : "normal",
      color: `rgb(${c.slice(0, 3).map((v) => Math.round(v * 255)).join(",")})`,
    };
  };

  function handleClick(e) {
    const wrap = wrapRef.current;
    if (!wrap || !scale) return;
    const rect = wrap.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / scale;
    const fy = (e.clientY - rect.top) / scale;
    if (placement) {
      onPlace(fx, fy);
      return;
    }
    onOverlaySelect(null); // click on the page clears any overlay selection
    onSelect(spanAt(spans, pageIndex, fx, fy));
  }

  const pageSpans = spans.filter((s) => s.page === pageIndex);

  return (
    <div className="relative inline-block">
      {err && (
        <div className="p-6 text-sm text-red-600 bg-red-50 rounded-lg border border-red-100">
          ⚠️ {err}
        </div>
      )}
      <div
        ref={wrapRef}
        onClick={handleClick}
        className={`relative select-none ${placement ? "cursor-crosshair" : "cursor-pointer"}`}
        style={{ width: dims.w || undefined, height: dims.h || undefined }}
      >
        <canvas ref={canvasRef} className="block rounded-lg shadow-card" />

        {/* Highlight overlay — pointer-events are passed through to the wrapper */}
        <div className="absolute inset-0 pointer-events-none">
          {/* Full-area band highlights (whole rows / sections) */}
          {highlightRects
            .filter((r) => (r.page ?? 0) === pageIndex)
            .map((r) => {
              const styles = {
                faint: { border: "1px solid rgba(37,99,235,.30)", background: "transparent" },
                strong: { border: "2px solid #2563eb", background: "rgba(37,99,235,.12)" },
                parent: { border: "2px dashed #06b6d4", background: "rgba(6,182,212,.07)" },
              };
              const st = styles[r.variant] || styles.faint;
              return (
                <div
                  key={r.key}
                  className="absolute rounded-[4px]"
                  style={{
                    left: r.x0 * scale,
                    top: r.y0 * scale,
                    width: (r.x1 - r.x0) * scale,
                    height: (r.y1 - r.y0) * scale,
                    ...st,
                    transition: "background-color .15s ease, border-color .15s ease",
                  }}
                />
              );
            })}

          {pageSpans.map((s) => {
            const [x0, y0, x1, y1] = s.bbox;
            const isSel = s.id === selectedId;
            const isEdited = editedIds.has(s.id);
            if (moves[s.id]) return null; // moved spans are drawn in the moves layer
            if (!isSel && !isEdited) return null;
            return (
              <div
                key={s.id}
                onPointerDown={isSel ? (e) => startSpanMove(e, s) : undefined}
                title={isSel ? "Drag to move this text" : undefined}
                className="absolute rounded-[2px] animate-pop"
                style={{
                  left: x0 * scale,
                  top: y0 * scale,
                  width: (x1 - x0) * scale,
                  height: (y1 - y0) * scale,
                  outline: "2px solid #2563eb",
                  background: isSel ? "rgba(37,99,235,.12)" : "rgba(37,99,235,.05)",
                  pointerEvents: isSel ? "auto" : "none",
                  cursor: isSel ? "move" : "default",
                  transition: "background-color .15s ease, outline-color .15s ease",
                }}
              />
            );
          })}
        </div>

        {/* Moved text spans: cover the original spot (bg-sampled) + draw the text
            at its new position (interactive: drag again, or reset). */}
        <div className="absolute inset-0 pointer-events-none">
          {pageSpans
            .filter((s) => moves[s.id])
            .map((s) => {
              const [x0, y0, x1, y1] = s.bbox;
              const pos = moves[s.id];
              const sel = s.id === selectedId;
              const text = edits[s.id] ?? s.text;
              return (
                <div key={`mv-${s.id}`}>
                  {/* erase preview over the original location */}
                  <div
                    className="absolute"
                    style={{
                      left: (x0 - 1) * scale,
                      top: (y0 - 1) * scale,
                      width: (x1 - x0 + 2) * scale,
                      height: (y1 - y0 + 2) * scale,
                      background: sampleBg(x0 + 0.5, y0 + 0.5),
                    }}
                  />
                  {/* the text at its new position */}
                  <div
                    onPointerDown={(e) => startSpanMove(e, s)}
                    onClick={(e) => e.stopPropagation()}
                    title="Drag to move · click ✕ to reset"
                    className="absolute pointer-events-auto cursor-move whitespace-pre"
                    style={{
                      left: pos.x * scale,
                      top: pos.y * scale,
                      fontSize: Math.max(6, s.size * scale),
                      lineHeight: 1.05,
                      fontFamily: "Inter, system-ui, sans-serif",
                      ...spanStyle(s),
                      outline: sel ? "1.5px solid #2563eb" : "1px dashed rgba(37,99,235,.5)",
                      background: sel ? "rgba(37,99,235,.06)" : "transparent",
                      padding: "0 1px",
                      borderRadius: 2,
                    }}
                  >
                    {text || " "}
                    {sel && (
                      <button
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => {
                          e.stopPropagation();
                          onSpanMoveClear(s.id);
                        }}
                        title="Reset to original position"
                        className="absolute -top-3 -right-3 w-5 h-5 rounded-full bg-error text-white text-[11px] leading-none flex items-center justify-center shadow"
                      >
                        ×
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
        </div>

        {/* Added-content overlays (interactive: place / drag / resize / edit) */}
        <div className="absolute inset-0 pointer-events-none">
          {overlays
            .filter((o) => (o.page ?? 0) === pageIndex)
            .map((o) => {
              const sel = o.id === overlaySelectedId;
              if (o.kind === "text") {
                return (
                  <div
                    key={o.id}
                    onPointerDown={(e) => startMove(e, o)}
                    onClick={(e) => e.stopPropagation()}
                    className="absolute pointer-events-auto cursor-move"
                    style={{ left: o.x * scale, top: o.y * scale }}
                  >
                    <div
                      style={{
                        fontSize: Math.max(6, o.size * scale),
                        color: o.color,
                        lineHeight: 1.05,
                        whiteSpace: "pre",
                        fontFamily: "Inter, system-ui, sans-serif",
                        fontWeight: o.bold ? 700 : 400,
                        fontStyle: o.italic ? "italic" : "normal",
                        padding: "1px 2px",
                        borderRadius: 2,
                        outline: sel ? "1.5px solid #2563eb" : "1px dashed rgba(37,99,235,.45)",
                        background: sel ? "rgba(37,99,235,.06)" : "transparent",
                      }}
                    >
                      {o.text || "Text"}
                    </div>
                    {sel && (
                      <div
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => e.stopPropagation()}
                        className="absolute -top-10 left-0 flex items-center gap-1 bg-surface-container-high border border-outline-variant/50 rounded-lg px-1.5 py-1 shadow-xl z-30 whitespace-nowrap"
                      >
                        <input
                          value={o.text}
                          autoFocus
                          onChange={(e) => onOverlayChange(o.id, { text: e.target.value })}
                          placeholder="Type text"
                          className="w-28 bg-surface-container-lowest border border-outline-variant/40 rounded px-1.5 py-0.5 text-[12px] text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container"
                        />
                        <select
                          value={o.font || ""}
                          onChange={(e) => onOverlayChange(o.id, { font: e.target.value })}
                          title="Font"
                          className="max-w-[104px] bg-surface-container-lowest border border-outline-variant/40 rounded px-1 py-0.5 text-[11px] text-on-surface focus:outline-none"
                        >
                          {fonts.map((f) => (
                            <option key={f} value={f}>
                              {f.split("+").pop()}
                            </option>
                          ))}
                        </select>
                        <button
                          onClick={() => onOverlayChange(o.id, { bold: !o.bold })}
                          title="Bold"
                          className={`w-5 h-5 rounded text-[12px] font-bold transition-colors ${
                            o.bold
                              ? "bg-secondary-container text-white"
                              : "text-on-surface-variant hover:bg-surface-container-highest"
                          }`}
                        >
                          B
                        </button>
                        <button
                          onClick={() => onOverlayChange(o.id, { italic: !o.italic })}
                          title="Italic"
                          className={`w-5 h-5 rounded text-[12px] italic font-serif transition-colors ${
                            o.italic
                              ? "bg-secondary-container text-white"
                              : "text-on-surface-variant hover:bg-surface-container-highest"
                          }`}
                        >
                          I
                        </button>
                        <button
                          onClick={() => onOverlayChange(o.id, { size: Math.max(6, o.size - 1) })}
                          className="w-5 h-5 rounded text-on-surface-variant hover:bg-surface-container-highest"
                        >
                          −
                        </button>
                        <span className="text-[11px] w-5 text-center tabular-nums text-on-surface-variant">
                          {Math.round(o.size)}
                        </span>
                        <button
                          onClick={() => onOverlayChange(o.id, { size: Math.min(96, o.size + 1) })}
                          className="w-5 h-5 rounded text-on-surface-variant hover:bg-surface-container-highest"
                        >
                          +
                        </button>
                        <button
                          onClick={() => onOverlayDelete(o.id)}
                          title="Delete"
                          className="w-6 h-6 rounded text-error hover:bg-error/10 flex items-center justify-center"
                        >
                          <span className="material-symbols-outlined text-[15px]">delete</span>
                        </button>
                      </div>
                    )}
                  </div>
                );
              }
              // Signature image
              return (
                <div
                  key={o.id}
                  onPointerDown={(e) => startMove(e, o)}
                  onClick={(e) => e.stopPropagation()}
                  className="absolute pointer-events-auto cursor-move"
                  style={{
                    left: o.x * scale,
                    top: o.y * scale,
                    width: o.w * scale,
                    height: o.h * scale,
                  }}
                >
                  <img
                    src={o.data}
                    draggable={false}
                    alt="signature"
                    className="w-full h-full object-contain select-none pointer-events-none"
                    style={{
                      outline: sel ? "1.5px solid #2563eb" : "1px dashed rgba(37,99,235,.35)",
                      borderRadius: 2,
                    }}
                  />
                  {sel && (
                    <>
                      <button
                        onPointerDown={(e) => e.stopPropagation()}
                        onClick={(e) => {
                          e.stopPropagation();
                          onOverlayDelete(o.id);
                        }}
                        title="Delete"
                        className="absolute -top-3 -right-3 w-6 h-6 rounded-full bg-surface-container-high border border-outline-variant/50 text-error shadow-md flex items-center justify-center hover:bg-error/10 z-30"
                      >
                        <span className="material-symbols-outlined text-[15px]">close</span>
                      </button>
                      <div
                        onPointerDown={(e) => startResize(e, o)}
                        title="Resize"
                        className="absolute -bottom-2 -right-2 w-4 h-4 rounded-full bg-secondary-container border-2 border-white shadow cursor-nwse-resize z-30"
                      />
                    </>
                  )}
                </div>
              );
            })}
        </div>

        {placement && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 z-40 bg-secondary-container text-white rounded-full px-3 py-1 text-[12px] shadow-xl flex items-center gap-1.5 pointer-events-none animate-fade">
            <span className="material-symbols-outlined text-[15px]">ads_click</span>
            Click where you want your {placement.kind === "sign" ? "signature" : "text"}
          </div>
        )}

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60 text-sm text-muted">
            Rendering…
          </div>
        )}
      </div>
    </div>
  );
}

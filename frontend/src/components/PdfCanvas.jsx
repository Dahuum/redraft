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
}) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const renderTaskRef = useRef(null);
  const drag = useRef(null); // { id, mode:'move'|'resize', sx, sy, ox, oy, ow, ratio }
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
      const d = drag.current;
      if (!d || !scale) return;
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
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [scale, onOverlayChange]);

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
            if (!isSel && !isEdited) return null;
            return (
              <div
                key={s.id}
                className="absolute rounded-[2px] animate-pop"
                style={{
                  left: x0 * scale,
                  top: y0 * scale,
                  width: (x1 - x0) * scale,
                  height: (y1 - y0) * scale,
                  outline: isSel
                    ? "2px solid #2563eb"
                    : "2px solid #2563eb",
                  background: isSel ? "rgba(37,99,235,.12)" : "rgba(37,99,235,.05)",
                  transition: "background-color .15s ease, outline-color .15s ease",
                }}
              />
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

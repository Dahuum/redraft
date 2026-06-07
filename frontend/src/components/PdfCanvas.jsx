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
 */
export default function PdfCanvas({
  data,
  pageIndex = 0,
  spans = [],
  selectedId = null,
  editedIds = new Set(),
  onSelect = () => {},
  maxWidth = 900,
}) {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const renderTaskRef = useRef(null);
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

  function handleClick(e) {
    const wrap = wrapRef.current;
    if (!wrap || !scale) return;
    const rect = wrap.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / scale;
    const fy = (e.clientY - rect.top) / scale;
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
        className="relative cursor-pointer select-none"
        style={{ width: dims.w || undefined, height: dims.h || undefined }}
      >
        <canvas ref={canvasRef} className="block rounded-lg shadow-card" />

        {/* Highlight overlay — pointer-events are passed through to the wrapper */}
        <div className="absolute inset-0 pointer-events-none">
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

        {loading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60 text-sm text-muted">
            Rendering…
          </div>
        )}
      </div>
    </div>
  );
}

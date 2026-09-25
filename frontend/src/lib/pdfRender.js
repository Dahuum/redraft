// PDF page rendering, built for zooming.
//
//  * The document is opened ONCE per file (not once per zoom step) and its pages are cached.
//  * A sharp bitmap is drawn on a hidden canvas and copied onto the visible one in a single
//    synchronous step, so the visible canvas is never empty and never half-painted.
//  * Zoom steps are debounced, superseded renders are cancelled, and the bitmap size is capped.
//  * Between the zoom click and the sharp bitmap arriving, the picture already on screen is
//    scaled to the new size by the compositor (see useZoomMotion), so nothing flashes.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

const MAX_PIXELS = 24e6; // ~96 MB of pixels per bitmap: keeps very large pages from exhausting memory
const ZOOM_DEBOUNCE = 90; // ms of quiet before the sharp bitmap is drawn
const RESCALE_TOL = 0.03; // skip a redraw when the needed resolution is within 3% of what is shown
const MAX_FIT = 2.2;
const MIN_FIT = 0.2;

export const fitScale = (maxWidth, unscaledWidth) =>
  Math.max(Math.min(maxWidth / unscaledWidth, MAX_FIT), MIN_FIT);

/**
 * Loads `data` once and draws page `pageIndex` onto `canvasRef` at a width of `maxWidth` CSS px.
 * Returns the layout the caller should use immediately (it follows maxWidth with no delay):
 *   { view, cssScale, w, h, painted, err }
 */
export function usePdfRender({ data, pageIndex, maxWidth, canvasRef }) {
  const [docVer, setDocVer] = useState(0);
  const [view, setView] = useState(null); // committed page: { index, uw, uh } (unscaled size in points)
  const [painted, setPainted] = useState(false);
  const [err, setErr] = useState(null);
  const s = useRef({
    doc: null, task: null, pages: new Map(), gen: 0, rt: null,
    backing: 0, off: null, pending: null, committedIndex: -1, committedVer: -1,
  }).current;

  // 1. Open the document once per file.
  useEffect(() => {
    if (!data) return undefined;
    let dead = false;
    // pdf.js transfers (detaches) the bytes it is given, so hand it a copy, once.
    const bytes = data instanceof Uint8Array ? data.slice() : new Uint8Array(data.slice(0));
    const task = pdfjsLib.getDocument({ data: bytes });
    task.promise.then(
      (doc) => {
        if (dead) { doc.destroy(); return; }
        s.gen++; // in-flight renders belong to the old document
        try { s.rt?.cancel(); } catch { /* noop */ }
        const prev = s.doc;
        s.doc = doc; s.task = task; s.pages = new Map();
        if (prev) prev.destroy();
        setDocVer((v) => v + 1);
      },
      (e) => { if (!dead) setErr("Couldn't open this PDF."); }
    );
    return () => { dead = true; if (s.task !== task) task.destroy(); };
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  // 2. Free everything on unmount.
  useEffect(() => () => {
    s.gen++;
    try { s.rt?.cancel(); } catch { /* noop */ }
    if (s.doc) s.doc.destroy();
    s.doc = null; s.pages = new Map(); s.off = null; s.pending = null;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const swap = (off, k) => {
    const c = canvasRef.current;
    if (!c) return;
    if (c.width !== off.width || c.height !== off.height) { c.width = off.width; c.height = off.height; }
    c.getContext("2d").drawImage(off, 0, 0); // same task as the resize, so it is painted as one frame
    s.backing = k;
    setPainted(true);
  };

  // 3. Draw. A new page or document is drawn at once; a zoom change waits for a pause.
  useEffect(() => {
    if (!s.doc || !docVer) return undefined;
    const gen = ++s.gen;
    const doc = s.doc;
    const structural = s.committedVer !== docVer || s.committedIndex !== pageIndex;
    let timer = 0;

    const run = async () => {
      try {
        let page = s.pages.get(pageIndex);
        if (!page) { page = await doc.getPage(pageIndex + 1); s.pages.set(pageIndex, page); }
        if (gen !== s.gen) return;
        const base = page.getViewport({ scale: 1 });
        const cssScale = fitScale(maxWidth, base.width);
        let k = cssScale * (window.devicePixelRatio || 1);
        const px = base.width * k * base.height * k;
        if (px > MAX_PIXELS) k *= Math.sqrt(MAX_PIXELS / px);
        if (!structural && s.backing && Math.abs(k / s.backing - 1) < RESCALE_TOL) return;

        const vp = page.getViewport({ scale: k });
        // A page or document change hands its canvas to React (committed in a layout effect), so it
        // gets its own; zoom redraws swap synchronously and can reuse one hidden canvas.
        const off = structural ? document.createElement("canvas") : (s.off || (s.off = document.createElement("canvas")));
        off.width = Math.max(1, Math.floor(vp.width));
        off.height = Math.max(1, Math.floor(vp.height));
        try { s.rt?.cancel(); } catch { /* noop */ }
        const task = page.render({ canvasContext: off.getContext("2d"), viewport: vp });
        s.rt = task;
        await task.promise;
        if (gen !== s.gen) return;

        if (structural) {
          s.pending = { canvas: off, k, index: pageIndex, ver: docVer };
          setErr(null);
          setView({ index: pageIndex, uw: base.width, uh: base.height });
        } else {
          swap(off, k);
        }
      } catch (e) {
        if (gen === s.gen && e?.name !== "RenderingCancelledException") setErr("Couldn't render this PDF page.");
      }
    };

    if (structural) run();
    else timer = setTimeout(run, ZOOM_DEBOUNCE);
    return () => {
      clearTimeout(timer);
      try { s.rt?.cancel(); } catch { /* noop */ }
    };
  }, [docVer, pageIndex, maxWidth]); // eslint-disable-line react-hooks/exhaustive-deps

  // 4. Commit a new page/document together with its bitmap, before the browser paints.
  useLayoutEffect(() => {
    const p = s.pending;
    if (!p || !view || p.index !== view.index) return;
    s.pending = null;
    swap(p.canvas, p.k);
    s.committedIndex = p.index;
    s.committedVer = p.ver;
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

  const cssScale = view ? fitScale(maxWidth, view.uw) : 0;
  return {
    view, cssScale, painted, err,
    w: view ? view.uw * cssScale : 0,
    h: view ? view.uh * cssScale : 0,
  };
}

const findScroller = (el) => {
  for (let n = el?.parentElement; n; n = n.parentElement) {
    const o = getComputedStyle(n);
    if (/(auto|scroll)/.test(o.overflowY + o.overflowX)) return n;
  }
  return document.scrollingElement;
};

/**
 * Smooth zoom. The layout jumps to its new size at once (overlays are always right); a FLIP
 * animation on the compositor makes the picture glide there from where it was, about the
 * pointer (Ctrl+wheel / pinch) or the middle of the view (buttons). Also turns Ctrl+wheel and
 * trackpad pinch over the board into zoom via onZoomFactor(factor).
 */
export function useZoomMotion({ rootRef, w, h, pageKey, onZoomFactor }) {
  const s = useRef({ box: null, anim: null, origin: { x: 0, y: 0 }, anchor: null, acc: 1, raf: 0, key: null, scroll: null }).current;
  const cb = useRef(onZoomFactor);
  cb.current = onZoomFactor;

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el || !w) return;
    const sc = findScroller(el);
    if (!sc) return;

    // Where is the picture on screen right now? (it may be mid-animation)
    let sCur = 1;
    if (s.anim) {
      const t = getComputedStyle(el).transform;
      if (t && t !== "none") sCur = new DOMMatrixReadOnly(t).a || 1;
      s.anim.cancel();
      s.anim = null;
    }
    const scRect = sc.getBoundingClientRect();
    const r = el.getBoundingClientRect(); // layout box, no transform
    const box = { l: r.left - scRect.left + sc.scrollLeft, t: r.top - scRect.top + sc.scrollTop, w: r.width, h: r.height };
    const prev = s.box;
    const sameDoc = prev && s.key === pageKey;
    s.box = box; s.key = pageKey;
    if (!s.scroll) s.scroll = { l: sc.scrollLeft, t: sc.scrollTop };
    el.style.transformOrigin = "";
    const anchor = s.anchor; s.anchor = null;
    if (!sameDoc || Math.abs(prev.w - box.w) < 0.5) return;

    const vis = { // the old picture, in scroller-content coordinates
      w: prev.w * sCur,
      l: prev.l + s.origin.x * (1 - sCur),
      t: prev.t + s.origin.y * (1 - sCur),
    };
    vis.h = prev.h * sCur;

    // Keep the point under the pointer (or the view centre) fixed while the page resizes.
    const px = anchor ? anchor.px : sc.clientWidth / 2;
    const py = anchor ? anchor.py : sc.clientHeight / 2;
    // Where the container was scrolled BEFORE this layout change. Reading it now would be too late:
    // when the page gets smaller the browser has already shortened the scroll range and clamped it.
    const sL0 = s.scroll ? s.scroll.l : sc.scrollLeft, sT0 = s.scroll ? s.scroll.t : sc.scrollTop;
    const fx = Math.min(1, Math.max(0, (sL0 + px - vis.l) / vis.w));
    const fy = Math.min(1, Math.max(0, (sT0 + py - vis.t) / vis.h));
    sc.scrollLeft = box.l + fx * box.w - px;
    sc.scrollTop = box.t + fy * box.h - py;
    const sL1 = sc.scrollLeft, sT1 = sc.scrollTop; // after the browser has clamped it
    s.scroll = { l: sL1, t: sT1 };

    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const k = vis.w / box.w; // start scale
    if (Math.abs(1 - k) < 0.01) return;
    // The first frame must sit exactly where the old picture was ON SCREEN. Scrolling moved the
    // container, so compare screen positions (content position minus scroll), not content positions.
    const ox = (vis.l - sL0 - (box.l - sL1)) / (1 - k);
    const oy = (vis.t - sT0 - (box.t - sT1)) / (1 - k);
    s.origin = { x: ox, y: oy };
    el.style.transformOrigin = `${ox}px ${oy}px`;
    const a = el.animate([{ transform: `scale(${k})` }, { transform: "scale(1)" }], {
      duration: 170, easing: "cubic-bezier(.2,.7,.2,1)",
    });
    s.anim = a;
    a.onfinish = a.oncancel = () => { if (s.anim === a) { s.anim = null; s.origin = { x: 0, y: 0 }; el.style.transformOrigin = ""; } };
  }, [w, h, pageKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Ctrl+wheel and pinch over the board zoom the page around the pointer.
  useEffect(() => {
    const el = rootRef.current;
    const sc = findScroller(el);
    if (!sc || sc === document.scrollingElement) return undefined;
    const onWheel = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const b = sc.getBoundingClientRect();
      s.anchor = { px: e.clientX - b.left, py: e.clientY - b.top };
      s.acc *= Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0022));
      if (!s.raf) {
        s.raf = requestAnimationFrame(() => { const f = s.acc; s.acc = 1; s.raf = 0; cb.current?.(f); });
      }
    };
    const onScroll = () => { s.scroll = { l: sc.scrollLeft, t: sc.scrollTop }; };
    sc.addEventListener("wheel", onWheel, { passive: false });
    sc.addEventListener("scroll", onScroll, { passive: true });
    return () => { sc.removeEventListener("wheel", onWheel); sc.removeEventListener("scroll", onScroll); cancelAnimationFrame(s.raf); s.raf = 0; };
  }, [w > 0]); // eslint-disable-line react-hooks/exhaustive-deps
}

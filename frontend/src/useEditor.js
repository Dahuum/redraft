import { useCallback, useMemo, useState } from "react";
import { extractSpans, editPdf } from "./api.js";

// Holds the loaded document + edit state and the actions the UI calls.
// Shared by the Editor (edits/preview/download) and the Bulk template.
export function useEditor() {
  const [file, setFile] = useState(null);
  const [fileData, setFileData] = useState(null); // ArrayBuffer (original)
  const [spans, setSpans] = useState([]);
  const [pages, setPages] = useState([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [edits, setEdits] = useState({}); // { id: newText }
  const [previewData, setPreviewData] = useState(null); // ArrayBuffer (edited)
  const [previewUrl, setPreviewUrl] = useState(null);
  const [fontReport, setFontReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [overlays, setOverlays] = useState([]); // added text + signature stamps
  const [moves, setMoves] = useState({}); // { spanId: {x,y} } — repositioned spans (new top-left, PDF pts)

  const nEdits = Object.keys(edits).length;
  const nMoves = Object.keys(moves).length;
  const editedIds = useMemo(() => new Set(Object.keys(edits).map(Number)), [edits]);
  const movedIds = useMemo(() => new Set(Object.keys(moves).map(Number)), [moves]);
  const fonts = useMemo(() => [...new Set(spans.map((s) => s.font))].filter(Boolean), [spans]);
  const hasChanges = nEdits > 0 || overlays.length > 0 || nMoves > 0;

  const resetPreview = useCallback(() => {
    setPreviewUrl((u) => {
      if (u) URL.revokeObjectURL(u);
      return null;
    });
    setPreviewData(null);
    setFontReport(null);
  }, []);

  const loadFile = useCallback(
    async (f) => {
      if (!f) return false;
      setError(null);
      setBusy(true);
      try {
        const buf = await f.arrayBuffer();
        const res = await extractSpans(f);
        setFile(f);
        setFileData(buf);
        setSpans(res.spans);
        setPages(res.pages);
        setPageIndex(0);
        setEdits({});
        setOverlays([]);
        setMoves({});
        resetPreview();
        setSelectedId(res.spans[0] ? res.spans[0].id : null);
        return res;
      } catch (e) {
        setError(e.message || "Couldn't read that PDF.");
        setFile(null);
        setSpans([]);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [resetPreview]
  );

  const setFieldValue = useCallback(
    (id, text) => {
      const sp = spans.find((s) => s.id === id);
      if (!sp) return;
      setEdits((prev) => {
        const next = { ...prev };
        if (text !== sp.text) next[id] = text;
        else delete next[id];
        return next;
      });
      resetPreview();
    },
    [spans, resetPreview]
  );

  const resetAll = useCallback(() => {
    setEdits({});
    setOverlays([]);
    setMoves({});
    resetPreview();
  }, [resetPreview]);

  // ---- Move a text span to a new position (erase original + redraw elsewhere) ----
  const moveSpan = useCallback((id, x, y) => {
    setMoves((m) => ({ ...m, [id]: { x, y } }));
    resetPreview();
  }, [resetPreview]);
  const clearMove = useCallback((id) => {
    setMoves((m) => {
      const n = { ...m };
      delete n[id];
      return n;
    });
    resetPreview();
  }, [resetPreview]);

  // Build the /edit payload: text edits + baked stamps (overlays + moved spans).
  // A moved span is an empty replacement (erase) plus a text stamp at the new
  // spot carrying its own font/size/color/weight (edited text if it was edited).
  const buildPayload = useCallback(() => {
    const rgbToHex = (c) =>
      Array.isArray(c) && c.length >= 3
        ? "#" + c.slice(0, 3).map((v) =>
            Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0")).join("")
        : "#3d3b3a";
    const spanById = new Map(spans.map((s) => [s.id, s]));
    const editArr = [];
    for (const [id, t] of Object.entries(edits)) {
      if (movedIds.has(Number(id))) continue; // moved spans handled below
      editArr.push({ index: Number(id), new_text: t });
    }
    const moveStamps = [];
    for (const [id, pos] of Object.entries(moves)) {
      const sp = spanById.get(Number(id));
      if (!sp) continue;
      editArr.push({ index: Number(id), new_text: "" }); // erase original
      const fl = sp.flags || 0;
      moveStamps.push({
        kind: "text", page: sp.page, x: pos.x, y: pos.y,
        text: edits[id] ?? sp.text,
        size: sp.size, color: rgbToHex(sp.color), font: sp.font,
        bold: (fl & 16) !== 0 || /bold|black|heavy|semibold|extrabold/i.test(sp.font || ""),
        italic: (fl & 2) !== 0 || /italic|oblique/i.test(sp.font || ""),
      });
    }
    return { editArr, stamps: [...overlays, ...moveStamps] };
  }, [spans, edits, moves, movedIds, overlays]);

  // ---- Added-content overlays (free text + signatures) ----
  const addOverlay = useCallback((o) => {
    const id = `ov${Date.now()}${Math.round(Math.random() * 1e4)}`;
    setOverlays((v) => [...v, { id, ...o }]);
    resetPreview();
    return id;
  }, [resetPreview]);
  const updateOverlay = useCallback((id, patch) => {
    setOverlays((v) => v.map((o) => (o.id === id ? { ...o, ...patch } : o)));
    resetPreview();
  }, [resetPreview]);
  const removeOverlay = useCallback((id) => {
    setOverlays((v) => v.filter((o) => o.id !== id));
    resetPreview();
  }, [resetPreview]);

  const preview = useCallback(async () => {
    if (!file || !hasChanges) return null;
    setError(null);
    setBusy(true);
    try {
      const { editArr, stamps } = buildPayload();
      const { blob, fontReport: fr } = await editPdf(file, editArr, stamps);
      const buf = await blob.arrayBuffer();
      const url = URL.createObjectURL(blob);
      setPreviewUrl((u) => {
        if (u) URL.revokeObjectURL(u);
        return url;
      });
      setPreviewData(buf);
      setFontReport(fr);
      return url;
    } catch (e) {
      setError(e.message || "Couldn't apply edits.");
      return null;
    } finally {
      setBusy(false);
    }
  }, [file, edits]);

  // Always bakes the current edits, overlays AND moves fresh (they're live on the
  // canvas, never in the text-only preview, so we can't reuse previewUrl here).
  const download = useCallback(async () => {
    if (!file || !hasChanges) return;
    setError(null);
    setBusy(true);
    try {
      const { editArr, stamps } = buildPayload();
      const { blob } = await editPdf(file, editArr, stamps, true); // final → counts toward plan
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `edited_${file?.name || "document.pdf"}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e.message || "Couldn't export the PDF.");
    } finally {
      setBusy(false);
    }
  }, [file, hasChanges, buildPayload]);

  return {
    file, fileData, spans, pages, pageIndex, setPageIndex,
    selectedId, setSelectedId, edits, nEdits, editedIds,
    previewData, previewUrl, fontReport, busy, error,
    zoom, setZoom,
    overlays, addOverlay, updateOverlay, removeOverlay, fonts, hasChanges,
    moves, movedIds, moveSpan, clearMove,
    loadFile, setFieldValue, resetAll, preview, download,
  };
}

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

  const nEdits = Object.keys(edits).length;
  const editedIds = useMemo(() => new Set(Object.keys(edits).map(Number)), [edits]);
  const fonts = useMemo(() => [...new Set(spans.map((s) => s.font))].filter(Boolean), [spans]);
  const hasChanges = nEdits > 0 || overlays.length > 0;

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
    resetPreview();
  }, [resetPreview]);

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
    if (!file || Object.keys(edits).length === 0) return null;
    setError(null);
    setBusy(true);
    try {
      const arr = Object.entries(edits).map(([id, t]) => ({
        index: Number(id),
        new_text: t,
      }));
      const { blob, fontReport: fr } = await editPdf(file, arr);
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

  // Always bakes the current edits AND overlays fresh (overlays are live on the
  // canvas, never in the text-only preview, so we can't reuse previewUrl here).
  const download = useCallback(async () => {
    if (!file) return;
    const arr = Object.entries(edits).map(([id, t]) => ({ index: Number(id), new_text: t }));
    if (arr.length === 0 && overlays.length === 0) return;
    setError(null);
    setBusy(true);
    try {
      const { blob } = await editPdf(file, arr, overlays);
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
  }, [file, edits, overlays]);

  return {
    file, fileData, spans, pages, pageIndex, setPageIndex,
    selectedId, setSelectedId, edits, nEdits, editedIds,
    previewData, previewUrl, fontReport, busy, error,
    zoom, setZoom,
    overlays, addOverlay, updateOverlay, removeOverlay, fonts, hasChanges,
    loadFile, setFieldValue, resetAll, preview, download,
  };
}

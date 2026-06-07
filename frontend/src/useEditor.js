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

  const nEdits = Object.keys(edits).length;
  const editedIds = useMemo(() => new Set(Object.keys(edits).map(Number)), [edits]);

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

  const download = useCallback(async () => {
    const url = previewUrl || (await preview());
    if (!url) return;
    const a = document.createElement("a");
    a.href = url;
    a.download = `edited_${file?.name || "document.pdf"}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, [previewUrl, preview, file]);

  return {
    file, fileData, spans, pages, pageIndex, setPageIndex,
    selectedId, setSelectedId, edits, nEdits, editedIds,
    previewData, previewUrl, fontReport, busy, error,
    zoom, setZoom,
    loadFile, setFieldValue, resetAll, preview, download,
  };
}

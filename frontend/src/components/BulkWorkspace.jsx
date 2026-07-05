import { useEffect, useMemo, useRef, useState } from "react";
import Papa from "papaparse";
import PdfCanvas from "./PdfCanvas.jsx";
import FontPanel from "./FontPanel.jsx";
import { bulkGenerate, editPdf, autoMapFields } from "../api.js";
import { effectiveSplit, smartSplit } from "../lib/split.js";
import SplitPicker from "./SplitPicker.jsx";

// Make a list of column names unique by suffixing duplicates: a, a (2), a (3).
function uniquify(names) {
  const seen = new Map();
  return names.map((n) => {
    const c = (seen.get(n) || 0) + 1;
    seen.set(n, c);
    return c === 1 ? n : `${n} (${c})`;
  });
}

/**
 * Document-first bulk generator. You click the spots on the PDF you want to
 * vary; each becomes a column pre-filled with its original value. Edit only
 * what differs, add documents (rows), or import a CSV to fill many at once.
 * Serializes to the same CSV + mapping the untouched /bulk backend expects.
 */
export default function BulkWorkspace({ file, spans, data, pages }) {
  const canvasBoxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(1);

  const [picked, setPicked] = useState([]); // span ids, in pick order
  const [rows, setRows] = useState([]); // [{ [spanId]: value }]  — one per document
  const [hoverId, setHoverId] = useState(null); // field highlighted on the doc
  const [filenameId, setFilenameId] = useState(null); // picked field that names the files
  const [outputMode, setOutputMode] = useState("zip"); // "zip" | "merged"
  const [splits, setSplits] = useState({}); // { [spanId]: -1|index } — value-split overrides
  const [splitEditId, setSplitEditId] = useState(null); // column currently in split mode

  // CSV/paste import (optional, scoped to the picked fields)
  const [showImport, setShowImport] = useState(false);
  const [impTab, setImpTab] = useState("upload"); // "upload" | "paste"
  const [impFirstRow, setImpFirstRow] = useState(true);
  const [impText, setImpText] = useState("");
  const [impHeaders, setImpHeaders] = useState([]);
  const [impRows, setImpRows] = useState([]);
  const [impMap, setImpMap] = useState({}); // { spanId: header }
  const impUploadRef = useRef(null);

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [autoBusy, setAutoBusy] = useState(false); // AI auto-detect in progress

  // Verify-before-download: render one generated document (reuses /edit).
  const [previewIdx, setPreviewIdx] = useState(null); // row being previewed, or null
  const [previewData, setPreviewData] = useState(null); // rendered ArrayBuffer
  const [previewBusy, setPreviewBusy] = useState(false);

  const pageCount = (pages && pages.length) || 1;
  const spanById = useMemo(() => new Map(spans.map((s) => [s.id, s])), [spans]);
  const pickedSpans = picked.map((id) => spanById.get(id)).filter(Boolean);
  const filenameValid = filenameId != null && picked.includes(filenameId);

  const label = (s) => {
    const t = (s.text || "").trim();
    return t ? (t.length > 40 ? t.slice(0, 40) + "…" : t) : `Field #${s.id}`;
  };
  const headerName = (s) => {
    const t = (s.text || "").trim();
    return t || `Field ${s.id}`;
  };
  const original = (id) => spanById.get(id)?.text ?? "";
  const makeRow = (ids) => Object.fromEntries(ids.map((id) => [id, original(id)]));

  // Split lens: the locked label is span.text[:split]; only the value is edited.
  // Rows always store the FULL text, so generate/preview stay correct as-is.
  const splitOf = (id) => effectiveSplit(splits[id], spanById.get(id)?.text || "");
  const labelOf = (id) => {
    const sp = splitOf(id);
    return sp != null ? (spanById.get(id)?.text || "").slice(0, sp) : "";
  };
  const valueOf = (id, full) => {
    const sp = splitOf(id);
    return sp != null ? String(full ?? "").slice(sp) : String(full ?? "");
  };
  const withLabel = (id, value) => {
    const sp = splitOf(id);
    return sp != null ? labelOf(id) + value : value;
  };

  // Measure the pane so the PDF fits its width (100% = fit-to-pane).
  useEffect(() => {
    const el = canvasBoxRef.current;
    if (!el) return;
    const update = () => setBoxW(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [file]);

  // ---- Auto-save the setup (picked fields + assignments + data) per template ----
  const storeKey = file ? `redraft:bulk:${file.name}:${spans.length}` : null;
  const skipSaveRef = useRef(false);

  useEffect(() => {
    if (!storeKey) return;
    let p = [], r = [], m = {}, f = null, sp = {};
    try {
      const raw = localStorage.getItem(storeKey);
      if (raw) {
        const s = JSON.parse(raw);
        p = (s.picked || []).filter((id) => spanById.has(id));
        r = Array.isArray(s.rows) ? s.rows : [];
        m = s.impMap || {};
        f = p.includes(s.filenameId) ? s.filenameId : null;
        sp = s.splits && typeof s.splits === "object" ? s.splits : {};
      }
    } catch {
      /* ignore corrupt cache */
    }
    skipSaveRef.current = true; // don't let the first save clobber what we just loaded
    setPicked(p);
    setRows(r);
    setImpMap(m);
    setFilenameId(f);
    setSplits(sp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeKey]);

  useEffect(() => {
    if (!storeKey) return;
    if (skipSaveRef.current) {
      skipSaveRef.current = false;
      return;
    }
    try {
      localStorage.setItem(storeKey, JSON.stringify({ picked, rows, impMap, filenameId, splits }));
    } catch {
      /* storage full / unavailable — non-fatal */
    }
  }, [storeKey, picked, rows, impMap, filenameId, splits]);

  // Any change to the fields or their values invalidates a shown preview.
  useEffect(() => {
    setPreviewIdx(null);
    setPreviewData(null);
  }, [picked, rows]);

  const pdfWidth = Math.max(260, Math.round(((boxW || 640) - 48) * zoom));

  // ---- Pick / unpick a field on the document ----
  function togglePick(id) {
    if (id == null) return;
    setPicked((prev) => {
      if (prev.includes(id)) {
        setRows((rs) =>
          rs.map((r) => {
            const c = { ...r };
            delete c[id];
            return c;
          })
        );
        return prev.filter((x) => x !== id);
      }
      const next = [...prev, id];
      const orig = original(id);
      setRows((rs) => (rs.length ? rs.map((r) => ({ ...r, [id]: orig })) : [makeRow(next)]));
      return next;
    });
    setResult(null);
  }

  // ---- Document (row) editing ----
  function setCell(rowIdx, id, val) {
    setRows((rs) => rs.map((r, i) => (i === rowIdx ? { ...r, [id]: val } : r)));
    setResult(null);
  }
  const addDoc = () => setRows((rs) => [...rs, makeRow(picked)]);
  const dupDoc = (i) => setRows((rs) => [...rs.slice(0, i + 1), { ...rs[i] }, ...rs.slice(i + 1)]);
  const delDoc = (i) => setRows((rs) => rs.filter((_, j) => j !== i));

  // ---- CSV / paste import (fills the picked fields) ----
  function impIngest(data2D) {
    const grid = (data2D || []).filter((r) => r.length && r.some((c) => String(c).trim() !== ""));
    if (!grid.length) return setError("No data found in that input.");
    let hs, rs;
    if (impFirstRow) {
      hs = grid[0].map((h, i) => String(h).trim() || `Column ${i + 1}`);
      rs = grid.slice(1);
    } else {
      hs = grid[0].map((_, i) => `Column ${i + 1}`);
      rs = grid;
    }
    hs = uniquify(hs);
    setImpHeaders(hs);
    setImpRows(rs.map((r) => r.map((v) => String(v ?? ""))));
    // Keep your prior column choices when re-pasting same-shaped data; fuzzy-fill the rest.
    setImpMap((prev) => {
      const m = {};
      for (const s of pickedSpans) {
        if (prev[s.id] && hs.includes(prev[s.id])) {
          m[s.id] = prev[s.id];
          continue;
        }
        const txt = (s.text || "").toLowerCase().trim();
        const hit =
          txt &&
          hs.find((h) => {
            const hl = h.toLowerCase();
            return hl && (txt.includes(hl) || hl.includes(txt));
          });
        if (hit) m[s.id] = hit;
      }
      return m;
    });
    setError(null);
  }
  function impUpload(f) {
    if (!f) return;
    Papa.parse(f, { skipEmptyLines: true, complete: (out) => impIngest(out.data) });
  }
  function impLoadPaste() {
    if (!impText.trim()) return setError("Paste some rows first.");
    impIngest(Papa.parse(impText.trim(), { skipEmptyLines: true }).data);
  }
  function impApply() {
    if (!impRows.length) return;
    const newRows = impRows.map((r) => {
      const o = {};
      for (const s of pickedSpans) {
        const h = impMap[s.id];
        const ci = h != null ? impHeaders.indexOf(h) : -1;
        // A split column maps the CSV value into just the VALUE part, keeping
        // the locked label (e.g. "Client: " + "INWI"). Unmapped → keep original.
        o[s.id] = ci >= 0 ? withLabel(s.id, r[ci] ?? "") : original(s.id);
      }
      return o;
    });
    setRows(newRows);
    setShowImport(false);
    setImpHeaders([]);
    setImpRows([]);
    setImpText("");
    setResult(null);
  }

  // ---- AI auto-detect: match CSV columns → the PDF's fields, pick+split them,
  // and build every row straight from the CSV (mapped fields get the column's
  // value with the label kept; everything else keeps the template value). ----
  async function autoDetect() {
    if (!impHeaders.length) return;
    setAutoBusy(true);
    setError(null);
    try {
      // Send up to 3 example values per column so the AI can tell a date from
      // an amount from an ICE number (all just digits with one example).
      const samples = {};
      impHeaders.forEach((h, i) => {
        const vals = [];
        for (let r = 0; r < impRows.length && vals.length < 3; r++) {
          const v = String(impRows[r]?.[i] ?? "").trim();
          if (v) vals.push(v);
        }
        samples[h] = vals.join(" | ");
      });
      if (!spans.length) {
        setError("No PDF fields to detect — load the template in the PDF Editor tab first.");
        return;
      }
      const { mapping, source } = await autoMapFields(spans, impHeaders, samples);
      const entries = Object.entries(mapping); // [ [column, spanId], … ]
      if (!entries.length) {
        setError(
          "The AI didn't match any fields to your columns. Map them by hand below, " +
            "or check the field names in your CSV."
        );
        return;
      }

      // Build the detected setup from the FRESH mapping (not via state, so there's
      // no timing gap): span → column, span → split index, and the picked list.
      const map = { ...impMap }; // keep any manual mappings the user already set
      const sp = { ...splits };
      const detected = [];
      for (const [col, sid] of entries) {
        if (!spanById.has(sid)) continue;
        detected.push(sid);
        map[sid] = col;
        // Template fields glue label+value ("Nom Client : ACME"); split so only
        // the value is replaced and the label stays.
        const s = smartSplit(spanById.get(sid).text || "");
        if (s != null) sp[sid] = s;
      }
      const allIds = [...new Set([...picked, ...detected])];

      // Fill one row per CSV line: mapped field → label(if split) + CSV value;
      // unmapped picked field → keep its full original template text.
      const colIndex = {};
      impHeaders.forEach((h, i) => (colIndex[h] = i));
      const newRows = impRows.map((r) => {
        const o = {};
        for (const sid of allIds) {
          const col = map[sid];
          const ci = col != null ? colIndex[col] : -1;
          if (ci >= 0) {
            const value = String(r[ci] ?? "");
            const idx = sp[sid];
            const label =
              idx != null && idx >= 0 ? (spanById.get(sid).text || "").slice(0, idx) : "";
            o[sid] = label + value;
          } else {
            o[sid] = spanById.get(sid)?.text ?? ""; // unmapped → full original
          }
        }
        return o;
      });

      setPicked(allIds);
      setImpMap(map);
      setSplits(sp);
      setRows(newRows);
      setShowImport(false);
      setResult({ detected: detected.length, rows: newRows.length, source });
    } catch (e) {
      setError(e.message || "Auto-detect failed.");
    } finally {
      setAutoBusy(false);
    }
  }

  // ---- Generate ----
  function process() {
    setError(null);
    setResult(null);
    if (!file) return setError("Load a template PDF in the PDF Editor tab first.");
    if (!picked.length) return setError("Click a place on the document to make it editable.");
    if (!rows.length) return setError("Add at least one document.");

    setBusy(true);
    const headers = uniquify(pickedSpans.map(headerName));
    const mapping = {};
    pickedSpans.forEach((s, i) => (mapping[s.id] = headers[i]));
    const data2D = rows.map((r) => pickedSpans.map((s) => r[s.id] ?? ""));
    const csv = Papa.unparse({ fields: headers, data: data2D });
    const csvFile = new File([csv], "data.csv", { type: "text/csv" });
    // Name files by the chosen field's column (its uniquified header), if any.
    const fnIdx = filenameValid ? pickedSpans.findIndex((s) => s.id === filenameId) : -1;
    const filenameCol = fnIdx >= 0 ? headers[fnIdx] : "";

    bulkGenerate(file, csvFile, mapping, filenameCol, outputMode)
      .then(({ blob, generated, failed }) => {
        const ext = outputMode === "merged" ? "pdf" : "zip";
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${(file.name || "template").replace(/\.pdf$/i, "")}_bulk.${ext}`;
        a.click();
        URL.revokeObjectURL(url);
        setResult({ generated, failed, merged: outputMode === "merged" });
      })
      .catch((e) => setError(e.message || "Generation failed."))
      .finally(() => setBusy(false));
  }

  // ---- Preview one generated document (reuses /edit for a single row) ----
  async function showPreview(idx) {
    if (!file || !rows.length) return;
    const i = Math.max(0, Math.min(rows.length - 1, idx));
    const edits = pickedSpans
      .map((s) => ({ index: s.id, new_text: String(rows[i][s.id] ?? "") }))
      .filter((e) => e.new_text !== "");
    setPreviewBusy(true);
    setError(null);
    try {
      const { blob } = await editPdf(file, edits);
      setPreviewData(await blob.arrayBuffer());
      setPreviewIdx(i);
      if (pickedSpans[0]) setPageIndex(pickedSpans[0].page);
    } catch (e) {
      setError(e.message || "Couldn't render the preview.");
    } finally {
      setPreviewBusy(false);
    }
  }
  function exitPreview() {
    setPreviewIdx(null);
    setPreviewData(null);
  }

  // One-tap demo so the flow is obvious: pick the first field, make 2 copies.
  function runExample() {
    const s = spans[0];
    if (!s) return;
    setPageIndex(s.page);
    setPicked([s.id]);
    const o = s.text || "";
    setRows([{ [s.id]: o }, { [s.id]: o }]);
    setShowImport(false);
    setResult(null);
    setError(null);
  }

  // Wipe the saved setup for this template and start fresh.
  function startOver() {
    setPicked([]);
    setRows([]);
    setImpMap({});
    setFilenameId(null);
    setSplits({});
    setSplitEditId(null);
    setShowImport(false);
    setResult(null);
    setError(null);
    if (storeKey) {
      try {
        localStorage.removeItem(storeKey);
      } catch {
        /* ignore */
      }
    }
  }

  const pageHasPicks = pickedSpans.some((s) => s.page === pageIndex);
  const currentStep = picked.length === 0 ? 1 : 2;
  const stepHint =
    picked.length === 0
      ? "Upload your data and auto-detect the fields — or click text on the document"
      : "Type the new values — each row makes one PDF, then press Generate";

  return (
    <div className="flex-1 flex gap-4 p-4 overflow-hidden max-w-[1500px] w-full mx-auto animate-rise">
      {/* Left: the document — click spots to make them editable */}
      <div className="flex-[0.58] bg-surface-container-lowest rounded-xl border border-outline-variant/30 flex flex-col overflow-hidden relative shadow-none">
        {/* Toolbar */}
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-surface/90 backdrop-blur-md border border-outline-variant/50 rounded-full px-3 py-1.5 flex items-center gap-3 z-10 shadow-xl">
          <button
            disabled={pageIndex === 0}
            onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
            className="text-on-surface-variant hover:text-primary transition-colors disabled:opacity-30"
          >
            <span className="material-symbols-outlined text-[18px]">chevron_left</span>
          </button>
          <span className="text-caption font-medium">
            {pageIndex + 1} / {pageCount}
          </span>
          <button
            disabled={pageIndex >= pageCount - 1}
            onClick={() => setPageIndex((p) => Math.min(pageCount - 1, p + 1))}
            className="text-on-surface-variant hover:text-primary transition-colors disabled:opacity-30"
          >
            <span className="material-symbols-outlined text-[18px]">chevron_right</span>
          </button>
          <div className="w-px h-4 bg-outline-variant"></div>
          <button
            onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}
            className="text-on-surface-variant hover:text-primary transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">zoom_out</span>
          </button>
          <span className="text-caption font-medium">{Math.round(zoom * 100)}%</span>
          <button
            onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(2)))}
            className="text-on-surface-variant hover:text-primary transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">zoom_in</span>
          </button>
        </div>

        {/* Preview banner — step through generated documents */}
        {previewIdx != null && (
          <div className="absolute top-14 left-1/2 -translate-x-1/2 z-20 bg-secondary-container text-white rounded-full px-3 py-1.5 flex items-center gap-2 shadow-xl text-caption">
            <span className="material-symbols-outlined text-[16px]">visibility</span>
            Preview · document {previewIdx + 1} / {rows.length}
            <div className="w-px h-4 bg-white/30 mx-0.5"></div>
            <button
              disabled={previewIdx === 0 || previewBusy}
              onClick={() => showPreview(previewIdx - 1)}
              className="disabled:opacity-30 hover:opacity-80 transition-opacity"
            >
              <span className="material-symbols-outlined text-[16px]">chevron_left</span>
            </button>
            <button
              disabled={previewIdx >= rows.length - 1 || previewBusy}
              onClick={() => showPreview(previewIdx + 1)}
              className="disabled:opacity-30 hover:opacity-80 transition-opacity"
            >
              <span className="material-symbols-outlined text-[16px]">chevron_right</span>
            </button>
            <button onClick={exitPreview} title="Exit preview" className="ml-0.5 hover:opacity-80 transition-opacity">
              <span className="material-symbols-outlined text-[16px]">close</span>
            </button>
          </div>
        )}

        {/* Hint */}
        {previewIdx == null && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 bg-surface/90 backdrop-blur-md border border-outline-variant/50 rounded-full px-3 py-1 text-caption text-on-surface-variant shadow-lg flex items-center gap-1.5">
            <span className="material-symbols-outlined text-[14px] text-accent-cyan">ads_click</span>
            {pickedSpans.length
              ? `${pickedSpans.length} selected — click text to add, click again to remove`
              : "Click any text or number you want to change"}
          </div>
        )}

        <div ref={canvasBoxRef} className="flex-1 overflow-auto p-6 flex justify-center bg-on-surface/[0.04]">
          {file && data ? (
            <div className="paper-shadow rounded-sm mt-12 mb-10 h-fit">
              <PdfCanvas
                data={previewIdx != null ? previewData : data}
                pageIndex={pageIndex}
                spans={spans}
                selectedId={previewIdx != null ? null : hoverId}
                editedIds={previewIdx != null ? new Set() : new Set(picked)}
                onSelect={previewIdx != null ? () => {} : togglePick}
                maxWidth={pdfWidth}
              />
            </div>
          ) : (
            <div className="self-center text-center text-on-surface-variant">
              <span className="material-symbols-outlined text-[40px] opacity-40">description</span>
              <p className="mt-2 text-body-md">Load a PDF in the PDF Editor tab to start.</p>
            </div>
          )}
        </div>
      </div>

      {/* Right: values for only the picked fields */}
      <div className="flex-[0.42] bg-surface-container rounded-xl border border-outline-variant/30 flex flex-col shadow-panel overflow-hidden">
        {/* Header + guided steps */}
        <div className="px-4 pt-4 pb-3 border-b border-outline-variant/30 bg-surface/50 backdrop-blur-md">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 className="font-display-md text-xl font-bold tracking-tight">Bulk generator</h2>
            </div>
            {file && (
              <button
                onClick={() => {
                  setShowImport((v) => !v);
                  setError(null);
                }}
                className={`shrink-0 px-3 py-1.5 rounded-lg border text-label-md flex items-center gap-1.5 transition-colors ${
                  showImport
                    ? "bg-accent-cyan/10 border-accent-cyan/30 text-accent-cyan"
                    : "border-outline-variant/40 text-on-surface-variant hover:text-on-surface"
                }`}
              >
                <span className="material-symbols-outlined text-[16px]">upload</span>
                {showImport ? "Close" : "Import list"}
              </button>
            )}
          </div>

          {/* One plain guidance line for the current step */}
          <div className="mt-2 flex items-center gap-2 text-caption">
            <span className="w-5 h-5 shrink-0 rounded-full bg-accent-cyan/15 text-accent-cyan flex items-center justify-center text-[11px] font-bold">
              {currentStep}
            </span>
            <span className="text-on-surface-variant">{stepHint}</span>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto">
          {!showImport && picked.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-6 text-on-surface-variant animate-fade">
              <span className="material-symbols-outlined text-[44px] text-accent-cyan/70">table_view</span>
              <p className="mt-3 text-body-lg text-on-surface font-semibold">
                Start from your spreadsheet
              </p>
              <p className="mt-1 text-body-md max-w-[290px]">
                Upload your CSV/Excel and let AI auto-detect which spots on the document to fill —
                or click any text on the PDF to pick fields yourself.
              </p>
              <button
                onClick={() => {
                  setShowImport(true);
                  setError(null);
                }}
                disabled={!file}
                className="mt-5 px-5 py-2.5 rounded-lg bg-secondary-container text-white hover:bg-[#003ea8] transition-colors text-label-md flex items-center gap-2 shadow-[0_0_20px_rgba(0,83,219,0.3)] disabled:opacity-40"
              >
                <span className="material-symbols-outlined text-[18px]">upload</span>
                Upload your data
              </button>
              {spans.length > 0 && (
                <button
                  onClick={runExample}
                  className="mt-3 text-caption text-on-surface-variant hover:text-accent-cyan transition-colors flex items-center gap-1.5"
                >
                  <span className="material-symbols-outlined text-[16px]">auto_awesome</span>
                  or show me an example
                </button>
              )}
            </div>
          ) : showImport ? (
            /* ---- Import panel ---- */
            <div className="p-4 space-y-4 animate-drop">
              <div className="flex items-center gap-1 bg-surface-container-low rounded-lg p-1 border border-outline-variant/20 w-max">
                {[
                  ["upload", "upload_file", "Upload"],
                  ["paste", "content_paste", "Paste"],
                ].map(([k, icon, lbl]) => (
                  <button
                    key={k}
                    onClick={() => setImpTab(k)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-label-md text-sm transition-all ${
                      impTab === k
                        ? "bg-surface-variant text-on-surface shadow-sm"
                        : "text-on-surface-variant hover:text-on-surface"
                    }`}
                  >
                    <span className="material-symbols-outlined text-[16px]">{icon}</span>
                    {lbl}
                  </button>
                ))}
              </div>

              <label className="flex items-center gap-2 text-caption text-on-surface-variant cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={impFirstRow}
                  onChange={(e) => setImpFirstRow(e.target.checked)}
                  className="accent-secondary-container w-4 h-4"
                />
                First row is the header
              </label>

              {impTab === "upload" ? (
                <div
                  onClick={() => impUploadRef.current?.click()}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    impUpload(e.dataTransfer.files?.[0]);
                  }}
                  className="border-2 border-dashed border-outline-variant/50 hover:border-secondary-container rounded-xl p-6 flex flex-col items-center gap-2 cursor-pointer transition-colors text-center"
                >
                  <span className="material-symbols-outlined text-[24px] text-on-surface-variant">
                    cloud_upload
                  </span>
                  <p className="text-body-md text-on-surface">Drop a CSV / TSV or click to browse</p>
                  <input
                    ref={impUploadRef}
                    type="file"
                    accept=".csv,.tsv,.txt"
                    className="hidden"
                    onChange={(e) => impUpload(e.target.files?.[0])}
                  />
                </div>
              ) : (
                <div className="space-y-2">
                  <textarea
                    value={impText}
                    onChange={(e) => setImpText(e.target.value)}
                    placeholder={"Paste rows from Excel / Sheets or CSV…"}
                    className="w-full h-28 bg-surface-container-lowest border border-outline-variant/50 rounded-lg p-3 text-sm text-on-surface font-mono focus:outline-none focus:ring-1 focus:ring-secondary-container resize-y"
                  />
                  <button
                    onClick={impLoadPaste}
                    className="px-3 py-1.5 bg-surface-container-highest border border-outline-variant rounded-lg text-label-md text-on-surface hover:border-secondary-container transition-colors"
                  >
                    Load
                  </button>
                </div>
              )}

              {impHeaders.length > 0 && (
                <div className="space-y-2">
                  <button
                    onClick={autoDetect}
                    disabled={autoBusy}
                    className="w-full flex items-center justify-center gap-2 py-2 rounded-lg bg-gradient-to-r from-accent-cyan/15 to-secondary-container/15 border border-accent-cyan/40 text-accent-cyan font-label-md text-sm hover:from-accent-cyan/25 hover:to-secondary-container/25 transition-all disabled:opacity-50"
                  >
                    <span className={`material-symbols-outlined text-[18px] ${autoBusy ? "animate-spin" : ""}`}>
                      {autoBusy ? "progress_activity" : "auto_awesome"}
                    </span>
                    {autoBusy ? "Detecting fields…" : "Auto-detect fields from this data (AI)"}
                  </button>
                  <p className="text-caption text-on-surface-variant">
                    Match each field to a column ({impRows.length} rows found):
                  </p>
                  {pickedSpans.map((s) => (
                    <div key={s.id} className="flex items-center gap-2">
                      <span className="flex-1 text-body-md text-on-surface truncate" title={s.text}>
                        {label(s)}
                      </span>
                      <span className="material-symbols-outlined text-[16px] text-on-surface-variant">
                        arrow_forward
                      </span>
                      <select
                        value={impMap[s.id] || ""}
                        onChange={(e) =>
                          setImpMap((m) => {
                            const n = { ...m };
                            if (e.target.value) n[s.id] = e.target.value;
                            else delete n[s.id];
                            return n;
                          })
                        }
                        className="flex-1 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1.5 px-2 text-body-md text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container"
                      >
                        <option value="">keep original</option>
                        {impHeaders.map((h) => (
                          <option key={h} value={h}>
                            {h}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
                  <button
                    onClick={impApply}
                    className="w-full mt-2 bg-secondary-container text-white py-2 rounded-lg font-label-md flex items-center justify-center gap-2"
                  >
                    <span className="material-symbols-outlined text-[18px]">done</span>
                    Create {impRows.length} document{impRows.length === 1 ? "" : "s"}
                  </button>
                </div>
              )}
            </div>
          ) : (
            /* ---- Copies table (only the picked fields) ---- */
            <div className="flex flex-col h-full">
              {splitEditId != null && spanById.has(splitEditId) ? (
                <SplitPicker
                  text={spanById.get(splitEditId).text}
                  split={splitOf(splitEditId)}
                  title={label(spanById.get(splitEditId))}
                  onSet={(i) => {
                    setSplits((m) => ({ ...m, [splitEditId]: i }));
                    setSplitEditId(null);
                  }}
                  onWhole={() => {
                    setSplits((m) => ({ ...m, [splitEditId]: -1 }));
                    setSplitEditId(null);
                  }}
                  onClose={() => setSplitEditId(null)}
                />
              ) : (
                <div className="px-3 py-2 text-caption text-on-surface-variant bg-surface-container-low border-b border-outline-variant/20 shrink-0">
                  Each row = one PDF. Edit only what changes. Hover a column, click ⋯ to edit only part of it.
                </div>
              )}
              <div className="overflow-auto flex-1">
              <table className="w-full border-collapse text-left">
                <thead className="sticky top-0 z-10 bg-surface-container-high">
                  <tr>
                    <th className="w-12 px-2 py-2.5 text-center text-caption text-on-surface-variant border-b border-outline-variant/20">
                      Copy
                    </th>
                    {pickedSpans.map((s) => (
                      <th
                        key={s.id}
                        onMouseEnter={() => setHoverId(s.id)}
                        onMouseLeave={() => setHoverId((h) => (h === s.id ? null : h))}
                        className="group min-w-[150px] px-3 py-2.5 border-b border-outline-variant/20"
                      >
                        <div className="flex items-center gap-1">
                          <span
                            className="flex-1 text-label-md text-on-surface truncate"
                            title={`${s.text || ""}  ·  page ${s.page + 1}`}
                          >
                            {label(s)}
                            {splitOf(s.id) != null && labelOf(s.id).trim() && (
                              <span className="ml-1 text-[10px] text-accent-cyan align-middle" title={`Editing only the value after "${labelOf(s.id)}"`}>
                                ✂
                              </span>
                            )}
                          </span>
                          <button
                            onClick={() => setSplitEditId(splitEditId === s.id ? null : s.id)}
                            title="Split — edit only part of this field"
                            className={`transition-all ${
                              splitEditId === s.id
                                ? "text-secondary-container"
                                : "opacity-0 group-hover:opacity-100 text-on-surface-variant hover:text-secondary-container"
                            }`}
                          >
                            <span className="material-symbols-outlined text-[16px]">more_horiz</span>
                          </button>
                          <button
                            onClick={() => togglePick(s.id)}
                            title="Remove this field"
                            className="opacity-0 group-hover:opacity-100 text-on-surface-variant hover:text-error transition-all"
                          >
                            <span className="material-symbols-outlined text-[16px]">close</span>
                          </button>
                        </div>
                      </th>
                    ))}
                    <th className="w-8 border-b border-outline-variant/20"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, r) => (
                    <tr key={r} className="group hover:bg-surface-container-high/40">
                      <td className="px-2 text-center text-caption text-on-surface-variant border-b border-outline-variant/10">
                        {r + 1}
                      </td>
                      {pickedSpans.map((s) => {
                        const val = row[s.id] ?? "";
                        const changed = val !== original(s.id);
                        const lbl = labelOf(s.id);
                        return (
                          <td key={s.id} className="border-b border-outline-variant/10 p-0">
                            <div className="flex items-stretch">
                              {lbl && (
                                <span
                                  title={lbl}
                                  className="shrink-0 max-w-[42%] truncate px-2 py-2 text-body-md text-on-surface-variant/60 bg-surface-container-high/40 border-r border-outline-variant/20 select-none flex items-center"
                                >
                                  {lbl}
                                </span>
                              )}
                              <input
                                value={valueOf(s.id, val)}
                                onChange={(e) => setCell(r, s.id, withLabel(s.id, e.target.value))}
                                onFocus={() => setHoverId(s.id)}
                                placeholder={valueOf(s.id, original(s.id))}
                                className={`flex-1 min-w-0 bg-transparent px-3 py-2 text-body-md text-on-surface focus:outline-none focus:bg-surface-container-lowest focus:ring-1 focus:ring-inset focus:ring-secondary-container ${
                                  changed ? "text-accent-cyan" : ""
                                }`}
                              />
                            </div>
                          </td>
                        );
                      })}
                      <td className="px-1 text-center border-b border-outline-variant/10">
                        <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => dupDoc(r)}
                            title="Duplicate this copy"
                            className="p-1 text-on-surface-variant hover:text-secondary transition-colors"
                          >
                            <span className="material-symbols-outlined text-[16px]">content_copy</span>
                          </button>
                          {rows.length > 1 && (
                            <button
                              onClick={() => delDoc(r)}
                              title="Delete this copy"
                              className="p-1 text-on-surface-variant hover:text-error transition-colors"
                            >
                              <span className="material-symbols-outlined text-[16px]">close</span>
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button
                onClick={addDoc}
                className="text-secondary text-label-md flex items-center gap-1 hover:underline px-3 py-2"
              >
                <span className="material-symbols-outlined text-[16px]">add</span> Add another copy
              </button>
              {!pageHasPicks && pickedSpans.length > 0 && (
                <p className="px-3 pb-2 text-caption text-on-surface-variant">
                  Some selected fields are on other pages — use the page arrows to see them.
                </p>
              )}
              </div>
            </div>
          )}
        </div>

        {/* Footer: generate */}
        <div className="p-4 border-t border-outline-variant/30 bg-surface/80 backdrop-blur-xl space-y-2">
          {file && <FontPanel file={file} />}
          {(error || result) && (
            <div
              className={`rounded-lg px-3 py-2 text-caption flex items-center gap-2 border ${
                error
                  ? "border-error/30 bg-error/10 text-error"
                  : "border-secondary-container/30 bg-secondary-container/10 text-secondary"
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">
                {error ? "error" : "check_circle"}
              </span>
              {error
                ? error
                : result.detected != null
                ? `Detected ${result.detected} field(s) and filled ${result.rows} row(s)` +
                  `${result.source === "ai" ? " (AI)" : ""} — review below, then Generate.`
                : `Generated ${result.generated} PDF(s)${
                    result.failed ? ` · ${result.failed} skipped` : ""
                  } — ${result.merged ? "merged PDF" : "ZIP"} downloaded.`}
            </div>
          )}
          {picked.length > 0 && (
            <div className="flex items-center gap-2 px-0.5 text-caption text-on-surface-variant">
              <span className="flex items-center gap-1.5 shrink-0">
                <span className="material-symbols-outlined text-[14px]">folder_zip</span>
                Output
              </span>
              <div className="flex items-center gap-1 bg-surface-container-low rounded-lg p-0.5 border border-outline-variant/20">
                <button
                  onClick={() => setOutputMode("zip")}
                  className={`px-2.5 py-1 rounded-md text-[12px] transition-all ${
                    outputMode === "zip"
                      ? "bg-surface-variant text-on-surface shadow-sm"
                      : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  Separate files
                </button>
                <button
                  onClick={() => setOutputMode("merged")}
                  className={`px-2.5 py-1 rounded-md text-[12px] transition-all ${
                    outputMode === "merged"
                      ? "bg-surface-variant text-on-surface shadow-sm"
                      : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  One PDF
                </button>
              </div>
            </div>
          )}
          {picked.length > 0 && outputMode === "zip" && (
            <label className="flex items-center gap-2 px-0.5 text-caption text-on-surface-variant">
              <span className="flex items-center gap-1.5 shrink-0">
                <span className="material-symbols-outlined text-[14px]">sell</span>
                Name files by
              </span>
              <select
                value={filenameValid ? filenameId : ""}
                onChange={(e) => setFilenameId(e.target.value ? Number(e.target.value) : null)}
                className="flex-1 min-w-0 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1.5 px-2 text-body-md text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container"
              >
                <option value="">Row number — row_0001.pdf</option>
                {pickedSpans.map((s) => (
                  <option key={s.id} value={s.id}>
                    {label(s)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {picked.length > 0 && (
            <div className="flex items-center justify-between px-0.5">
              <span className="text-caption text-on-surface-variant flex items-center gap-1">
                <span className="material-symbols-outlined text-[14px]">cloud_done</span>
                Saved automatically
              </span>
              <button
                onClick={startOver}
                className="text-caption text-on-surface-variant hover:text-error transition-colors"
              >
                Start over
              </button>
            </div>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => showPreview(previewIdx ?? 0)}
              disabled={previewBusy || busy || !picked.length || !rows.length}
              title="See what one generated document looks like"
              className="shrink-0 px-3 py-2.5 rounded-lg border border-outline-variant/50 text-on-surface hover:bg-surface-container-high transition-colors font-label-md text-sm flex items-center gap-1.5 disabled:opacity-40"
            >
              <span className="material-symbols-outlined text-[18px]">visibility</span>
              {previewBusy ? "…" : "Preview"}
            </button>
            <button
              onClick={process}
              disabled={busy || !picked.length || !rows.length}
              className="flex-1 bg-secondary-container hover:bg-[#003ea8] text-white py-2.5 rounded-lg font-label-md text-sm shadow-[0_0_20px_rgba(0,83,219,0.3)] transition-all flex justify-center items-center gap-2 border border-outline-variant/50 disabled:opacity-40"
            >
              <span className="material-symbols-outlined text-[18px]">bolt</span>
              {busy
                ? "Generating…"
                : `Generate ${rows.length || ""} PDF${rows.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

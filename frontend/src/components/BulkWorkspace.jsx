import { useEffect, useMemo, useRef, useState } from "react";
import Papa from "papaparse";
import PdfCanvas from "./PdfCanvas.jsx";
import FontPanel from "./FontPanel.jsx";
import { bulkGenerate, editPdf } from "../api.js";
import { effectiveSplit } from "../lib/split.js";
import { cloudEnabled, saveProject, updateProjectSetup } from "../lib/cloud.js";
import SplitPicker from "./SplitPicker.jsx";
import CanvasToolbar from "./CanvasToolbar.jsx";
import Notice from "./Notice.jsx";
import ImportSource from "./ImportSource.jsx";
import { toast } from "./Toast.jsx";
import Icon from "./Icon.jsx";
import { fitWidth } from "../lib/fit.js";

const MAX_ROWS = 500;

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
export default function BulkWorkspace({ file, spans, data, pages, cloudProjectId, onCloudSaved }) {
  const canvasBoxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const [boxH, setBoxH] = useState(0);
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

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Verify-before-download: render one generated document (reuses /edit).
  const [previewIdx, setPreviewIdx] = useState(null); // row being previewed, or null
  const [previewData, setPreviewData] = useState(null); // rendered ArrayBuffer
  const [previewBusy, setPreviewBusy] = useState(false);

  // Cloud save (template PDF + this setup → your account)
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveNote, setSaveNote] = useState(null); // { ok, text }

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
    const update = () => {
      setBoxW(el.clientWidth);
      setBoxH(el.clientHeight);
    };
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

  // Editing a SAVED template → auto-sync its setup back to the cloud (debounced).
  // Skips the first run so just opening it doesn't cause a redundant write.
  const skipCloudRef = useRef(true);
  useEffect(() => {
    skipCloudRef.current = true; // reset when switching which template is open
  }, [cloudProjectId]);
  useEffect(() => {
    if (!cloudProjectId) return;
    if (skipCloudRef.current) {
      skipCloudRef.current = false;
      return;
    }
    const t = setTimeout(() => {
      updateProjectSetup(cloudProjectId, { picked, impMap, filenameId, splits });
    }, 800);
    return () => clearTimeout(t);
  }, [cloudProjectId, picked, impMap, filenameId, splits]);

  const pdfWidth = fitWidth({ boxW, boxH, pages, pageIndex, zoom });

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
    // Positional auto-map: the Nth field you picked ← the Nth CSV column, in the
    // order you clicked them. Deterministic (no fuzzy guessing) — pick fields in
    // the same order as your columns and everything lines up automatically.
    setImpMap(() => {
      const m = {};
      pickedSpans.forEach((s, i) => {
        if (i < hs.length) m[s.id] = hs[i];
      });
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

  // ---- Generate ----
  function process() {
    setError(null);
    setResult(null);
    if (!file) return setError("Load a template PDF in the PDF Editor tab first.");
    if (!picked.length) return setError("Click a place on the document to make it editable.");
    if (!rows.length) return setError("Add at least one document.");
    if (rows.length > MAX_ROWS)
      return setError(
        `Too many documents (${rows.length}). The limit is ${MAX_ROWS} per batch — import fewer rows.`
      );

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
        window.rdTrack?.("bulk_generate", { count: generated, failed, merged: outputMode === "merged" });
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

  // Save this template PDF + its field setup to the account (max 3 on free).
  // Data rows are NOT saved — only the reusable structure — so next time you
  // just paste fresh CSV. The DB trigger surfaces the friendly limit message.
  async function saveToCloud() {
    if (!file || !picked.length) return;
    const name = (window.prompt("Name this template", file.name.replace(/\.pdf$/i, "")) || "").trim();
    if (!name) return;
    setSaveBusy(true);
    setSaveNote(null);
    try {
      const setup = { picked, impMap, filenameId, splits };
      const proj = await saveProject(file, { name, kind: "bulk", setup, pages: pageCount });
      // Become the live project so further edits auto-sync (no duplicate saves).
      skipCloudRef.current = true;
      onCloudSaved?.(proj.id);
      window.rdTrack?.("template_saved", { kind: "bulk" });
      setSaveNote({ ok: true, text: `Saved “${name}” — changes now sync automatically.` });
    } catch (e) {
      setSaveNote({ ok: false, text: e.message || "Couldn't save." });
    } finally {
      setSaveBusy(false);
    }
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

  // Wipe the saved setup for this template and start fresh (undoable).
  function startOver() {
    const prev = { picked, rows, impMap, filenameId, splits };
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
    toast("Setup cleared", {
      actionLabel: "Undo",
      onAction: () => {
        setPicked(prev.picked);
        setRows(prev.rows);
        setImpMap(prev.impMap);
        setFilenameId(prev.filenameId);
        setSplits(prev.splits);
      },
    });
  }

  const pageHasPicks = pickedSpans.some((s) => s.page === pageIndex);
  const currentStep = picked.length === 0 ? 1 : 2;
  const stepHint =
    picked.length === 0
      ? "Click the text on the document you want to change"
      : "Type the new values — each row makes one PDF, then press Generate";

  return (
    // Stacks below lg — see EditorWorkspace for why the scroll moves here.
    <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-3 px-3 pb-3 sm:px-4 sm:pb-4 overflow-y-auto lg:overflow-hidden max-w-[1500px] w-full mx-auto animate-rise">
      {/* Left: the document — click spots to make them editable */}
      <div className="flex-none h-[55vh] min-h-[300px] lg:flex-1 lg:min-w-0 lg:h-auto lg:min-h-0 rounded-[28px] bg-[rgb(var(--c-tint-sand))] flex flex-col overflow-hidden relative">
        {/* Toolbar */}
        <CanvasToolbar
          pageIndex={pageIndex}
          pageCount={pageCount}
          setPageIndex={setPageIndex}
          zoom={zoom}
          setZoom={setZoom}
        />

        {/* Preview banner — step through generated documents */}
        {previewIdx != null && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 z-20 bg-secondary-container text-white rounded-full pl-4 pr-3 py-2 flex items-center gap-2.5 shadow-panel text-[14px]">
            <Icon name="eye" size={16} />
            Preview · document {previewIdx + 1} / {rows.length}
            <div className="w-px h-4 bg-white/30 mx-0.5"></div>
            <button
              disabled={previewIdx === 0 || previewBusy}
              onClick={() => showPreview(previewIdx - 1)}
              className="disabled:opacity-30 hover:opacity-80 transition-opacity"
            >
              <Icon name="chevleft" size={16} />
            </button>
            <button
              disabled={previewIdx >= rows.length - 1 || previewBusy}
              onClick={() => showPreview(previewIdx + 1)}
              className="disabled:opacity-30 hover:opacity-80 transition-opacity"
            >
              <Icon name="chevright" size={16} />
            </button>
            <button onClick={exitPreview} title="Exit preview" className="ml-0.5 hover:opacity-80 transition-opacity">
              <Icon name="close" size={16} />
            </button>
          </div>
        )}

        {/* Hint */}
        {previewIdx == null && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 z-10 bg-surface/95 backdrop-blur-md rounded-full px-4 py-2 text-[13px] sm:text-[14px] text-on-surface shadow-panel flex items-center gap-2 max-w-[92%] whitespace-nowrap overflow-hidden">
            <Icon name="target" size={14} className="text-accent-cyan" />
            {pickedSpans.length
              ? `${pickedSpans.length} selected — click text to add, click again to remove`
              : "Click any text or number you want to change"}
          </div>
        )}

        <div ref={canvasBoxRef} className="flex-1 overflow-auto px-6 pt-16 pb-20 flex justify-center">
          {file && data ? (
            <div className="paper-shadow rounded-sm h-fit">
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
              <Icon name="doc" size={40} className="opacity-40" />
              <p className="mt-2 text-body-md">Load a PDF in the PDF Editor tab to start.</p>
            </div>
          )}
        </div>
      </div>

      {/* Right: values for only the picked fields */}
      <div className="ink-scope flex-none lg:w-[540px] min-h-[45vh] lg:min-h-0 rounded-[28px] bg-[rgb(var(--c-sidebar))] text-on-surface flex flex-col overflow-hidden">
        {/* Header + guided steps */}
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 className="font-display-md font-black text-[24px] leading-none tracking-[-0.4px]">Bulk generator</h2>
            </div>
            {picked.length > 0 && (
              <button
                onClick={() => {
                  setShowImport((v) => !v);
                  setError(null);
                }}
                className={`shrink-0 px-4 py-2 rounded-full text-[14px] flex items-center gap-1.5 transition-colors ${
                  showImport
                    ? "bg-secondary-container text-white"
                    : "bg-black/25 text-on-surface hover:bg-[rgb(var(--c-field))]"
                }`}
              >
                <Icon name="upload" size={16} />
                Import list
              </button>
            )}
          </div>

          {/* One plain guidance line for the current step */}
          <div className="mt-3 flex items-center gap-2.5 text-[14px]">
            <span className="w-6 h-6 shrink-0 rounded-full bg-[#f7e7a6] text-[#2d2323] flex items-center justify-center text-[12px] font-bold">
              {currentStep}
            </span>
            <span className="text-on-surface-variant">{stepHint}</span>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto">
          {picked.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center p-6 text-on-surface-variant animate-fade">
              <span className="w-16 h-16 rounded-2xl bg-black/25 grid place-items-center text-accent-cyan"><Icon name="target" size={30} /></span>
              <p className="mt-4 font-display-md font-black text-[22px] tracking-[-0.3px] text-on-surface">
                Click on the document to start
              </p>
              <p className="mt-1 text-body-md max-w-[270px]">
                Tap any text or number on the PDF — a client name, a date, a price. It appears here
                so you can type a new value. Everything you don't touch stays the same.
              </p>
              <div className="mt-3 flex items-center gap-1.5 text-accent-cyan text-label-md">
                <Icon name="back" size={18} />
                the document is right here
              </div>
              {spans.length > 0 && (
                <button
                  onClick={runExample}
                  className="mt-5 px-5 py-2.5 rounded-full bg-[rgb(var(--c-field))] text-on-surface hover:bg-[rgb(var(--c-field-hover))] transition-colors text-[15px] flex items-center gap-2"
                >
                  <Icon name="spark" size={18} />
                  Show me an example
                </button>
              )}
            </div>
          ) : showImport ? (
            /* ---- Import panel ---- */
            <div className="p-4 space-y-4 animate-drop">
              <ImportSource
                tab={impTab}
                setTab={setImpTab}
                onFile={impUpload}
                onLoadPaste={impLoadPaste}
                text={impText}
                setText={setImpText}
                top={
                  <label className="flex items-center gap-2 text-caption text-on-surface-variant cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={impFirstRow}
                      onChange={(e) => setImpFirstRow(e.target.checked)}
                      className="accent-secondary-container w-4 h-4"
                    />
                    First row is the header
                  </label>
                }
              />

              {impHeaders.length > 0 && (
                <div className="space-y-2">
                  <p className="text-caption text-on-surface-variant">
                    Matched in order — field 1 ← column 1, field 2 ← column 2 …
                    ({impRows.length} rows). Adjust any below if needed.
                  </p>
                  {pickedSpans.map((s) => (
                    <div key={s.id} className="flex items-center gap-2">
                      <span className="flex-1 text-body-md text-on-surface truncate" title={s.text}>
                        {label(s)}
                      </span>
                      <Icon name="arrowright" size={16} className="text-on-surface-variant" />
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
                        className="flex-1 bg-[rgb(var(--c-field))] rounded-2xl py-2 px-3 text-[14px] text-on-surface focus:outline-none focus:ring-2 focus:ring-secondary-container"
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
                    className="w-full mt-2 bg-secondary-container hover:bg-secondary-container-hover text-white py-3 rounded-full text-[15px] flex items-center justify-center gap-2 transition-colors"
                  >
                    <Icon name="check" size={18} />
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
                <div className="px-5 py-2 text-[13px] text-on-surface-variant shrink-0">
                  Each row = one PDF. Edit only what changes. Hover a column, click ⋯ to edit only part of it.
                </div>
              )}
              <div className="overflow-auto flex-1">
              <table className="w-full border-collapse text-left">
                <thead className="sticky top-0 z-10 bg-[rgb(var(--c-sidebar))]">
                  <tr>
                    <th className="w-12 px-2 py-2.5 text-center text-caption text-on-surface-variant border-b border-white/10">
                      Copy
                    </th>
                    {pickedSpans.map((s) => (
                      <th
                        key={s.id}
                        onMouseEnter={() => setHoverId(s.id)}
                        onMouseLeave={() => setHoverId((h) => (h === s.id ? null : h))}
                        className="group min-w-[140px] px-3 py-2.5 border-b border-white/10"
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
                                : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 text-on-surface-variant hover:text-secondary-container"
                            }`}
                          >
                            <Icon name="dots" size={16} />
                          </button>
                          <button
                            onClick={() => togglePick(s.id)}
                            title="Remove this field"
                            className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 text-on-surface-variant hover:text-error transition-all"
                          >
                            <Icon name="close" size={16} />
                          </button>
                        </div>
                      </th>
                    ))}
                    <th className="w-8 border-b border-white/10"></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, r) => (
                    <tr key={r} className="group hover:bg-surface-container-high/40">
                      <td className="px-2 text-center text-caption text-on-surface-variant border-b border-white/[0.06]">
                        {r + 1}
                      </td>
                      {pickedSpans.map((s) => {
                        const val = row[s.id] ?? "";
                        const changed = val !== original(s.id);
                        const lbl = labelOf(s.id);
                        return (
                          <td key={s.id} className="border-b border-white/[0.06] p-0">
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
                                className={`flex-1 min-w-0 bg-transparent px-3 py-2 text-body-md text-on-surface focus:outline-none focus:bg-[rgb(var(--c-field))] focus:ring-2 focus:ring-inset focus:ring-secondary-container ${
                                  changed ? "text-accent-cyan" : ""
                                }`}
                              />
                            </div>
                          </td>
                        );
                      })}
                      <td className="px-1 text-center border-b border-white/[0.06]">
                        <div className="flex items-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 transition-opacity">
                          <button
                            onClick={() => dupDoc(r)}
                            title="Duplicate this copy"
                            className="p-1 text-on-surface-variant hover:text-secondary transition-colors"
                          >
                            <Icon name="copy" size={16} />
                          </button>
                          {rows.length > 1 && (
                            <button
                              onClick={() => delDoc(r)}
                              title="Delete this copy"
                              className="p-1 text-on-surface-variant hover:text-error transition-colors"
                            >
                              <Icon name="close" size={16} />
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
                className="text-accent-cyan text-[14px] flex items-center gap-1.5 hover:underline px-5 py-3"
              >
                <Icon name="plus" size={16} /> Add another copy
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
        <div className="p-4 pt-3 space-y-2.5 bg-[rgb(var(--c-sidebar))]">
          {file && <FontPanel file={file} />}
          {error && <Notice tone="error">{error}</Notice>}
          {result && (
            <Notice tone="success">
              Generated {result.generated} PDF(s)
              {result.failed ? ` · ${result.failed} skipped` : ""} —{" "}
              {result.merged ? "merged PDF" : "ZIP"} downloaded.
            </Notice>
          )}
          {picked.length > 0 && (
            <div className="flex items-center gap-2 px-0.5 text-caption text-on-surface-variant">
              <span className="flex items-center gap-1.5 shrink-0">
                <Icon name="zip" size={14} />
                Output
              </span>
              <div className="flex items-center gap-1 bg-black/25 rounded-full p-1">
                <button
                  onClick={() => setOutputMode("zip")}
                  className={`px-3.5 py-1.5 rounded-full text-[13px] transition-all ${
                    outputMode === "zip"
                      ? "bg-primary text-on-primary"
                      : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  Separate files
                </button>
                <button
                  onClick={() => setOutputMode("merged")}
                  className={`px-3.5 py-1.5 rounded-full text-[13px] transition-all ${
                    outputMode === "merged"
                      ? "bg-primary text-on-primary"
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
                <Icon name="tag" size={14} />
                Name files by
              </span>
              <select
                value={filenameValid ? filenameId : ""}
                onChange={(e) => setFilenameId(e.target.value ? Number(e.target.value) : null)}
                className="flex-1 min-w-0 bg-[rgb(var(--c-field))] rounded-2xl py-2 px-3 text-[14px] text-on-surface focus:outline-none focus:ring-2 focus:ring-secondary-container"
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
              {!cloudEnabled ? (
                <span className="text-caption text-on-surface-variant flex items-center gap-1">
                  <Icon name="cloud" size={14} />
                  Saved on this device
                </span>
              ) : cloudProjectId ? (
                <span
                  className="text-caption text-secondary flex items-center gap-1"
                  title="This is a saved template — your changes sync automatically."
                >
                  <Icon name="cloud" size={14} />
                  Synced to your account
                </span>
              ) : (
                <button
                  onClick={saveToCloud}
                  disabled={saveBusy}
                  title="Keep this template + setup in your account (any device)"
                  className="text-caption text-secondary hover:underline flex items-center gap-1 disabled:opacity-50"
                >
                  <Icon name={saveBusy ? "spinner" : "cloudup"} size={14} spin={saveBusy} />
                  {saveBusy ? "Saving…" : "Save to account"}
                </button>
              )}
              <button
                onClick={startOver}
                className="text-caption text-on-surface-variant hover:text-error transition-colors"
              >
                Start over
              </button>
            </div>
          )}
          {saveNote && (
            <Notice tone={saveNote.ok ? "success" : "error"} icon={saveNote.ok ? "cloud" : "warning"}>
              {saveNote.text}
            </Notice>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => showPreview(previewIdx ?? 0)}
              disabled={previewBusy || busy || !picked.length || !rows.length}
              title="See what one generated document looks like"
              className="shrink-0 px-5 py-3 rounded-full bg-black/25 text-on-surface hover:bg-[rgb(var(--c-field))] transition-colors text-[15px] flex items-center gap-2 disabled:opacity-40"
            >
              <Icon name="eye" size={18} />
              {previewBusy ? "…" : "Preview"}
            </button>
            <button
              onClick={process}
              disabled={busy || !picked.length || !rows.length}
              className="flex-1 bg-secondary-container hover:bg-secondary-container-hover hover:shadow-[0_8px_22px_rgba(79,117,254,0.35)] text-white py-3 rounded-full text-[15px] transition-all flex justify-center items-center gap-2 disabled:opacity-40"
            >
              <Icon name="bolt" size={18} />
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

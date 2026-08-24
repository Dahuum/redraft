import { useEffect, useMemo, useRef, useState } from "react";
import Papa from "papaparse";
import PdfCanvas from "./PdfCanvas.jsx";
import FontPanel from "./FontPanel.jsx";
import { annexModel, annexGenerate } from "../api.js";
import { getTemplate, saveTemplate } from "../lib/templates.js";

const MAX_ROWS = 500;

// NFD decomposes accents (é → e + ◌́); stripping non-alphanumerics then drops the
// mark, so "Période" ≈ "Periode" and "Référence" ≈ "Reference".
const norm = (s) =>
  (s || "").toLowerCase().normalize("NFD").replace(/[^a-z0-9]/g, "");

// fr / Morocco number format ('.' groups, ',' decimals) — mirrors the backend so
// the client-side "review before download" preview matches the generated PDFs.
const parseNum = (t) => {
  const s = String(t ?? "").replace(/\s/g, "").trim();
  if (!s) return null;
  const n = parseFloat(s.replace(/\./g, "").replace(",", "."));
  return Number.isFinite(n) ? n : null;
};
const fmtNum = (v, d = 2) =>
  v
    .toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })
    .replace(/,/g, "\x00")
    .replace(/\./g, ",")
    .replace(/\x00/g, ".");

// Local cache key for the scanned layout template. The source of truth is the
// per-account Supabase store (see lib/templates.js); this localStorage copy is
// the offline / not-signed-in fallback. Keyed by annex file name →
// "scan once, reuse every month".
const tplKey = (name) => `redraft:annexTemplate:${name || "annex"}`;

function matchColumn(label, columns) {
  const L = norm(label);
  if (!L) return null;
  return columns.find((c) => {
    const C = norm(c);
    return C && (C.includes(L) || L.includes(C));
  }) || null;
}

// Fuzzy-match each detected line to a data column, by label.
function autoMap(items, columns) {
  const m = {};
  for (const it of items) {
    const hit = matchColumn(it.label, columns);
    if (hit) m[it.index] = hit;
  }
  return m;
}

// Fuzzy-match each header field (facture N°, client, ICE…) to a data column.
function autoMapHeaders(headers, columns) {
  const m = {};
  for (const h of headers) {
    const hit = matchColumn(h.label, columns);
    if (hit) m[h.key] = hit;
  }
  return m;
}

/**
 * Annex automation (Layer 3). Reads the annex into line items (/annex/model),
 * lets you link each line to a column of your client data, and generates one
 * annex per client (/annex/generate): a 0/empty quantity removes that line and
 * the Total HT is recomputed. Engine untouched.
 */
function NumField({ label, value, onChange }) {
  return (
    <label className="flex items-center gap-1.5 text-caption text-on-surface-variant">
      <span className="shrink-0">{label}</span>
      <input
        type="number"
        step="0.5"
        value={Number.isFinite(value) ? value : ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        className="w-[72px] bg-surface-container-lowest border border-outline-variant/50 rounded-md py-1 px-1.5 text-[13px] text-on-surface tabular-nums focus:outline-none focus:ring-1 focus:ring-secondary-container"
      />
    </label>
  );
}

const ADJUST_COLS = [
  ["qty", "Qty"],
  ["price", "Price"],
  ["amount", "Amount"],
  ["unit", "Unit"],
  ["label", "Label ends before"],
];

export default function AnnexWorkspace({ file, spans, data, pages }) {
  const canvasBoxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(1);

  const [model, setModel] = useState(null);
  const [modelStatus, setModelStatus] = useState("idle"); // idle|loading|ready|error
  const [modelError, setModelError] = useState(null);
  const [template, setTemplate] = useState(null); // saved layout (scan-once)
  const [reused, setReused] = useState(false);    // template came from storage
  const [filenameCol, setFilenameCol] = useState(""); // data column → output filename
  const [showReview, setShowReview] = useState(false);

  const [mapping, setMapping] = useState({}); // { itemIndex: column }
  const [headerMapping, setHeaderMapping] = useState({}); // { headerKey: column }
  const [hoverLine, setHoverLine] = useState(null); // item index highlighted
  const [hoverHeader, setHoverHeader] = useState(null); // header key highlighted
  const [hoverSection, setHoverSection] = useState(null); // section group index highlighted

  const [showImport, setShowImport] = useState(false);
  const [impTab, setImpTab] = useState("upload"); // upload | paste
  const [impText, setImpText] = useState("");
  const [impHeaders, setImpHeaders] = useState([]);
  const [impRows, setImpRows] = useState([]); // array of row objects keyed by header
  const impUploadRef = useRef(null);

  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  // Layout adjustment — fix what detect_template got wrong, then re-scan.
  const [adjusting, setAdjusting] = useState(false);
  const [draftTmpl, setDraftTmpl] = useState(null);
  const [armCol, setArmCol] = useState(null); // column awaiting a canvas click
  const [scanning, setScanning] = useState(false);

  const pageCount = (pages && pages.length) || 1;

  // ---- Read the annex into a model whenever the file changes ----
  useEffect(() => {
    if (!file) {
      setModel(null);
      setModelStatus("idle");
      return;
    }
    let cancelled = false;
    setModelStatus("loading");
    setModelError(null);
    (async () => {
      // Prefer the per-account saved layout (Supabase, cross-device); fall back
      // to this device's localStorage cache when offline / not signed in.
      let saved = await getTemplate(file.name);
      const fromAccount = Boolean(saved);
      if (!saved) {
        try {
          saved = JSON.parse(localStorage.getItem(tplKey(file.name)) || "null");
        } catch {
          saved = null;
        }
      }
      if (cancelled) return;
      try {
        const m = await annexModel(file, saved);
        if (cancelled) return;
        const tmpl = m.template || saved || null;
        setModel(m);
        setTemplate(tmpl);
        setReused(Boolean(saved));
        setModelStatus("ready");
        // Persist the layout for next month: local cache + account (so it
        // syncs across devices). Migrates a local-only template up on reuse.
        if (tmpl) {
          try {
            localStorage.setItem(tplKey(file.name), JSON.stringify(tmpl));
          } catch {
            /* storage blocked/full — reuse just won't persist locally */
          }
          if (!fromAccount) saveTemplate(file.name, tmpl); // best-effort
        }
      } catch (e) {
        if (cancelled) return;
        setModelError(e.message || "Couldn't read this annex.");
        setModelStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [file]);

  // Measure the pane so the PDF fits its width.
  useEffect(() => {
    const el = canvasBoxRef.current;
    if (!el) return;
    const update = () => setBoxW(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [file, modelStatus]);

  const items = model?.items || [];
  const headerFields = model?.headers || [];
  const idToItem = useMemo(() => {
    const m = new Map();
    for (const it of items) for (const id of it.ids || []) m.set(id, it.index);
    return m;
  }, [items]);
  const headerBySpan = useMemo(() => {
    const m = new Map();
    for (const h of headerFields) m.set(h.spanId, h);
    return m;
  }, [headerFields]);
  const spanById = useMemo(
    () => new Map((spans || []).map((s) => [s.id, s])),
    [spans]
  );

  // Group lines by section for display.
  const groups = useMemo(() => {
    const out = [];
    let cur = null;
    for (const it of items) {
      if (!cur || cur.section !== it.section) {
        cur = { section: it.section, items: [] };
        out.push(cur);
      }
      cur.items.push(it);
    }
    return out;
  }, [items]);

  // Full-row band (spans the whole table width) for a set of span ids.
  const TABLE_L = 22;
  const TABLE_R = 543;
  function bandOf(ids) {
    const boxes = (ids || []).map((id) => spanById.get(id)?.bbox).filter(Boolean);
    if (!boxes.length) return null;
    return {
      x0: TABLE_L,
      x1: TABLE_R,
      y0: Math.min(...boxes.map((b) => b[1])) - 2,
      y1: Math.max(...boxes.map((b) => b[3])) + 2,
      page: spanById.get(ids[0])?.page ?? 0,
    };
  }

  // What lights up on the PDF: every line faint; the hovered line/section/field strong.
  const highlightRects = useMemo(() => {
    const rects = [];
    for (const it of items) {
      const b = bandOf(it.ids);
      if (b)
        rects.push({
          key: `line-${it.index}`,
          ...b,
          variant: hoverLine === it.index ? "strong" : "faint",
        });
    }
    if (hoverSection != null && groups[hoverSection]) {
      const b = bandOf(groups[hoverSection].items.flatMap((it) => it.ids));
      if (b) rects.push({ key: `sec-${hoverSection}`, ...b, variant: "parent" });
    }
    if (hoverHeader != null) {
      const h = headerFields.find((x) => x.key === hoverHeader);
      const sp = h && spanById.get(h.spanId);
      if (sp)
        rects.push({
          key: `hdr-${h.key}`,
          x0: sp.bbox[0] - 3,
          y0: sp.bbox[1] - 2,
          x1: sp.bbox[2] + 3,
          y1: sp.bbox[3] + 2,
          page: sp.page ?? 0,
          variant: "strong",
        });
    }
    return rects;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, groups, hoverLine, hoverSection, hoverHeader, headerFields, spanById]);

  const pdfWidth = Math.max(260, Math.round(((boxW || 640) - 48) * zoom));
  const mappedCount = Object.values(mapping).filter(Boolean).length;
  const headerMappedCount = Object.values(headerMapping).filter(Boolean).length;

  // Master Total HT (before) — same for every client; the per-client "after" is
  // recomputed below.
  const masterTotal = useMemo(() => {
    const t = parseNum(model?.total?.value);
    return t != null
      ? t
      : items.reduce((a, it) => a + (parseNum(it.amount) || 0), 0);
  }, [model, items]);

  // Verify-before-download: compute each client's result the same way the backend
  // will (0/empty qty removes a line; Total HT recomputed) so the user can trust
  // the batch before generating a single PDF.
  const preview = useMemo(() => {
    if (!items.length || !impRows.length) return [];
    const nameCol = filenameCol || headerMapping.clientName || null;
    return impRows.map((row, ri) => {
      let kept = 0;
      let removed = 0;
      let after = 0;
      for (const it of items) {
        const col = mapping[it.index];
        if (col) {
          const q = parseNum(row[col]);
          if (!q) {
            removed += 1;
            continue;
          }
          after += Math.round(q * (parseNum(it.unitPrice) || 0) * 100) / 100;
          kept += 1;
        } else {
          after += parseNum(it.amount) || 0;
          kept += 1;
        }
      }
      const name =
        nameCol && String(row[nameCol] ?? "").trim()
          ? String(row[nameCol]).trim()
          : `Client ${ri + 1}`;
      return { name, kept, removed, after: Math.round(after * 100) / 100 };
    });
  }, [items, impRows, mapping, filenameCol, headerMapping]);

  function onCanvasSelect(spanId) {
    if (spanId == null) {
      setHoverLine(null);
      setHoverHeader(null);
      return;
    }
    if (adjusting && armCol) {
      const sp = spanById.get(spanId);
      if (sp) assignSpanToColumn(sp, armCol);
      setArmCol(null);
      return;
    }
    const idx = idToItem.get(spanId);
    if (idx != null) {
      setHoverLine(idx);
      setHoverHeader(null);
      return;
    }
    const h = headerBySpan.get(spanId);
    if (h) {
      setHoverHeader(h.key);
      setHoverLine(null);
    }
  }

  // ---- Import client data ----
  function ingestParsed(res) {
    const headers = (res.meta && res.meta.fields) || [];
    const rows = (res.data || []).filter((r) =>
      Object.values(r).some((v) => String(v).trim() !== "")
    );
    if (!headers.length || !rows.length) {
      setError("No columns/rows found in that data.");
      return;
    }
    setImpHeaders(headers);
    setImpRows(rows);
    setMapping(autoMap(items, headers));
    setHeaderMapping(autoMapHeaders(headerFields, headers));
    setShowImport(false);
    setResult(null);
    setError(null);
  }
  function impUpload(f) {
    if (!f) return;
    Papa.parse(f, { header: true, skipEmptyLines: true, complete: ingestParsed });
  }
  function impLoadPaste() {
    if (!impText.trim()) return setError("Paste some rows first.");
    ingestParsed(Papa.parse(impText.trim(), { header: true, skipEmptyLines: true }));
  }

  // ---- Layout adjustment ----
  function openAdjust() {
    setDraftTmpl(template ? JSON.parse(JSON.stringify(template)) : null);
    setArmCol(null);
    setAdjusting(true);
  }
  function closeAdjust() {
    setAdjusting(false);
    setArmCol(null);
    setDraftTmpl(null);
  }

  function patchColumn(col, patch) {
    setDraftTmpl((t) => {
      if (!t) return t;
      const cols = { ...(t.columns || {}) };
      cols[col] = { ...(cols[col] || {}), ...patch };
      return { ...t, columns: cols };
    });
  }
  function patchRegion(key, value) {
    setDraftTmpl((t) =>
      t ? { ...t, tableRegion: { ...(t.tableRegion || {}), [key]: value } } : t
    );
  }

  // Re-point a column band from one real cell the user clicked: numeric cols
  // match on the right edge (x1), unit on the centre (xc), label on where the
  // label zone ends. Floors fall back into the inter-column gap.
  function assignSpanToColumn(span, colKey) {
    const b = span.bbox;
    if (!b || !draftTmpl) return;
    if (colKey === "label") {
      patchColumn("label", { by: "x0", max: b[2] + 4 });
      return;
    }
    if (colKey === "unit") {
      patchColumn("unit", { by: "xc", min: b[0] - 10, max: b[2] + 10 });
      return;
    }
    const floor = Math.max(draftTmpl?.tableEdges?.x0 ?? 0, b[0] - 36);
    patchColumn(colKey, { by: "x1", min: b[0] - 8, max: b[2] + 8, floor });
  }

  // Re-run the fuzzy label↔column matching against a freshly scanned model,
  // so a layout fix doesn't throw away an already-loaded data file.
  function remapFromModel(m) {
    if (!impHeaders.length) {
      setMapping({});
      setHeaderMapping({});
      return;
    }
    setMapping(autoMap(m.items || [], impHeaders));
    setHeaderMapping(autoMapHeaders(m.headers || [], impHeaders));
  }

  async function persistTemplate(tmpl) {
    try {
      localStorage.setItem(tplKey(file.name), JSON.stringify(tmpl));
    } catch {
      /* storage blocked — reuse just won't persist locally */
    }
    saveTemplate(file.name, tmpl); // best-effort account sync
  }

  async function applyRescan() {
    if (!file || !draftTmpl) return;
    setScanning(true);
    setError(null);
    try {
      const m = await annexModel(file, draftTmpl);
      const tmpl = m.template || draftTmpl;
      setModel(m);
      setTemplate(tmpl);
      await persistTemplate(tmpl);
      remapFromModel(m);
      setResult(null);
      window.rdTrack?.("annex_layout_adjusted");
      closeAdjust();
    } catch (e) {
      setError(e.message || "Couldn't re-scan with this layout.");
    } finally {
      setScanning(false);
    }
  }

  async function redetectFresh() {
    if (!file) return;
    setScanning(true);
    setError(null);
    try {
      const m = await annexModel(file, null);
      const tmpl = m.template || null;
      setModel(m);
      setTemplate(tmpl);
      remapFromModel(m);
      setResult(null);
      setDraftTmpl(tmpl ? JSON.parse(JSON.stringify(tmpl)) : null);
    } catch (e) {
      setError(e.message || "Couldn't re-detect.");
    } finally {
      setScanning(false);
    }
  }

  // ---- Generate ----
  function generate() {
    setError(null);
    setResult(null);
    if (!file) return setError("Open the annex in the PDF Editor tab first.");
    if (modelStatus !== "ready") return setError("Still reading the annex…");
    if (!impRows.length) return setError("Load your client data first.");
    if (impRows.length > MAX_ROWS)
      return setError(
        `Too many clients (${impRows.length}). The limit is ${MAX_ROWS} per batch — split your data.`
      );
    const map = Object.fromEntries(Object.entries(mapping).filter(([, h]) => h));
    const hmap = Object.fromEntries(Object.entries(headerMapping).filter(([, h]) => h));
    if (!Object.keys(map).length && !Object.keys(hmap).length)
      return setError("Link at least one line or field to a data column.");

    setBusy(true);
    const csv = Papa.unparse({ fields: impHeaders, data: impRows });
    const csvFile = new File([csv], "clients.csv", { type: "text/csv" });
    annexGenerate(file, csvFile, map, hmap, template, filenameCol)
      .then(({ blob, generated, failed }) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${(file.name || "annex").replace(/\.pdf$/i, "")}_annexes.zip`;
        a.click();
        URL.revokeObjectURL(url);
        setResult({ generated, failed });
        window.rdTrack?.("annex_generate", { count: generated, failed });
      })
      .catch((e) => setError(e.message || "Generation failed."))
      .finally(() => setBusy(false));
  }

  const dataLoaded = impRows.length > 0;

  return (
    <div className="flex-1 flex gap-4 p-4 overflow-hidden max-w-[1500px] w-full mx-auto animate-rise">
      {/* Left: the annex with detected lines highlighted */}
      <div className="flex-[0.58] bg-surface-container-lowest rounded-xl border border-outline-variant/30 flex flex-col overflow-hidden relative">
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-surface/90 backdrop-blur-md border border-outline-variant/50 rounded-full px-3 py-1.5 flex items-center gap-3 z-10 shadow-xl">
          <button
            disabled={pageIndex === 0}
            onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
            aria-label="Previous page"
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
            aria-label="Next page"
            className="text-on-surface-variant hover:text-primary transition-colors disabled:opacity-30"
          >
            <span className="material-symbols-outlined text-[18px]">chevron_right</span>
          </button>
          <div className="w-px h-4 bg-outline-variant"></div>
          <button
            onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}
            aria-label="Zoom out"
            className="text-on-surface-variant hover:text-primary transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">zoom_out</span>
          </button>
          <span className="text-caption font-medium">{Math.round(zoom * 100)}%</span>
          <button
            onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(2)))}
            aria-label="Zoom in"
            className="text-on-surface-variant hover:text-primary transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">zoom_in</span>
          </button>
        </div>

        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 bg-surface/90 backdrop-blur-md border border-outline-variant/50 rounded-full px-3 py-1 text-caption text-on-surface-variant shadow-lg flex items-center gap-1.5">
          <span className="material-symbols-outlined text-[14px] text-accent-cyan">
            {reused ? "bookmark" : "rule"}
          </span>
          {adjusting
            ? armCol
              ? `Click a ${armCol} cell on the page…`
              : "Pick a column chip, then click its first data cell"
            : modelStatus === "ready"
            ? `${items.length} line${items.length === 1 ? "" : "s"} detected${
                reused ? " · saved layout" : ""
              } — hover a row to find it`
            : "Reading the annex…"}
        </div>

        <div ref={canvasBoxRef} className="flex-1 overflow-auto p-6 flex justify-center bg-on-surface/[0.04]">
          {file && data ? (
            <div className="paper-shadow rounded-sm mt-12 mb-10 h-fit">
              <PdfCanvas
                data={data}
                pageIndex={pageIndex}
                spans={spans}
                highlightRects={highlightRects}
                onSelect={onCanvasSelect}
                maxWidth={pdfWidth}
              />
            </div>
          ) : (
            <div className="self-center text-center text-on-surface-variant">
              <span className="material-symbols-outlined text-[40px] opacity-40">description</span>
              <p className="mt-2 text-body-md">Open an annex in the PDF Editor tab to start.</p>
            </div>
          )}
        </div>
      </div>

      {/* Right: the lines + data linking */}
      <div className="flex-[0.42] bg-surface-container rounded-xl border border-outline-variant/30 flex flex-col shadow-panel overflow-hidden">
        <div className="px-4 pt-4 pb-3 border-b border-outline-variant/30 bg-surface/50 backdrop-blur-md">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <h2 className="font-display-md text-xl font-bold tracking-tight">Annex automation</h2>
              <p className="text-caption text-on-surface-variant mt-0.5">
                One annex per client. A line whose column is <b>0</b> or empty is removed; the
                Total&nbsp;HT recomputes.
              </p>
            </div>
            {modelStatus === "ready" && !adjusting && (
              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  onClick={openAdjust}
                  title="Fix what was auto-detected"
                  aria-label="Adjust layout"
                  className="shrink-0 px-2.5 py-2 rounded-lg border border-outline-variant/40 text-on-surface-variant hover:text-on-surface hover:border-accent-cyan/50 transition-colors"
                >
                  <span className="material-symbols-outlined text-[16px]">tune</span>
                </button>
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
                  <span className="material-symbols-outlined text-[16px]">table_chart</span>
                  {dataLoaded ? `${impRows.length} clients` : "Load data"}
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-auto">
          {!adjusting && (
          <>
          {modelStatus === "loading" && (
            <div className="h-full flex flex-col items-center justify-center text-on-surface-variant animate-fade">
              <span className="material-symbols-outlined text-[40px] animate-pulse">rule</span>
              <p className="mt-2 text-body-md">Reading the annex…</p>
            </div>
          )}

          {modelStatus === "error" && (
            <div className="p-4">
              <div className="rounded-lg px-3 py-2 text-caption flex items-center gap-2 border border-error/30 bg-error/10 text-error">
                <span className="material-symbols-outlined text-[16px]">error</span>
                {modelError}
              </div>
            </div>
          )}

          {modelStatus === "ready" && items.length === 0 && headerFields.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-center p-6 text-on-surface-variant">
              <span className="material-symbols-outlined text-[40px] opacity-40">search_off</span>
              <p className="mt-2 text-body-md max-w-[260px]">
                No line items detected. This screen is built for line-item annexes (table of
                services with quantities).
              </p>
            </div>
          )}

          {modelStatus === "ready" && (items.length > 0 || headerFields.length > 0) && showImport && (
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
              <p className="text-caption text-on-surface-variant">
                First row = column names (one column per line, holding its quantity). One row per
                client.
              </p>

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
            </div>
          )}

          {modelStatus === "ready" && (items.length > 0 || headerFields.length > 0) && !showImport && (
            <div className="flex flex-col">
              <div className="px-3 py-2 text-caption text-on-surface-variant bg-surface-container-low border-b border-outline-variant/20 sticky top-0 z-10">
                {dataLoaded
                  ? "Link each line to its quantity column; fields to their value column."
                  : "Load your client data above to link columns."}
              </div>

              {headerFields.length > 0 && (
                <>
                  <div className="px-3 pt-3 pb-1 text-label-md font-semibold text-on-surface-variant italic">
                    Document info
                  </div>
                  {headerFields.map((h) => (
                    <div
                      key={h.key}
                      onMouseEnter={() => setHoverHeader(h.key)}
                      onMouseLeave={() => setHoverHeader((x) => (x === h.key ? null : x))}
                      className={`flex items-center gap-2 px-3 py-2 border-b border-outline-variant/10 transition-colors ${
                        hoverHeader === h.key ? "bg-surface-container-high/50" : ""
                      }`}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-body-md text-on-surface truncate">{h.label}</p>
                        <p
                          className="text-caption text-on-surface-variant truncate"
                          title={h.value}
                        >
                          {h.value || "—"}
                        </p>
                      </div>
                      <select
                        disabled={!dataLoaded}
                        value={headerMapping[h.key] || ""}
                        onChange={(e) =>
                          setHeaderMapping((m) => {
                            const n = { ...m };
                            if (e.target.value) n[h.key] = e.target.value;
                            else delete n[h.key];
                            return n;
                          })
                        }
                        className="w-[44%] shrink-0 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1.5 px-2 text-body-md text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container disabled:opacity-40"
                      >
                        <option value="">keep as-is</option>
                        {impHeaders.map((c) => (
                          <option key={c} value={c}>
                            {c}
                          </option>
                        ))}
                      </select>
                    </div>
                  ))}
                </>
              )}

              {items.length > 0 && (
                <div className="px-3 pt-3 pb-1 text-caption uppercase tracking-wide text-on-surface-variant/70">
                  Lines
                </div>
              )}
              {groups.map((g, gi) => (
                <div key={gi} className="mb-1">
                  {/* Parent: section */}
                  <div
                    onMouseEnter={() => setHoverSection(gi)}
                    onMouseLeave={() => setHoverSection((s) => (s === gi ? null : s))}
                    className={`flex items-center gap-2 px-3 py-1.5 cursor-default transition-colors ${
                      hoverSection === gi ? "text-secondary" : "text-on-surface-variant"
                    }`}
                  >
                    <span className="material-symbols-outlined text-[18px] opacity-70">
                      {hoverSection === gi ? "folder_open" : "folder"}
                    </span>
                    <span className="text-label-md font-semibold italic truncate" title={g.section}>
                      {g.section || "—"}
                    </span>
                    <span className="text-caption opacity-60 shrink-0">
                      · {g.items.length} line{g.items.length === 1 ? "" : "s"}
                    </span>
                  </div>

                  {/* Children: lines, indented under the section */}
                  <div className="ml-4 border-l-2 border-outline-variant/25">
                    {g.items.map((it) => (
                      <div
                        key={it.index}
                        onMouseEnter={() => setHoverLine(it.index)}
                        onMouseLeave={() => setHoverLine((h) => (h === it.index ? null : h))}
                        className={`flex items-center gap-2 pl-3 pr-3 py-2 border-l-2 -ml-0.5 transition-colors ${
                          hoverLine === it.index
                            ? "bg-secondary-container/10 border-secondary-container"
                            : "border-transparent"
                        }`}
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-body-md text-on-surface truncate" title={it.label}>
                            {it.label}
                          </p>
                          <p className="text-caption text-on-surface-variant">
                            PU {it.unitPrice || "—"} · {it.unit || ""}
                          </p>
                        </div>
                        <select
                          disabled={!dataLoaded}
                          value={mapping[it.index] || ""}
                          onChange={(e) =>
                            setMapping((m) => {
                              const n = { ...m };
                              if (e.target.value) n[it.index] = e.target.value;
                              else delete n[it.index];
                              return n;
                            })
                          }
                          className="w-[44%] shrink-0 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1.5 px-2 text-body-md text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container disabled:opacity-40"
                        >
                          <option value="">keep as-is</option>
                          {impHeaders.map((h) => (
                            <option key={h} value={h}>
                              {h}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          </>
          )}

          {adjusting && draftTmpl && (
            <div className="p-4 space-y-4 animate-drop">
              <p className="text-caption text-on-surface-variant">
                Wrong column or missing lines? Click a chip, then click one real
                cell of that column on the page — or nudge the numbers below.
              </p>

              <div className="flex flex-wrap gap-1.5">
                {ADJUST_COLS.map(([k, lbl]) => (
                  <button
                    key={k}
                    onClick={() => setArmCol(armCol === k ? null : k)}
                    aria-pressed={armCol === k}
                    className={`px-2.5 py-1 rounded-lg border text-label-md text-[12px] transition-colors ${
                      armCol === k
                        ? "bg-secondary-container text-white border-transparent"
                        : "border-outline-variant/50 text-on-surface-variant hover:text-on-surface hover:border-accent-cyan/50"
                    }`}
                  >
                    {lbl}
                  </button>
                ))}
              </div>
              {armCol && (
                <p className="text-caption text-accent-cyan flex items-center gap-1.5">
                  <span className="material-symbols-outlined text-[14px]">ads_click</span>
                  Now click a “{armCol}” cell on the document.
                </p>
              )}

              <div className="space-y-2 rounded-lg border border-outline-variant/30 bg-surface-container-low p-3">
                <div className="flex flex-wrap gap-x-4 gap-y-1.5 pb-2 border-b border-outline-variant/20">
                  <NumField
                    label="Table top y>"
                    value={draftTmpl.tableRegion?.yTop}
                    onChange={(v) => patchRegion("yTop", v)}
                  />
                  <NumField
                    label="bottom y<"
                    value={draftTmpl.tableRegion?.yBottom}
                    onChange={(v) => patchRegion("yBottom", v)}
                  />
                </div>
                {["qty", "price", "amount", "unit", "label"]
                  .filter((k) => draftTmpl.columns?.[k])
                  .map((k) => {
                    const c = draftTmpl.columns[k];
                    return (
                      <div
                        key={k}
                        className="flex flex-wrap items-center gap-x-4 gap-y-1.5 py-1.5 border-b border-outline-variant/15 last:border-0"
                      >
                        <span className="w-16 shrink-0 font-label-md text-[12px] font-semibold capitalize text-on-surface">
                          {k}
                        </span>
                        {c.min != null && (
                          <NumField label="min x>" value={c.min} onChange={(v) => patchColumn(k, { min: v })} />
                        )}
                        {c.max != null && (
                          <NumField
                            label={k === "label" ? "up to x<" : "max x<"}
                            value={c.max}
                            onChange={(v) => patchColumn(k, { max: v })}
                          />
                        )}
                        {c.floor != null && (
                          <NumField label="floor x>" value={c.floor} onChange={(v) => patchColumn(k, { floor: v })} />
                        )}
                      </div>
                    );
                  })}
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={redetectFresh}
                  disabled={scanning}
                  title="Throw this layout away and auto-detect from scratch"
                  className="shrink-0 px-3 py-2 rounded-lg border border-outline-variant/50 text-on-surface hover:bg-surface-container-high transition-colors text-label-md text-sm disabled:opacity-40"
                >
                  Re-detect
                </button>
                <span className="flex-1" />
                <button
                  onClick={closeAdjust}
                  disabled={scanning}
                  className="shrink-0 px-3 py-2 rounded-lg border border-outline-variant/50 text-on-surface hover:bg-surface-container-high transition-colors text-label-md text-sm disabled:opacity-40"
                >
                  Cancel
                </button>
                <button
                  onClick={applyRescan}
                  disabled={scanning || !draftTmpl}
                  className="shrink-0 px-4 py-2 rounded-lg bg-secondary-container hover:bg-[#003ea8] text-white font-label-md text-sm transition-all disabled:opacity-40 flex items-center gap-1.5"
                >
                  <span
                    className={`material-symbols-outlined text-[16px] ${
                      scanning ? "animate-spin" : ""
                    }`}
                  >
                    {scanning ? "progress_activity" : "radar"}
                  </span>
                  {scanning ? "Scanning…" : "Apply & re-scan"}
                </button>
              </div>
            </div>
          )}
          {adjusting && !draftTmpl && (
            <div className="h-full flex flex-col items-center justify-center gap-3 text-center p-6 text-on-surface-variant">
              <span className="material-symbols-outlined text-[36px] opacity-40">tune</span>
              <p className="text-body-md max-w-[280px]">
                No saved layout to adjust yet — run the detector first, then fix what it found.
              </p>
              <button
                onClick={redetectFresh}
                disabled={scanning}
                className="px-4 py-2 rounded-lg bg-secondary-container text-white text-label-md text-sm flex items-center gap-1.5 disabled:opacity-40"
              >
                <span className={`material-symbols-outlined text-[16px] ${scanning ? "animate-spin" : ""}`}>
                  {scanning ? "progress_activity" : "radar"}
                </span>
                {scanning ? "Scanning…" : "Detect layout now"}
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
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
                : `Generated ${result.generated} annex(es)${
                    result.failed ? ` · ${result.failed} skipped` : ""
                  } — ZIP downloaded.`}
            </div>
          )}
          {modelStatus === "ready" && dataLoaded && (
            <>
              {/* Name each output file by a data column (e.g. client / Facture N°) */}
              <div className="flex items-center gap-2 text-caption">
                <span className="material-symbols-outlined text-[15px] text-on-surface-variant">
                  sell
                </span>
                <span className="text-on-surface-variant shrink-0">Name files by</span>
                <select
                  value={filenameCol}
                  onChange={(e) => setFilenameCol(e.target.value)}
                  className="flex-1 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1 px-2 text-body-md text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container"
                >
                  <option value="">annex_0001.pdf …</option>
                  {impHeaders.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </div>

              {/* Verify before download */}
              <button
                onClick={() => setShowReview((v) => !v)}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg border border-outline-variant/40 text-label-md text-on-surface-variant hover:text-on-surface transition-colors"
              >
                <span className="flex items-center gap-1.5">
                  <span className="material-symbols-outlined text-[16px] text-accent-cyan">
                    fact_check
                  </span>
                  Review {preview.length} client{preview.length === 1 ? "" : "s"} before download
                </span>
                <span className="material-symbols-outlined text-[18px]">
                  {showReview ? "expand_less" : "expand_more"}
                </span>
              </button>
              {showReview && (
                <div className="max-h-48 overflow-auto rounded-lg border border-outline-variant/30 bg-surface-container-lowest animate-drop">
                  <div className="px-3 py-1.5 text-caption text-on-surface-variant sticky top-0 bg-surface-container-low border-b border-outline-variant/20">
                    Total HT before: <b className="text-on-surface">{fmtNum(masterTotal)}</b> — each
                    client recomputed
                  </div>
                  {preview.slice(0, 80).map((p, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 px-3 py-1.5 text-caption border-b border-outline-variant/10 last:border-0"
                    >
                      <span className="flex-1 truncate text-on-surface" title={p.name}>
                        {p.name}
                      </span>
                      <span className="shrink-0 text-on-surface-variant">
                        {p.kept} kept{p.removed ? ` · ${p.removed} removed` : ""}
                      </span>
                      <span className="shrink-0 tabular-nums font-semibold text-secondary w-20 text-right">
                        {fmtNum(p.after)}
                      </span>
                    </div>
                  ))}
                  {preview.length > 80 && (
                    <div className="px-3 py-1.5 text-caption text-on-surface-variant">
                      +{preview.length - 80} more…
                    </div>
                  )}
                </div>
              )}

              <div className="text-caption text-on-surface-variant px-0.5">
                {mappedCount}/{items.length} lines · {headerMappedCount}/{headerFields.length} fields
                linked · {impRows.length} client{impRows.length === 1 ? "" : "s"}
              </div>
            </>
          )}
          <button
            onClick={generate}
            disabled={
              busy ||
              modelStatus !== "ready" ||
              !dataLoaded ||
              (mappedCount === 0 && headerMappedCount === 0)
            }
            className="w-full bg-secondary-container hover:bg-[#003ea8] text-white py-2.5 rounded-lg font-label-md text-sm shadow-[0_0_20px_rgba(0,83,219,0.3)] transition-all flex justify-center items-center gap-2 border border-outline-variant/50 disabled:opacity-40"
          >
            <span className="material-symbols-outlined text-[18px]">bolt</span>
            {busy
              ? "Generating…"
              : `Generate ${dataLoaded ? impRows.length : ""} annex${
                  impRows.length === 1 ? "" : "es"
                }`}
          </button>
        </div>
      </div>
    </div>
  );
}

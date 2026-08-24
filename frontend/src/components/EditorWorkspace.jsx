import { useEffect, useMemo, useRef, useState } from "react";
import PdfCanvas from "./PdfCanvas.jsx";
import CanvasToolbar from "./CanvasToolbar.jsx";
import FontPanel from "./FontPanel.jsx";
import SignaturePanel from "./SignaturePanel.jsx";
import SplitField from "./SplitField.jsx";

export default function EditorWorkspace({ ed, onDownload, guest = false }) {
  const inputRef = useRef(null);
  const canvasBoxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const [panel, setPanel] = useState("fields"); // "fields" | "sign"
  const [splits, setSplits] = useState({}); // { [spanId]: -1|index } — value-split overrides
  const [splitEditingId, setSplitEditingId] = useState(null);
  const {
    file, fileData, spans, pages, pageIndex, setPageIndex, selectedId, setSelectedId,
    edits, nEdits, editedIds, previewData, busy, error, zoom, setZoom,
    overlays, addOverlay, updateOverlay, removeOverlay, fonts, hasChanges,
    moves, moveSpan, clearMove,
    loadFile, setFieldValue, resetAll, preview, download,
  } = ed;
  const doDownload = onDownload || download;

  const [overlaySel, setOverlaySel] = useState(null);
  const [placement, setPlacement] = useState(null); // null | {kind:'text'} | {kind:'sign', data, ratio}
  const [hideFontNote, setHideFontNote] = useState(false);

  const problemFonts = (ed.fontReport?.fonts || []).filter(
    (f) => f.status === "substitute" || f.status === "fallback"
  );
  useEffect(() => {
    setHideFontNote(false);
  }, [ed.fontReport]);

  // ---- Find & replace across every span (current values included) ----
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [replaceWith, setReplaceWith] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [matchIdx, setMatchIdx] = useState(0);

  const matches = useMemo(() => {
    const q = findQuery.trim();
    if (!q) return [];
    const needle = caseSensitive ? q : q.toLowerCase();
    return spans.filter((s) => {
      const cur = ed.edits[s.id] ?? s.text;
      return (caseSensitive ? cur : cur.toLowerCase()).includes(needle);
    });
  }, [findQuery, caseSensitive, spans, ed.edits]);

  useEffect(() => {
    if (matchIdx >= matches.length) setMatchIdx(0);
  }, [matches.length, matchIdx]);

  function normIdx(i) {
    if (!matches.length) return 0;
    return ((i % matches.length) + matches.length) % matches.length;
  }

  function goToMatch(idx) {
    if (!matches.length) return;
    const at = normIdx(idx);
    setMatchIdx(at);
    setPageIndex(matches[at].page);
    setSelectedId(matches[at].id);
  }

  // Replace every occurrence inside one field value. Manual scan so the same
  // code path handles both case modes without regex-escaping the query.
  function replaceInValue(text) {
    const q = findQuery.trim();
    let out = "";
    let i = 0;
    const hay = caseSensitive ? text : text.toLowerCase();
    const needle = caseSensitive ? q : q.toLowerCase();
    for (;;) {
      const hit = hay.indexOf(needle, i);
      if (hit === -1) {
        out += text.slice(i);
        return out;
      }
      out += text.slice(i, hit) + replaceWith;
      i = hit + q.length;
    }
  }

  function replaceCurrent() {
    const m = matches[matchIdx];
    if (!m || !findQuery.trim()) return;
    const cur = ed.edits[m.id] ?? m.text;
    setFieldValue(m.id, replaceInValue(cur));
  }

  function replaceAllMatches() {
    if (!matches.length || !findQuery.trim()) return;
    for (const m of matches) {
      const cur = ed.edits[m.id] ?? m.text;
      setFieldValue(m.id, replaceInValue(cur));
    }
  }

  // Drop a new text box or signature where the user clicks the page.
  function handlePlace(x, y) {
    if (!placement) return;
    if (placement.kind === "text") {
      setOverlaySel(
        addOverlay({ kind: "text", page: pageIndex, x, y, text: "", size: 16, color: "#111827", font: fonts[0] || "" })
      );
    } else if (placement.kind === "sign") {
      const w = 150;
      setOverlaySel(
        addOverlay({ kind: "sign", page: pageIndex, x, y, w, h: w / (placement.ratio || 3), data: placement.data })
      );
    }
    setPlacement(null);
  }

  // Selecting away from an empty text box discards it (no stray "Text" ghosts).
  function selectOverlay(id) {
    if (overlaySel && overlaySel !== id) {
      const prev = overlays.find((o) => o.id === overlaySel);
      if (prev && prev.kind === "text" && !String(prev.text).trim()) removeOverlay(overlaySel);
    }
    setOverlaySel(id);
  }

  const pageCount = (pages && pages.length) || 1;
  const pageSpans = spans.filter((s) => s.page === pageIndex);

  // Measure the document pane so the PDF fits its width (100% = fit-to-pane).
  useEffect(() => {
    const el = canvasBoxRef.current;
    if (!el) return;
    const update = () => setBoxW(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [file]);

  // When a field is picked on the PDF, scroll its input into view.
  useEffect(() => {
    if (selectedId == null) return;
    const el = document.getElementById(`field-${selectedId}`);
    if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  const fieldLabel = (s) => {
    const t = (s.text || "").trim();
    return t ? (t.length > 48 ? t.slice(0, 48) + "…" : t) : `Field #${s.id}`;
  };

  const pdfWidth = Math.max(260, Math.round(((boxW || 640) - 48) * zoom));

  return (
    <div className="flex-1 flex gap-4 p-4 overflow-hidden max-w-[1400px] w-full mx-auto animate-rise">
      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])}
      />

      {/* Left Pane: Document Preview (65%) */}
      <div className="flex-[0.65] bg-surface-container-lowest rounded-xl border border-outline-variant/30 flex flex-col overflow-hidden relative shadow-none">
        {/* Toolbar overlay */}
        <CanvasToolbar
          pageIndex={pageIndex}
          pageCount={pageCount}
          setPageIndex={setPageIndex}
          zoom={zoom}
          setZoom={setZoom}
        />

        {/* Font report — surfaced after a preview/download so substitutions are visible */}
        {problemFonts.length > 0 && !hideFontNote && (
          <div className="absolute top-14 left-1/2 z-20 flex max-w-[92%] -translate-x-1/2 items-start gap-2 rounded-lg border border-amber-500/30 bg-surface-container-high/95 px-3 py-2 text-caption text-amber-400 shadow-xl backdrop-blur-md">
            <span className="material-symbols-outlined shrink-0 text-[16px]">warning</span>
            <span>
              Fonts replaced with lookalikes:{" "}
              <b className="font-semibold">
                {[...new Set(problemFonts.map((f) => f.font))].join(", ")}
              </b>
              . Upload the real files in the Fonts panel for an exact match.
            </span>
            <button
              onClick={() => setHideFontNote(true)}
              aria-label="Dismiss"
              className="shrink-0 hover:text-amber-200"
            >
              <span className="material-symbols-outlined text-[16px]">close</span>
            </button>
          </div>
        )}

        {/* Document Canvas */}
        <div ref={canvasBoxRef} className="flex-1 overflow-auto p-6 flex justify-center bg-on-surface/[0.04]">
          {file && fileData ? (
            <div className="paper-shadow rounded-sm mt-8 mb-6 h-fit">
              <PdfCanvas
                data={previewData || fileData}
                pageIndex={pageIndex}
                spans={spans}
                selectedId={selectedId}
                editedIds={editedIds}
                onSelect={(id) => id != null && setSelectedId(id)}
                maxWidth={pdfWidth}
                overlays={overlays}
                overlaySelectedId={overlaySel}
                onOverlaySelect={selectOverlay}
                onOverlayChange={updateOverlay}
                onOverlayDelete={(id) => {
                  removeOverlay(id);
                  setOverlaySel(null);
                }}
                fonts={fonts}
                placement={placement}
                onPlace={handlePlace}
                moves={previewData ? {} : moves}
                edits={edits}
                onSpanMove={moveSpan}
                onSpanMoveClear={clearMove}
              />
            </div>
          ) : (
            <button
              onClick={() => inputRef.current?.click()}
              className="bg-white w-full max-w-[640px] min-h-[400px] paper-shadow rounded-sm text-[#1e293b] flex flex-col items-center justify-center gap-3 mt-8 mb-6 hover:opacity-90 transition-opacity"
            >
              <span className="material-symbols-outlined text-[40px] text-[#94a3b8]">
                {busy ? "hourglass_top" : "upload_file"}
              </span>
              <p className="text-base font-semibold">
                {busy ? "Reading PDF…" : "Upload a PDF to start editing"}
              </p>
              <p className="text-sm text-[#64748b]">Drag &amp; drop or click to browse</p>
            </button>
          )}
        </div>

        {error && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 bg-error-container text-on-error-container border border-error/30 rounded-lg px-4 py-2 text-caption shadow-xl z-20">
            {error}
          </div>
        )}
      </div>

      {/* Right Pane: Text Fields Sidebar (35%) */}
      <div className="flex-[0.35] bg-surface-container rounded-xl border border-outline-variant/30 flex flex-col shadow-panel overflow-hidden relative">
        {/* Header: Text / Sign toggle + Find & Replace */}
        <div className="p-3 border-b border-outline-variant/30 bg-surface/50 backdrop-blur-md sticky top-0 z-10">
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-surface-container-high p-1 rounded-full flex items-center gap-1 border border-outline-variant/20">
              <button
                onClick={() => setPanel("fields")}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-1.5 rounded-full font-label-md text-sm transition-all ${
                  panel === "fields"
                    ? "bg-secondary-container text-white shadow-lg"
                    : "text-on-surface-variant hover:text-on-surface"
                }`}
              >
                <span className="material-symbols-outlined text-[18px]">text_fields</span>
                Text
              </button>
              <button
                onClick={() => setPanel("sign")}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-1.5 rounded-full font-label-md text-sm transition-all ${
                  panel === "sign"
                    ? "bg-secondary-container text-white shadow-lg"
                    : "text-on-surface-variant hover:text-on-surface"
                }`}
              >
                <span className="material-symbols-outlined text-[18px]">draw</span>
                Sign
              </button>
            </div>
            <button
              onClick={() => setFindOpen((v) => !v)}
              title="Find & replace"
              aria-label="Find and replace"
              className={`shrink-0 w-9 h-9 rounded-full inline-flex items-center justify-center transition-colors ${
                findOpen
                  ? "bg-secondary-container text-white"
                  : "border border-outline-variant/40 text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"
              }`}
            >
              <span className="material-symbols-outlined text-[18px]">search</span>
            </button>
          </div>

          {findOpen && (
            <div className="mt-2 space-y-2 animate-drop">
              <div className="flex items-center gap-1.5">
                <input
                  value={findQuery}
                  onChange={(e) => setFindQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setFindOpen(false);
                    else if (e.key === "Enter") {
                      e.preventDefault();
                      goToMatch(e.shiftKey ? matchIdx - 1 : matchIdx + 1);
                    }
                  }}
                  autoFocus
                  placeholder="Find in document…"
                  className="flex-1 min-w-0 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1.5 px-2.5 text-sm text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container"
                />
                <button
                  onClick={() => setCaseSensitive((v) => !v)}
                  title={caseSensitive ? "Case sensitive" : "Case insensitive"}
                  aria-label="Toggle case sensitivity"
                  className={`shrink-0 h-8 w-8 rounded-lg border font-caption text-[12px] font-semibold transition-colors ${
                    caseSensitive
                      ? "border-secondary-container bg-secondary-container/10 text-secondary"
                      : "border-outline-variant/50 text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  Aa
                </button>
              </div>

              {findQuery.trim() !== "" && (
                <>
                  <div className="flex items-center justify-between text-caption text-on-surface-variant">
                    <span>
                      {matches.length
                        ? `Match ${matchIdx + 1} of ${matches.length} field${
                            matches.length === 1 ? "" : "s"
                          }`
                        : "No matching fields"}
                    </span>
                    <span className="flex items-center gap-0.5">
                      <button
                        disabled={!matches.length}
                        onClick={() => goToMatch(matchIdx - 1)}
                        title="Previous match (Shift+Enter)"
                        aria-label="Previous match"
                        className="disabled:opacity-30 hover:text-on-surface transition-colors"
                      >
                        <span className="material-symbols-outlined text-[16px]">
                          keyboard_arrow_up
                        </span>
                      </button>
                      <button
                        disabled={!matches.length}
                        onClick={() => goToMatch(matchIdx + 1)}
                        title="Next match (Enter)"
                        aria-label="Next match"
                        className="disabled:opacity-30 hover:text-on-surface transition-colors"
                      >
                        <span className="material-symbols-outlined text-[16px]">
                          keyboard_arrow_down
                        </span>
                      </button>
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      value={replaceWith}
                      onChange={(e) => setReplaceWith(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setFindOpen(false);
                      }}
                      placeholder="Replace with…"
                      className="flex-1 min-w-0 bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-1.5 px-2.5 text-sm text-on-surface focus:outline-none focus:ring-1 focus:ring-secondary-container"
                    />
                    <button
                      onClick={replaceCurrent}
                      disabled={!matches.length}
                      title="Replace in the current field"
                      className="shrink-0 rounded-lg border border-outline-variant/50 px-2.5 py-1.5 font-label-md text-[12px] text-on-surface transition-colors hover:border-accent-cyan/50 hover:text-accent-cyan disabled:opacity-40"
                    >
                      Replace
                    </button>
                    <button
                      onClick={replaceAllMatches}
                      disabled={!matches.length}
                      title="Replace in every matching field"
                      className="shrink-0 rounded-lg bg-secondary-container px-2.5 py-1.5 font-label-md text-[12px] text-white transition-colors hover:bg-secondary-container-hover disabled:opacity-40"
                    >
                      All
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Body: text fields OR the signature workspace */}
        {panel === "sign" ? (
          <div className="flex-1 overflow-y-auto">
            <SignaturePanel cloud={!guest} onPlace={(data, ratio) => setPlacement({ kind: "sign", data, ratio })} />
          </div>
        ) : (
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          <button
            onClick={() => setPlacement(placement?.kind === "text" ? null : { kind: "text" })}
            disabled={!file}
            className={`w-full flex items-center justify-center gap-2 py-2 rounded-lg border text-label-md text-sm transition-colors disabled:opacity-40 ${
              placement?.kind === "text"
                ? "border-accent-cyan/40 bg-accent-cyan/10 text-accent-cyan"
                : "border-outline-variant/50 text-on-surface hover:bg-surface-container-high"
            }`}
          >
            <span className="material-symbols-outlined text-[18px]">
              {placement?.kind === "text" ? "ads_click" : "add"}
            </span>
            {placement?.kind === "text" ? "Click on the document…" : "Add text"}
          </button>
          {file && !guest && <FontPanel file={file} onChanged={() => nEdits > 0 && preview()} />}
          {pageSpans.length === 0 && (
            <p className="text-caption text-on-surface-variant">
              Upload a PDF to see its editable text fields here.
            </p>
          )}
          {pageSpans.map((s) => (
            <SplitField
              key={s.id}
              span={s}
              label={fieldLabel(s)}
              fullValue={edits[s.id] ?? s.text}
              selected={selectedId === s.id}
              onFocus={() => setSelectedId(s.id)}
              onChange={(val) => setFieldValue(s.id, val)}
              override={splits[s.id]}
              editing={splitEditingId === s.id}
              onEnterSplit={() => setSplitEditingId(s.id)}
              onSetSplit={(i) => {
                setSplits((m) => ({ ...m, [s.id]: i }));
                setSplitEditingId(null);
              }}
              onWholeField={() => {
                setSplits((m) => ({ ...m, [s.id]: -1 }));
                setSplitEditingId(null);
              }}
              onCloseSplit={() => setSplitEditingId(null)}
            />
          ))}

          {/* Status Chip */}
          <div className="mt-6 bg-secondary-container/10 border border-secondary-container/20 rounded-lg p-3 flex items-center gap-3">
            <span className="material-symbols-outlined text-secondary-container text-[18px]">edit</span>
            <span className="text-label-md text-[13px] text-secondary-container">
              {nEdits} field(s) modified
            </span>
          </div>
        </div>
        )}

        {/* Footer Controls */}
        <div className="p-5 border-t border-outline-variant/30 bg-surface/80 backdrop-blur-xl flex flex-col gap-3 sticky bottom-0">
          <div className="flex gap-3">
            <button
              onClick={preview}
              disabled={nEdits === 0 || busy}
              className="flex-1 bg-secondary-container hover:bg-secondary-container-hover text-on-secondary-container py-2.5 rounded-lg font-label-md text-sm shadow-[0_0_20px_rgba(0,83,219,0.3)] transition-all flex justify-center items-center gap-2 border border-outline-variant/50 disabled:opacity-40"
            >
              <span className="material-symbols-outlined text-[18px]">visibility</span>
              {busy ? "Working…" : "Preview"}
            </button>
            <button
              onClick={resetAll}
              className="flex-1 bg-transparent border border-outline-variant hover:bg-surface-container-high text-on-surface py-2.5 rounded-lg font-label-md text-sm transition-all flex justify-center items-center gap-2"
            >
              <span className="material-symbols-outlined text-[18px]">refresh</span>
              Reset All
            </button>
          </div>
          <button
            onClick={doDownload}
            disabled={!hasChanges || busy}
            className="w-full bg-transparent border border-outline-variant hover:bg-surface-container-high text-on-surface py-2.5 rounded-lg font-label-md text-sm transition-all flex justify-center items-center gap-2 disabled:opacity-40"
          >
            <span className="material-symbols-outlined text-[18px]">file_download</span>
            Download Edited PDF
          </button>
        </div>
      </div>
    </div>
  );
}

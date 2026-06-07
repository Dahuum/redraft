import { useEffect, useRef, useState } from "react";
import PdfCanvas from "./PdfCanvas.jsx";
import FontPanel from "./FontPanel.jsx";

export default function EditorWorkspace({ ed, onDownload }) {
  const inputRef = useRef(null);
  const canvasBoxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const {
    file, fileData, spans, pages, pageIndex, selectedId, setSelectedId,
    edits, nEdits, editedIds, previewData, busy, error, zoom, setZoom,
    loadFile, setFieldValue, resetAll, preview, download,
  } = ed;
  const doDownload = onDownload || download;

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
      <div className="flex-[0.65] bg-surface-container-lowest rounded-xl border border-outline-variant/30 flex flex-col overflow-hidden relative shadow-[inset_0_0_80px_rgba(0,0,0,0.2)]">
        {/* Toolbar overlay */}
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-surface/90 backdrop-blur-md border border-white/10 rounded-full px-3 py-1.5 flex items-center gap-3 z-10 shadow-xl">
          <button
            className="text-on-surface-variant hover:text-primary transition-colors"
            onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}
          >
            <span className="material-symbols-outlined text-[18px]">zoom_out</span>
          </button>
          <span className="text-caption font-medium">{Math.round(zoom * 100)}%</span>
          <button
            className="text-on-surface-variant hover:text-primary transition-colors"
            onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(2)))}
          >
            <span className="material-symbols-outlined text-[18px]">zoom_in</span>
          </button>
          <div className="w-px h-4 bg-outline-variant"></div>
          <button
            className="text-on-surface-variant hover:text-primary transition-colors"
            onClick={() => setZoom(1)}
            title="Fit width"
          >
            <span className="material-symbols-outlined text-[18px]">fit_screen</span>
          </button>
        </div>

        {/* Document Canvas */}
        <div ref={canvasBoxRef} className="flex-1 overflow-auto p-6 flex justify-center bg-black/20">
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
      <div className="flex-[0.35] bg-surface-container rounded-xl border border-white/5 flex flex-col shadow-[0_10px_40px_rgba(0,0,0,0.3)] overflow-hidden relative">
        {/* Header */}
        <div className="p-5 border-b border-outline-variant/30 bg-surface/50 backdrop-blur-md sticky top-0 z-10">
          <h2 className="font-display-md text-xl font-bold mb-0.5 tracking-tight">Text Fields</h2>
          <p className="text-caption text-on-surface-variant flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-secondary-container inline-block"></span>
            {pageSpans.length} field(s) on page {pageIndex + 1}
          </p>
        </div>

        {/* Scrollable Fields List */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {file && <FontPanel file={file} onChanged={() => nEdits > 0 && preview()} />}
          {pageSpans.length === 0 && (
            <p className="text-caption text-on-surface-variant">
              Upload a PDF to see its editable text fields here.
            </p>
          )}
          {pageSpans.map((s) => (
            <div className="space-y-1.5 group" key={s.id}>
              <div className="flex justify-between items-center text-label-md text-[13px] text-on-surface-variant group-focus-within:text-secondary-container transition-colors">
                <label htmlFor={`field-${s.id}`} className="truncate">{fieldLabel(s)}</label>
                <span className="material-symbols-outlined text-[16px] opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer">
                  help
                </span>
              </div>
              <input
                className={`w-full bg-surface-container-lowest border rounded-lg py-2 px-3 text-sm text-on-surface focus:outline-none focus:border-secondary-container focus:ring-1 focus:ring-secondary-container transition-all hover:bg-surface-container-high shadow-sm ${
                  selectedId === s.id
                    ? "border-secondary-container ring-1 ring-secondary-container"
                    : "border-outline-variant/50"
                }`}
                id={`field-${s.id}`}
                type="text"
                value={edits[s.id] ?? s.text}
                onFocus={() => setSelectedId(s.id)}
                onChange={(e) => setFieldValue(s.id, e.target.value)}
              />
            </div>
          ))}

          {/* Status Chip */}
          <div className="mt-6 bg-secondary-container/10 border border-secondary-container/20 rounded-lg p-3 flex items-center gap-3">
            <span className="material-symbols-outlined text-secondary-container text-[18px]">edit</span>
            <span className="text-label-md text-[13px] text-secondary-container">
              {nEdits} field(s) modified
            </span>
          </div>
        </div>

        {/* Footer Controls */}
        <div className="p-5 border-t border-outline-variant/30 bg-surface/80 backdrop-blur-xl flex flex-col gap-3 sticky bottom-0">
          <div className="flex gap-3">
            <button
              onClick={preview}
              disabled={nEdits === 0 || busy}
              className="flex-1 bg-secondary-container hover:bg-[#003ea8] text-on-secondary-container py-2.5 rounded-lg font-label-md text-sm shadow-[0_0_20px_rgba(0,83,219,0.3)] transition-all flex justify-center items-center gap-2 border border-white/10 disabled:opacity-40"
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
            disabled={nEdits === 0 || busy}
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

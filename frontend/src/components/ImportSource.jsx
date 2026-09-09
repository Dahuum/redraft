import { useRef } from "react";

/**
 * Shared CSV/paste source picker for the bulk and annex import panels:
 * Upload/Paste tabs, a drop zone, and the paste box + Load button. Callers
 * render whatever sits around it (mapping rows, checkboxes, apply buttons).
 */
export default function ImportSource({
  tab,
  setTab,
  onFile,
  onLoadPaste,
  text,
  setText,
  top = null,
  dropLabel = "Drop a CSV / TSV or click to browse",
}) {
  const inputRef = useRef(null);
  return (
    <>
      <div className="flex items-center gap-1 bg-surface-container-low rounded-lg p-1 border border-outline-variant/20 w-max">
        {[
          ["upload", "upload_file", "Upload"],
          ["paste", "content_paste", "Paste"],
        ].map(([k, icon, lbl]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-label-md text-sm transition-all ${
              tab === k
                ? "bg-surface-variant text-on-surface shadow-sm"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            <span className="material-symbols-outlined text-[16px]">{icon}</span>
            {lbl}
          </button>
        ))}
      </div>

      {top}

      {tab === "upload" ? (
        <div
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            onFile(e.dataTransfer.files?.[0]);
          }}
          className="border-2 border-dashed border-outline-variant/50 hover:border-secondary-container rounded-xl p-6 flex flex-col items-center gap-2 cursor-pointer transition-colors text-center"
        >
          <span className="material-symbols-outlined text-[24px] text-on-surface-variant">
            cloud_upload
          </span>
          <p className="text-body-md text-on-surface">{dropLabel}</p>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.tsv,.txt"
            className="hidden"
            onChange={(e) => onFile(e.target.files?.[0])}
          />
        </div>
      ) : (
        <div className="space-y-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"Paste rows from Excel / Sheets or CSV…"}
            className="w-full h-28 bg-surface-container-lowest border border-outline-variant/50 rounded-lg p-3 text-sm text-on-surface font-mono focus:outline-none focus:ring-1 focus:ring-secondary-container resize-y"
          />
          <button
            onClick={onLoadPaste}
            className="px-3 py-1.5 bg-surface-container-highest border border-outline-variant rounded-lg text-label-md text-on-surface hover:border-secondary-container transition-colors"
          >
            Load
          </button>
        </div>
      )}
    </>
  );
}

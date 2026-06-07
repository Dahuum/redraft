import { useEffect, useState } from "react";
import { useEditor } from "./useEditor.js";
import { addDoc, getBytes, patchDoc, getAllRecords } from "./lib/history.js";
import { renderThumb } from "./lib/thumb.js";
import HomeScreen from "./components/HomeScreen.jsx";
import EditorWorkspace from "./components/EditorWorkspace.jsx";
import BulkWorkspace from "./components/BulkWorkspace.jsx";

export default function App() {
  const [view, setView] = useState("home"); // "home" | "editor"
  const [mode, setMode] = useState("editor"); // editor view: "editor" | "bulk"
  const [docId, setDocId] = useState(null); // current history doc id
  const ed = useEditor();

  // One-time backfill: give any pre-existing history docs a thumbnail.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const recs = await getAllRecords();
      for (const r of recs) {
        if (cancelled) break;
        if (!r.thumb && r.bytes) {
          const thumb = await renderThumb(r.bytes);
          if (thumb && !cancelled) await patchDoc(r.id, { thumb });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the current doc's history thumbnail (and bytes) in sync with the
  // latest edited preview, so the history card shows the latest changes.
  useEffect(() => {
    if (!docId || !ed.previewData) return;
    let cancelled = false;
    renderThumb(ed.previewData).then((thumb) => {
      if (!cancelled) patchDoc(docId, { bytes: ed.previewData, ...(thumb && { thumb }) });
    });
    return () => {
      cancelled = true;
    };
  }, [ed.previewData, docId]);

  async function handleUpload(file, { bulk = false } = {}) {
    const res = await ed.loadFile(file);
    if (!res) return;
    const buf = await file.arrayBuffer();
    let id = null;
    try {
      id = await addDoc({
        name: file.name,
        bytes: buf,
        pages: res.pages.length,
        fields: res.spans.length,
      });
      setDocId(id);
    } catch {
      /* history is best-effort */
    }
    setMode(bulk ? "bulk" : "editor");
    setView("editor");
    // Render the thumbnail in the background so the upload stays snappy.
    if (id) renderThumb(buf).then((thumb) => thumb && patchDoc(id, { thumb }));
  }

  async function openFromHistory(meta) {
    const bytes = await getBytes(meta.id);
    if (!bytes) return;
    const file = new File([bytes], meta.name, { type: "application/pdf" });
    const res = await ed.loadFile(file);
    if (!res) return;
    patchDoc(meta.id, { addedAt: Date.now() });
    setDocId(meta.id);
    setView("editor");
  }

  async function handleDownload() {
    await ed.download();
    if (docId) patchDoc(docId, { status: "Final" });
  }

  if (view === "home") {
    return (
      <HomeScreen
        onUpload={handleUpload}
        onOpen={openFromHistory}
        busy={ed.busy}
        error={ed.error}
      />
    );
  }

  // Editor view (the approved dark "Text Fields" design)
  return (
    <div className="h-screen w-full flex flex-col overflow-hidden bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-[#1a233a] via-background to-background">
      {/* TopNavBar */}
      <header className="bg-surface/80 backdrop-blur-xl text-primary font-label-md text-label-md h-14 w-full border-b border-outline-variant flex justify-between items-center sticky top-0 z-30 px-6">
        <button
          onClick={() => setView("home")}
          className="flex items-center gap-2 text-on-surface hover:opacity-80 transition-opacity"
        >
          <span className="material-symbols-outlined text-[20px]">arrow_back</span>
          <span className="font-display-md text-[17px] font-bold tracking-tight">Redraft</span>
        </button>
        <div className="flex items-center gap-3">
          <button className="px-3 py-1.5 rounded-md font-label-md text-[13px] text-on-surface border border-outline-variant hover:bg-surface-container-high transition-colors opacity-80 active:opacity-100">
            Share
          </button>
          <button
            onClick={handleDownload}
            disabled={ed.nEdits === 0 || ed.busy}
            className="px-3 py-1.5 rounded-md font-label-md text-[13px] bg-primary text-on-primary hover:bg-primary/90 transition-colors shadow-[0_0_15px_rgba(195,198,210,0.1)] opacity-80 active:opacity-100 flex items-center gap-2 disabled:opacity-40"
          >
            <span className="material-symbols-outlined text-[18px]">download</span>
            Export PDF
          </button>
          <div className="w-7 h-7 rounded-full bg-surface-container-highest overflow-hidden border border-outline-variant ml-1">
            <img
              alt="User Avatar"
              className="w-full h-full object-cover"
              src="https://lh3.googleusercontent.com/aida-public/AB6AXuBKVjGU7TawQrZNbtZK_FZFyKW-qgcmLhj-wGGes2yje-4Liy13EVs9sYq-o4Pt-6zqFIOTtyTNOcSXeeQKu-9ftXai-hTAhTkbUBeOMFn57lrU8hd71wlosWAQ5YvZcmWLmKkXWYx2V_hjJ7kudTglvCj-dcJPIijuHvKRhYpWEGcU7Q64UrLCGC1bAPpX1dOMPZmessdR2y5QYV1Fc0FelSvPbYZGo9RsKe1sVJx78dJnaZOc3Ide2ZZdQKwei9PvNxmNErQvMInN"
            />
          </div>
        </div>
      </header>

      {/* Mode toggle */}
      <div className="w-full flex justify-center py-3 bg-background border-b border-outline-variant/30">
        <div className="bg-surface-container-high p-1 rounded-full flex items-center gap-1 border border-outline-variant/20">
          <button
            onClick={() => setMode("editor")}
            className={`flex items-center gap-2 px-5 py-1.5 rounded-full font-label-md text-sm transition-all ${
              mode === "editor"
                ? "bg-secondary-container text-white shadow-lg"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            <span className="material-symbols-outlined text-[16px]">edit</span>
            PDF Editor
          </button>
          <button
            onClick={() => setMode("bulk")}
            className={`flex items-center gap-2 px-5 py-1.5 rounded-full font-label-md text-sm transition-all ${
              mode === "bulk"
                ? "bg-secondary-container text-white shadow-lg"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            <span className="material-symbols-outlined text-[16px]">layers</span>
            Bulk Generator
          </button>
        </div>
      </div>

      {mode === "editor" ? (
        <EditorWorkspace ed={ed} onDownload={handleDownload} />
      ) : (
        <BulkWorkspace
          file={ed.file}
          spans={ed.spans}
          data={ed.fileData}
          pages={ed.pages}
        />
      )}
    </div>
  );
}

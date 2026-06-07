import { useRef, useState } from "react";
import { useHistory, removeDoc, ago } from "../lib/history.js";

const STATUS = {
  Draft: "bg-[#00f5ff]/10 text-[#00f5ff]",
  Final: "bg-emerald-500/10 text-emerald-400",
  Review: "bg-amber-500/10 text-amber-400",
};

/**
 * Home / landing screen — "Midnight Executive Workspace" design.
 * Drop Zone + Browse Files perform the real upload (`onUpload`); Recent Activity
 * is the real, persistent history (`onOpen` reopens a doc).
 */
export default function HomeScreen({ onUpload, onOpen, busy, error }) {
  const inputRef = useRef(null);
  const [drag, setDrag] = useState(false);
  const [tab, setTab] = useState("editor"); // "editor" | "history"
  const docs = useHistory();

  const pick = (f) => f && onUpload(f);

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => pick(e.target.files?.[0])}
      />

      {/* Top Toolbar */}
      <nav className="fixed top-0 left-0 w-full z-50 flex justify-between items-center px-lg h-14 bg-[#0f131c]/90 backdrop-blur-md border-b border-white/5 shadow-sm transition-all duration-300">
        <div className="flex items-center gap-md">
          <div className="flex items-center gap-xs">
            <div className="font-display-md text-[18px] font-bold text-on-surface tracking-tight">
              Redraft
            </div>
          </div>
          <div className="h-4 w-[1px] bg-white/10 mx-xs hidden md:block"></div>
          <span className="hidden md:flex items-center text-on-surface-variant font-label-md text-sm">
            Personal Workspace
          </span>
        </div>

        {/* Center Mode Toggle */}
        <div className="absolute left-1/2 -translate-x-1/2 hidden md:flex items-center bg-[#1c1b1c] rounded-lg p-1 border border-white/5">
          <button
            onClick={() => setTab("editor")}
            className={`px-md py-1.5 rounded-md font-label-md text-sm transition-all ${
              tab === "editor"
                ? "bg-surface-variant text-on-surface shadow-sm"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            Editor
          </button>
          <button
            onClick={() => setTab("history")}
            className={`px-md py-1.5 rounded-md font-label-md text-sm transition-all ${
              tab === "history"
                ? "bg-surface-variant text-on-surface shadow-sm"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            History
          </button>
        </div>

        {/* Trailing Actions */}
        <div className="flex items-center gap-sm">
          <button className="font-label-md text-[13px] px-md py-1.5 rounded-md border border-white/10 text-on-surface hover:bg-surface-container-high transition-colors active:scale-95">
            Share
          </button>
          <button className="font-label-md text-[13px] px-md py-1.5 rounded-md bg-[#00f5ff]/10 text-[#00f5ff] border border-[#00f5ff]/20 hover:bg-[#00f5ff]/20 transition-colors active:scale-95">
            Export
          </button>
          <div className="w-8 h-8 rounded-full bg-surface-container-high border border-white/10 overflow-hidden cursor-pointer hover:border-[#00f5ff]/50 transition-colors ml-xs">
            <img
              alt="User avatar"
              className="w-full h-full object-cover"
              src="https://lh3.googleusercontent.com/aida-public/AB6AXuB9e-ZXz4fzaPxmxwTxGC9xj1jqiInEDBT2XXjBgtn-vxeUTE16SE0kP3OjWlRkgFfldtdBAQIUQCD5dNw9WEj5QBET7PAyCxMvBx_MUR9T41yFpF2TlDAzn4Gsg3QkdkBTEF2ZAW9-UD53iYpnqII1e7J01kKRLHKzUV6ZNoT36qZOe5TfhgEXyrisP0wfj_qaPOrTmwjEfsQryO0AqyRI_cU99QHfdgPgSY4zxt6n3vaBGHOPk1-1imzfYgKQJwQ_LW0gub_-NdWd"
            />
          </div>
        </div>
      </nav>

      {/* Main Workspace Canvas */}
      <main className="pt-20 min-h-screen flex flex-col px-lg pb-lg md:px-xl md:pb-xl max-w-[1100px] mx-auto w-full relative z-10">
        {/* Ambient Glow */}
        <div className="absolute top-[20%] left-[50%] -translate-x-1/2 w-[600px] h-[600px] bg-[#00f5ff]/5 rounded-full blur-[120px] pointer-events-none -z-10"></div>

        <div className="flex-1 flex flex-col w-full mx-auto">
          {/* Drop Zone (Primary Action Area) */}
          {tab === "editor" && (
            <div
              onClick={() => inputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                pick(e.dataTransfer.files?.[0]);
              }}
              className={`relative w-full rounded-2xl bg-[#1c1b1c]/40 backdrop-blur-xl border transition-all duration-300 group cursor-pointer overflow-hidden flex flex-col items-center justify-center py-[60px] px-lg mb-lg shadow-[0_8px_32px_rgba(0,0,0,0.4)] mt-2 ${
                drag
                  ? "border-[#00f5ff]/30 bg-[#1c1b1c]/60"
                  : "border-white/5 hover:border-[#00f5ff]/30 hover:bg-[#1c1b1c]/60"
              }`}
            >
              <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-white/5 group-hover:ring-[#00f5ff]/20 transition-all pointer-events-none"></div>
              <div className="w-14 h-14 rounded-2xl bg-surface-container flex items-center justify-center mb-md shadow-[0_0_20px_rgba(0,0,0,0.5)] border border-white/5 group-hover:-translate-y-1 transition-transform duration-300">
                <span className="material-symbols-outlined text-[28px] text-on-surface-variant group-hover:text-[#00f5ff] transition-colors">
                  {busy ? "hourglass_top" : "upload_file"}
                </span>
              </div>
              <h2 className="font-display-md text-[20px] text-on-surface mb-xs font-semibold tracking-tight">
                {busy ? "Reading PDF…" : "Drop PDF here"}
              </h2>
              <p className="font-body-md text-sm text-on-surface-variant mb-md text-center max-w-sm">
                Securely upload your document for AI-assisted redaction and structural formatting.
              </p>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  inputRef.current?.click();
                }}
                disabled={busy}
                className="font-label-md text-sm px-lg py-2 rounded-lg bg-[#00f5ff] text-[#080c14] hover:bg-[#00d0d9] shadow-[0_0_15px_rgba(0,245,255,0.2)] transition-all font-semibold active:scale-95 disabled:opacity-50"
              >
                Browse Files
              </button>
              {error && (
                <p className="mt-md text-sm text-error text-center max-w-sm">{error}</p>
              )}
            </div>
          )}

          {/* Recent Activity Section */}
          <section className="mt-2 flex-1">
            <div className="flex items-center justify-between mb-md border-b border-white/5 pb-sm">
              <h3 className="font-label-md text-sm text-on-surface font-medium flex items-center gap-sm">
                <span className="material-symbols-outlined text-[18px] text-on-surface-variant">
                  history
                </span>
                {tab === "history" ? "History" : "Recent Activity"}
              </h3>
              <span className="text-on-surface-variant font-caption text-[11px]">
                {docs.length} document{docs.length === 1 ? "" : "s"}
              </span>
            </div>

            {docs.length === 0 ? (
              <div className="rounded-xl border border-dashed border-white/10 p-10 text-center">
                <span className="material-symbols-outlined text-[32px] text-on-surface-variant/40">
                  folder_open
                </span>
                <p className="mt-2 text-sm text-on-surface-variant">
                  No documents yet — upload a PDF and it'll appear here.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-md">
                {docs.map((d) => (
                  <div
                    key={d.id}
                    onClick={() => onOpen(d)}
                    className="group relative rounded-xl bg-[#1c1b1c] border border-white/5 p-4 hover:border-white/10 transition-all cursor-pointer hover:-translate-y-0.5 shadow-sm hover:shadow-md"
                  >
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        removeDoc(d.id);
                      }}
                      className="absolute top-2 right-2 z-10 w-6 h-6 rounded-md bg-black/40 text-on-surface-variant opacity-0 group-hover:opacity-100 hover:text-error hover:bg-black/60 transition-all flex items-center justify-center"
                      title="Remove from history"
                    >
                      <span className="material-symbols-outlined text-[16px]">close</span>
                    </button>
                    <div className="aspect-[4/3] rounded-lg bg-surface-container-lowest mb-md border border-white/5 flex items-center justify-center overflow-hidden relative">
                      <div
                        className={`absolute top-2 left-2 z-10 px-2 py-1 font-label-md text-[10px] rounded uppercase tracking-wider font-bold ${
                          STATUS[d.status] || STATUS.Draft
                        }`}
                      >
                        {d.status || "Draft"}
                      </div>
                      {d.thumb ? (
                        <img
                          src={d.thumb}
                          alt={d.name}
                          className="absolute inset-0 w-full h-full object-cover object-top"
                        />
                      ) : (
                        <span className="material-symbols-outlined text-[32px] text-on-surface-variant/30">
                          description
                        </span>
                      )}
                    </div>
                    <h4 className="font-body-md text-sm text-on-surface font-medium truncate mb-xs group-hover:text-[#00f5ff] transition-colors">
                      {d.name}
                    </h4>
                    <div className="flex items-center justify-between text-on-surface-variant font-caption text-[11px]">
                      <span className="">Edited {ago(d.addedAt)}</span>
                      <span className="flex items-center gap-1">
                        <span className="material-symbols-outlined text-[14px]">description</span>
                        {d.pages || 1}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>
    </>
  );
}

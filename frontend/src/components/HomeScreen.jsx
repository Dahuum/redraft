import { useCallback, useEffect, useRef, useState } from "react";
import { useHistory, removeDoc, ago } from "../lib/history.js";
import { cloudEnabled, listProjects, deleteProject, openProjectFile } from "../lib/cloud.js";
import { renderThumb } from "../lib/thumb.js";
import { composeDoc } from "../api.js";
import ThemeToggle from "./ThemeToggle.jsx";

const STATUS = {
  Draft: "bg-accent-cyan/10 text-accent-cyan",
  Final: "bg-emerald-500/10 text-emerald-400",
  Review: "bg-amber-500/10 text-amber-400",
};

/**
 * Home / landing screen — "Midnight Executive Workspace" design.
 * Drop Zone + Browse Files perform the real upload (`onUpload`); Recent Activity
 * is the real, persistent history (`onOpen` reopens a doc).
 */
export default function HomeScreen({ onUpload, onOpen, onOpenCloud, busy, error, onSignOut, guest = false }) {
  const inputRef = useRef(null);
  const menuRef = useRef(null);
  const [drag, setDrag] = useState(false);
  const [tab, setTab] = useState("editor"); // "editor" | "history"
  const [menuOpen, setMenuOpen] = useState(false);
  const docs = useHistory();

  // Cloud-saved templates (your account, max 3 on free).
  const [projects, setProjects] = useState([]);
  const [openingId, setOpeningId] = useState(null);
  const [thumbs, setThumbs] = useState({}); // projectId → data-URL preview
  const loadProjects = useCallback(() => {
    if (cloudEnabled) listProjects().then(setProjects).catch(() => {});
  }, []);
  useEffect(() => loadProjects(), [loadProjects]);

  // Mini first-page preview per saved template. Cached per device (the PDF never
  // changes for a saved template), so it downloads + renders only once.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const p of projects) {
        if (cancelled) break;
        if (thumbs[p.id]) continue;
        const cacheKey = `redraft:ct:${p.id}`;
        let url = null;
        try {
          url = localStorage.getItem(cacheKey);
        } catch {
          /* ignore */
        }
        if (!url) {
          try {
            const file = await openProjectFile(p);
            url = await renderThumb(await file.arrayBuffer(), 220);
            if (url) {
              try {
                localStorage.setItem(cacheKey, url);
              } catch {
                /* cache full — skip */
              }
            }
          } catch {
            /* leave the icon fallback */
          }
        }
        if (url && !cancelled) setThumbs((t) => ({ ...t, [p.id]: url }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projects]); // eslint-disable-line react-hooks/exhaustive-deps

  async function openProject(p) {
    setOpeningId(p.id);
    try {
      await onOpenCloud?.(p);
    } finally {
      setOpeningId(null);
    }
  }
  async function removeProject(p) {
    await deleteProject(p);
    loadProjects();
  }

  // Close the account menu on any outside click.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const pick = (f) => f && onUpload(f);

  // ---- Start from text → clean PDF → opens in the editor ----
  const [showText, setShowText] = useState(false);
  const [txtTitle, setTxtTitle] = useState("");
  const [txtBody, setTxtBody] = useState("");
  const [composing, setComposing] = useState(false);
  const [txtError, setTxtError] = useState(null);

  async function createFromText() {
    if (!txtBody.trim()) return setTxtError("Paste or write the document text first.");
    setComposing(true);
    setTxtError(null);
    window.rdTrack?.("compose_submit", { source: "home" });
    try {
      const blob = await composeDoc(txtBody, txtTitle);
      const name = (txtTitle.trim() || "document").replace(/[^\w \-]/g, "").slice(0, 60) || "document";
      const file = new File([blob], `${name}.pdf`, { type: "application/pdf" });
      setShowText(false);
      onUpload(file); // same path as a normal upload → opens in the editor
    } catch (e) {
      setTxtError(e.message || "Couldn't build the document.");
    } finally {
      setComposing(false);
    }
  }

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
      <nav className="fixed top-0 left-0 w-full z-50 flex justify-between items-center px-lg h-14 bg-page/90 backdrop-blur-md border-b border-outline-variant/30 shadow-sm transition-all duration-300">
        <div className="flex items-center gap-md">
          <div className="flex items-center gap-xs">
            <div className="font-display-md text-[18px] font-bold text-on-surface tracking-tight">
              Redraft
            </div>
          </div>
          <div className="h-4 w-[1px] bg-on-surface/10 mx-xs hidden md:block"></div>
          <span className="hidden md:flex items-center text-on-surface-variant font-label-md text-sm">
            Personal Workspace
          </span>
        </div>

        {/* Center Mode Toggle */}
        <div className="absolute left-1/2 -translate-x-1/2 hidden md:flex items-center bg-surface-container-low rounded-lg p-1 border border-outline-variant/30">
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
          <ThemeToggle />
          {guest ? (
            <button
              onClick={() => { window.location.href = "/"; }}
              title="Sign in to save your work and unlock bulk & annex"
              className="ml-xs h-8 px-3 rounded-md font-label-md text-[13px] bg-secondary-container text-white hover:bg-[#003ea8] transition-colors inline-flex items-center gap-1.5"
            >
              <span className="material-symbols-outlined text-[18px]">login</span>
              Sign in
            </button>
          ) : (
            <div className="relative ml-xs" ref={menuRef}>
              <button
                onClick={() => setMenuOpen((v) => !v)}
                title="Account"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                className="w-8 h-8 rounded-full bg-surface-container-high border border-outline-variant/50 overflow-hidden cursor-pointer hover:border-accent-cyan/50 transition-colors flex items-center justify-center"
              >
                <img
                  alt="User avatar"
                  className="w-full h-full object-cover"
                  src="https://lh3.googleusercontent.com/aida-public/AB6AXuB9e-ZXz4fzaPxmxwTxGC9xj1jqiInEDBT2XXjBgtn-vxeUTE16SE0kP3OjWlRkgFfldtdBAQIUQCD5dNw9WEj5QBET7PAyCxMvBx_MUR9T41yFpF2TlDAzn4Gsg3QkdkBTEF2ZAW9-UD53iYpnqII1e7J01kKRLHKzUV6ZNoT36qZOe5TfhgEXyrisP0wfj_qaPOrTmwjEfsQryO0AqyRI_cU99QHfdgPgSY4zxt6n3vaBGHOPk1-1imzfYgKQJwQ_LW0gub_-NdWd"
                />
              </button>
              {menuOpen && (
                <div className="absolute right-0 mt-2 w-40 rounded-lg bg-surface-container border border-outline-variant/40 shadow-panel py-1 z-50 animate-drop">
                  <button
                    onClick={() => {
                      setMenuOpen(false);
                      onSignOut?.();
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left font-label-md text-[13px] text-on-surface hover:bg-surface-container-high transition-colors"
                  >
                    <span className="material-symbols-outlined text-[18px]">logout</span>
                    Log out
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </nav>

      {/* Main Workspace Canvas */}
      <main className="pt-20 min-h-screen flex flex-col px-lg pb-lg md:px-xl md:pb-xl max-w-[1100px] mx-auto w-full relative z-10 animate-fade">
        {/* Ambient Glow */}
        <div className="absolute top-[20%] left-[50%] -translate-x-1/2 w-[600px] h-[600px] bg-accent-cyan/5 rounded-full blur-[120px] pointer-events-none -z-10"></div>

        <div className="flex-1 flex flex-col w-full mx-auto">
          {/* Guest banner — edit/sign/download work now; sign-in unlocks more */}
          {guest && (
            <div className="mb-md flex items-center gap-2 rounded-xl border border-secondary-container/30 bg-secondary-container/10 px-4 py-2.5 text-caption text-on-surface">
              <span className="material-symbols-outlined text-[18px] text-secondary shrink-0">info</span>
              <span className="flex-1">
                You're using Redraft as a guest — edit, sign &amp; download work right away.{" "}
                <b>Sign in</b> to save your work and unlock bulk generation &amp; annex automation.
              </span>
              <button
                onClick={() => { window.location.href = "/"; }}
                className="shrink-0 px-3 py-1 rounded-md bg-secondary-container text-white font-label-md text-[12px] hover:bg-[#003ea8] transition-colors"
              >
                Sign in
              </button>
            </div>
          )}
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
              className={`relative w-full rounded-2xl bg-surface-container-low/40 backdrop-blur-xl border transition-all duration-300 group cursor-pointer overflow-hidden flex flex-col items-center justify-center py-[60px] px-lg mb-lg shadow-panel mt-2 ${
                drag
                  ? "border-accent-cyan/30 bg-surface-container-low/60"
                  : "border-outline-variant/30 hover:border-accent-cyan/30 hover:bg-surface-container-low/60"
              }`}
            >
              <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-on-surface/10 group-hover:ring-accent-cyan/20 transition-all pointer-events-none"></div>
              <div className="w-14 h-14 rounded-2xl bg-surface-container flex items-center justify-center mb-md shadow-soft border border-outline-variant/30 group-hover:-translate-y-1 transition-transform duration-300">
                <span className="material-symbols-outlined text-[28px] text-on-surface-variant group-hover:text-accent-cyan transition-colors">
                  {busy ? "hourglass_top" : "upload_file"}
                </span>
              </div>
              <h2 className="font-display-md text-[20px] text-on-surface mb-xs font-semibold tracking-tight">
                {busy ? "Reading PDF…" : "Drop PDF here"}
              </h2>
              <p className="font-body-md text-sm text-on-surface-variant mb-md text-center max-w-sm">
                Edit values in place, generate hundreds of documents from a spreadsheet, or
                automate billing annexes — all from one PDF.
              </p>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  inputRef.current?.click();
                }}
                disabled={busy}
                className="font-label-md text-sm px-lg py-2 rounded-lg bg-accent-cyan text-[#080c14] hover:bg-[#00d0d9] shadow-[0_0_15px_rgba(0,245,255,0.2)] transition-all font-semibold active:scale-95 disabled:opacity-50"
              >
                Browse Files
              </button>
              <p className="mt-md flex items-center gap-1.5 text-caption text-on-surface-variant/70">
                <span className="material-symbols-outlined text-[14px]">lock</span>
                Your files are processed in memory and never stored.
              </p>
              {error && (
                <p className="mt-md text-sm text-error text-center max-w-sm">{error}</p>
              )}
            </div>
          )}

          {/* Start from text → clean PDF, straight into the editor */}
          {tab === "editor" && (
            <div className="-mt-sm mb-lg flex items-center justify-center gap-2">
              <span className="text-caption text-on-surface-variant/60">or</span>
              <button
                onClick={() => {
                  setTxtError(null);
                  setShowText(true);
                }}
                className="font-label-md text-sm px-md py-1.5 rounded-lg border border-outline-variant/50 text-on-surface hover:border-accent-cyan/50 hover:text-accent-cyan transition-colors flex items-center gap-2"
              >
                <span className="material-symbols-outlined text-[18px]">edit_note</span>
                Start from text
              </button>
            </div>
          )}

          {/* My templates (cloud) */}
          {cloudEnabled && projects.length > 0 && (
            <section className="mt-2 mb-lg">
              <div className="flex items-center justify-between mb-md border-b border-outline-variant/30 pb-sm">
                <h3 className="font-label-md text-sm text-on-surface font-medium flex items-center gap-sm">
                  <span className="material-symbols-outlined text-[18px] text-secondary">cloud_done</span>
                  My templates
                </h3>
                <span className="text-on-surface-variant font-caption text-[11px]">
                  {projects.length} of 3 saved
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-md">
                {projects.map((p) => (
                  <div
                    key={p.id}
                    onClick={() => openingId == null && openProject(p)}
                    className="group relative rounded-xl bg-surface-container-low border border-secondary-container/30 p-4 hover:border-secondary-container/60 transition-all cursor-pointer hover:-translate-y-0.5 shadow-sm"
                  >
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        removeProject(p);
                      }}
                      title="Remove from account"
                      className="absolute top-2 right-2 z-10 w-6 h-6 rounded-md bg-black/40 text-on-surface-variant opacity-0 group-hover:opacity-100 hover:text-error hover:bg-black/60 transition-all flex items-center justify-center"
                    >
                      <span className="material-symbols-outlined text-[16px]">delete</span>
                    </button>
                    <div className="flex items-center gap-3">
                      <div className="w-12 h-16 rounded-md bg-white border border-outline-variant/40 overflow-hidden shrink-0 flex items-center justify-center shadow-sm">
                        {openingId === p.id ? (
                          <span className="material-symbols-outlined text-[18px] text-secondary animate-spin">
                            progress_activity
                          </span>
                        ) : thumbs[p.id] ? (
                          <img
                            src={thumbs[p.id]}
                            alt=""
                            className="w-full h-full object-cover object-top"
                          />
                        ) : (
                          <span className="material-symbols-outlined text-[22px] text-secondary/50">
                            description
                          </span>
                        )}
                      </div>
                      <div className="min-w-0">
                        <h4 className="font-body-md text-sm text-on-surface font-medium truncate">
                          {p.name}
                        </h4>
                        <p className="text-on-surface-variant font-caption text-[11px] flex items-center gap-1">
                          <span className="material-symbols-outlined text-[13px]">layers</span>
                          Bulk template · {(p.setup?.picked?.length) || 0} fields
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Recent Activity Section */}
          <section className="mt-2 flex-1">
            <div className="flex items-center justify-between mb-md border-b border-outline-variant/30 pb-sm">
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
              <div className="rounded-xl border border-dashed border-outline-variant/50 p-10 text-center">
                <span className="material-symbols-outlined text-[32px] text-on-surface-variant/40">
                  folder_open
                </span>
                <p className="mt-2 text-sm text-on-surface-variant">
                  No documents yet — upload a PDF and it'll appear here.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-md">
                {docs.map((d, i) => (
                  <div
                    key={d.id}
                    onClick={() => onOpen(d)}
                    style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}
                    className="group relative rounded-xl bg-surface-container-low border border-outline-variant/30 p-4 hover:border-outline-variant/50 transition-all cursor-pointer hover:-translate-y-0.5 shadow-sm hover:shadow-md animate-rise"
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
                    <div className="aspect-[4/3] rounded-lg bg-surface-container-lowest mb-md border border-outline-variant/30 flex items-center justify-center overflow-hidden relative">
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
                    <h4 className="font-body-md text-sm text-on-surface font-medium truncate mb-xs group-hover:text-accent-cyan transition-colors">
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

      {/* Start-from-text composer */}
      {showText && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fade"
          onMouseDown={(e) => e.target === e.currentTarget && !composing && setShowText(false)}
        >
          <div className="w-full max-w-2xl bg-surface-container rounded-2xl border border-outline-variant/40 shadow-panel overflow-hidden animate-drop">
            <div className="px-5 py-4 border-b border-outline-variant/30 flex items-center justify-between">
              <h3 className="font-display-md text-lg font-bold flex items-center gap-2">
                <span className="material-symbols-outlined text-[20px] text-accent-cyan">edit_note</span>
                Start from text
              </h3>
              <button
                onClick={() => !composing && setShowText(false)}
                className="text-on-surface-variant hover:text-on-surface transition-colors"
              >
                <span className="material-symbols-outlined text-[20px]">close</span>
              </button>
            </div>
            <div className="p-5 space-y-3">
              <p className="text-caption text-on-surface-variant">
                Paste any text — a contract, a letter, an agreement. Redraft turns it into a clean,
                formatted PDF you can edit and sign right away.
              </p>
              <input
                value={txtTitle}
                onChange={(e) => setTxtTitle(e.target.value)}
                placeholder="Title (optional) — e.g. Service Agreement"
                className="w-full bg-surface-container-lowest border border-outline-variant/50 rounded-lg py-2 px-3 text-sm text-on-surface focus:outline-none focus:ring-1 focus:ring-accent-cyan"
              />
              <textarea
                value={txtBody}
                onChange={(e) => setTxtBody(e.target.value)}
                placeholder={
                  "Paste your document text here…\n\nTip: leave a blank line between paragraphs. A short line in CAPITALS or ending with ':' becomes a section heading."
                }
                className="w-full h-64 bg-surface-container-lowest border border-outline-variant/50 rounded-lg p-3 text-sm text-on-surface leading-relaxed focus:outline-none focus:ring-1 focus:ring-accent-cyan resize-y"
              />
              {txtError && <p className="text-caption text-error">{txtError}</p>}
            </div>
            <div className="px-5 py-4 border-t border-outline-variant/30 flex items-center justify-end gap-2">
              <button
                onClick={() => !composing && setShowText(false)}
                className="px-4 py-2 rounded-lg border border-outline-variant/50 text-on-surface hover:bg-surface-container-high transition-colors font-label-md text-sm"
              >
                Cancel
              </button>
              <button
                onClick={createFromText}
                disabled={composing || !txtBody.trim()}
                className="px-4 py-2 rounded-lg bg-accent-cyan text-[#080c14] font-semibold hover:bg-[#00d0d9] transition-all font-label-md text-sm flex items-center gap-2 disabled:opacity-50"
              >
                <span className={`material-symbols-outlined text-[18px] ${composing ? "animate-spin" : ""}`}>
                  {composing ? "progress_activity" : "auto_awesome"}
                </span>
                {composing ? "Building…" : "Create & open in editor"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

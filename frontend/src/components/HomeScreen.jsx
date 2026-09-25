import { useCallback, useEffect, useRef, useState } from "react";
import { useHistory, removeDoc, ago, getRecord, putRecord } from "../lib/history.js";
import { cloudEnabled, listProjects, deleteProject, openProjectFile, saveProject } from "../lib/cloud.js";
import { renderThumb } from "../lib/thumb.js";
import { composeDoc } from "../api.js";
import ThemeToggle from "./ThemeToggle.jsx";
import { toast } from "./Toast.jsx";
import { Tabs, Dropdown, Avatar, Label } from "@heroui/react";
import Icon from "./Icon.jsx";
import Brand from "./Brand.jsx";

const STATUS = {
  Draft: "bg-[rgb(var(--c-tint-yellow))] text-on-surface",
  Final: "bg-[rgb(var(--c-tint-mint))] text-on-surface",
  Review: "bg-[rgb(var(--c-tint-blue))] text-on-surface",
};

const PILL = "inline-flex items-center justify-center gap-2 rounded-full font-normal text-[16px] leading-5 px-[26px] py-[14px] cursor-pointer select-none transition-[transform,background,box-shadow] duration-200 hover:-translate-y-0.5 disabled:opacity-60 disabled:hover:translate-y-0";
const PILL_BLUE = `${PILL} bg-secondary-container text-white hover:bg-secondary-container-hover hover:shadow-[0_8px_22px_rgba(79,117,254,0.35)]`;
const PILL_SAND = `${PILL} bg-surface-container-high text-on-surface hover:bg-surface-variant`;
const HEAD = "font-display-md font-black tracking-[-0.5px] text-on-surface";

/**
 * Home screen — the Homerun-derived design system (sand page, cream sheets, one blue).
 * Drop Zone + Browse Files perform the real upload (`onUpload`); Recent Activity
 * is the real, persistent history (`onOpen` reopens a doc).
 */
export default function HomeScreen({ onUpload, onOpen, onOpenCloud, busy, error, onSignOut, guest = false }) {
  const inputRef = useRef(null);
  const [drag, setDrag] = useState(false);
  const [tab, setTab] = useState("editor"); // "editor" | "history"
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
    let bytes = null;
    try {
      const f = await openProjectFile(p);
      bytes = await f.arrayBuffer();
    } catch {
      /* deletion proceeds without undo if the file can't be fetched */
    }
    await deleteProject(p);
    loadProjects();
    if (bytes) {
      toast(`Removed template “${p.name}”`, {
        actionLabel: "Undo",
        onAction: async () => {
          try {
            await saveProject(
              new File([bytes], `${p.name}.pdf`, { type: "application/pdf" }),
              { name: p.name, kind: p.kind || "bulk", setup: p.setup || {}, pages: p.pages || 1 }
            );
          } catch {
            /* free cap may be filled meanwhile — template stays deleted */
          }
          loadProjects();
        },
      });
    }
  }

  async function removeDocUndoable(id, name) {
    const rec = await getRecord(id);
    if (!rec) return;
    await removeDoc(id);
    toast(`Removed “${name}” from history`, {
      actionLabel: "Undo",
      onAction: () => putRecord(rec),
    });
  }

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

  useEffect(() => {
    if (!showText || composing) return;
    const onKey = (e) => {
      if (e.key === "Escape") setShowText(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showText, composing]);

  const composerRef = useRef(null);
  function trapComposerTab(e) {
    if (e.key !== "Tab") return;
    const root = composerRef.current;
    if (!root) return;
    const focusables = [
      ...root.querySelectorAll(
        'button, input, textarea, select, [tabindex]:not([tabindex="-1"])'
      ),
    ].filter((el) => !el.disabled && el.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  const browse = () => inputRef.current?.click();
  const openComposer = () => {
    setTxtError(null);
    setShowText(true);
  };
  const dropProps = {
    onDragOver: (e) => {
      e.preventDefault();
      setDrag(true);
    },
    onDragLeave: () => setDrag(false),
    onDrop: (e) => {
      e.preventDefault();
      setDrag(false);
      pick(e.dataTransfer.files?.[0]);
    },
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => pick(e.target.files?.[0])}
      />

      {/* Top bar — the landing page's banner: a floating white pill */}
      <header className="fixed top-3 left-1/2 -translate-x-1/2 z-50 w-[min(1100px,calc(100%-24px))]">
        <nav className="relative flex items-center justify-between h-[68px] pl-5 md:pl-6 pr-2.5 rounded-full bg-surface/95 backdrop-blur-md shadow-[0_1px_0_rgb(var(--c-shadow)/0.04),0_12px_32px_-18px_rgb(var(--c-shadow)/0.35)]">
          <div className="flex items-center gap-3 min-w-0">
            <Brand />
            <span className="hidden lg:block h-5 w-px bg-outline-variant" />
            <span className="hidden lg:block text-[15px] text-on-surface-variant">Personal workspace</span>
          </div>

          <Tabs
            className="absolute left-1/2 -translate-x-1/2 hidden md:flex"
            selectedKey={tab}
            onSelectionChange={(key) => setTab(key)}
          >
            <Tabs.ListContainer className="rounded-2xl bg-surface-container">
              <Tabs.List
                aria-label="View"
                className="p-1 **:data-[slot=tabs-tab]:rounded-xl **:data-[slot=tabs-tab]:text-[15px] **:data-[slot=tabs-tab]:px-5 **:data-[slot=tabs-tab]:py-2 **:data-[slot=tabs-indicator]:rounded-xl **:data-[slot=tabs-indicator]:bg-surface-bright **:data-[slot=tabs-indicator]:shadow-soft"
              >
                <Tabs.Tab id="editor">
                  Editor
                  <Tabs.Indicator />
                </Tabs.Tab>
                <Tabs.Tab id="history">
                  History
                  <Tabs.Indicator />
                </Tabs.Tab>
              </Tabs.List>
            </Tabs.ListContainer>
          </Tabs>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            {guest ? (
              <button
                onClick={() => { window.location.href = "/"; }}
                title="Sign in to save your work and unlock bulk & annex"
                className={`${PILL_BLUE} !px-5 !py-[11px] !text-[15px]`}
              >
                <Icon name="login" size={18} />
                Sign in
              </button>
            ) : (
              <Dropdown>
                <Dropdown.Trigger
                  title="Account"
                  className="w-10 h-10 rounded-full bg-surface-container text-on-surface hover:bg-surface-container-high grid place-items-center transition-colors"
                >
                  <Avatar className="!bg-transparent !text-on-surface">
                    <Avatar.Fallback className="!bg-transparent"><Icon name="user" size={20} /></Avatar.Fallback>
                  </Avatar>
                </Dropdown.Trigger>
                <Dropdown.Popover placement="bottom end">
                  <Dropdown.Menu onAction={() => onSignOut?.()}>
                    <Dropdown.Item id="log-out" textValue="Log out">
                      <Icon name="logout" size={18} />
                      <Label>Log out</Label>
                    </Dropdown.Item>
                  </Dropdown.Menu>
                </Dropdown.Popover>
              </Dropdown>
            )}
          </div>
        </nav>
      </header>

      <main className="pt-[112px] pb-16 px-3 md:px-6 min-h-screen max-w-[1148px] mx-auto w-full animate-fade">
        {/* Guest note — edit/sign/download work now; sign-in unlocks more */}
        {guest && (
          <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[28px] md:rounded-full bg-surface pl-2.5 pr-2.5 py-2.5 text-[15px] text-on-surface">
            <span className="shrink-0 rounded-full bg-[rgb(var(--c-tint-yellow))] px-3 py-1 text-[13px] font-semibold">Guest</span>
            <span className="flex-1 min-w-[16rem] leading-6 text-on-surface-variant">
              Edit, sign &amp; download work right away.{" "}
              <b className="text-on-surface font-semibold">Sign in</b> to save your work and unlock bulk generation &amp; annex automation.
            </span>
          </div>
        )}

        {/* Drop zone */}
        {tab === "editor" && (
          <section
            {...dropProps}
            className="relative grid md:grid-cols-[1.05fr_0.95fr] gap-6 md:gap-8 rounded-[32px] bg-surface p-5 md:p-10"
          >
            <div className="flex flex-col justify-center gap-5 md:pr-2 py-2">
              <p className="font-hand uppercase tracking-[0.06em] text-[22px] leading-none text-secondary-container">
                Your workspace
              </p>
              <h1 className={`${HEAD} text-[42px] md:text-[56px] leading-[1.02]`}>
                {busy ? (
                  <>Reading<br />your PDF…</>
                ) : (
                  <>Drop a PDF.<br />Edit it exactly<br />how you want.</>
                )}
              </h1>
              <p className="text-[17px] leading-[27px] text-on-surface-variant max-w-[30rem]">
                Edit values in place, generate hundreds of documents from a spreadsheet, or automate billing
                annexes — all from one PDF.
              </p>
              <div className="flex flex-wrap gap-2.5 mt-1">
                <button onClick={browse} disabled={busy} className={PILL_BLUE}>
                  <Icon name={busy ? "spinner" : "upload"} size={19} spin={busy} />
                  Browse files
                </button>
                <button onClick={openComposer} className={PILL_SAND}>
                  <Icon name="pen" size={19} />
                  Start from text
                </button>
              </div>
              <p className="flex items-center gap-1.5 text-[14px] text-on-surface-variant">
                <Icon name="lock" size={15} />
                Your files are processed in memory and never stored.
              </p>
              {error && <p className="text-[15px] text-error max-w-[26rem]">{error}</p>}
            </div>

            <button
              type="button"
              onClick={browse}
              aria-label="Choose a PDF, or drop one here"
              className={`group relative min-h-[260px] md:min-h-[360px] rounded-[26px] grid place-items-center overflow-hidden cursor-pointer transition-colors duration-200 ${
                drag ? "bg-[rgb(var(--c-tint-blue))]" : "bg-[rgb(var(--c-tint-yellow))]"
              }`}
            >
              <span
                className={`pointer-events-none absolute inset-4 rounded-[20px] border-[2.5px] border-dashed transition-colors ${
                  drag ? "border-secondary-container" : "border-on-surface/25 group-hover:border-on-surface/45"
                }`}
              />
              <svg viewBox="0 0 260 230" className="w-[62%] max-w-[300px] transition-transform duration-300 group-hover:-translate-y-1 group-hover:rotate-[-1.5deg]" aria-hidden="true">
                <rect x="58" y="22" width="132" height="172" rx="14" fill="#fff" stroke="rgb(var(--c-on-surface))" strokeWidth="5" />
                <path d="M82 62h84M82 86h84M82 110h52" stroke="rgb(45 35 35)" strokeWidth="5" strokeLinecap="round" />
                <rect x="82" y="128" width="58" height="22" rx="7" fill="#8aa0ff" stroke="#2d2323" strokeWidth="4" />
                <circle cx="188" cy="176" r="34" fill="#4f75fe" stroke="rgb(var(--c-on-surface))" strokeWidth="5" />
                <path d="M188 192v-30M175 174l13-13 13 13" fill="none" stroke="#fff" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M206 38l4.5 13 13 4.5-13 4.5-4.5 13-4.5-13-13-4.5 13-4.5z" fill="#ff8a3d" stroke="rgb(var(--c-on-surface))" strokeWidth="4" strokeLinejoin="round" />
              </svg>
              <span className="absolute bottom-7 left-0 right-0 text-center font-hand uppercase tracking-[0.06em] text-[19px] text-on-surface/70">
                {drag ? "Let go to open it" : "or drop it here"}
              </span>
            </button>
          </section>
        )}

        {/* My templates (cloud): same card as Recent activity, on a blue tint */}
        {cloudEnabled && projects.length > 0 && (
          <section className="mt-10">
            <div className="flex items-center justify-between mb-4 px-1">
              <h2 className={`${HEAD} text-[24px] flex items-center gap-3`}>
                <span className="w-9 h-9 rounded-xl bg-[rgb(var(--c-tint-blue))] grid place-items-center"><Icon name="cloud" size={20} /></span>
                My templates
              </h2>
              <span className="rounded-full bg-surface px-3 py-1 text-[13px] text-on-surface-variant">{projects.length} of 3 saved</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {projects.map((p) => (
                <div
                  key={p.id}
                  onClick={() => openingId == null && openProject(p)}
                  className="group relative rounded-3xl bg-surface p-3 pb-4 cursor-pointer transition-transform duration-200 hover:-translate-y-1 animate-rise"
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeProject(p);
                    }}
                    title="Remove from account"
                    aria-label="Remove template from account"
                    className="absolute top-5 right-5 z-10 w-8 h-8 rounded-full bg-surface text-on-surface-variant shadow-soft opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 hover:text-error transition-all grid place-items-center"
                  >
                    <Icon name="trash" size={16} />
                  </button>
                  <div className="aspect-[4/3] rounded-[20px] bg-[rgb(var(--c-tint-blue))] mb-3.5 relative overflow-hidden grid place-items-center">
                    <span className="absolute top-3 left-3 z-10 inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-[12px] font-semibold text-on-surface">
                      <Icon name="layers" size={13} />
                      Bulk template
                    </span>
                    {openingId === p.id ? (
                      <Icon name="spinner" size={28} spin className="text-secondary-container" />
                    ) : thumbs[p.id] ? (
                      <div className="absolute left-[16%] right-[16%] top-9 bottom-0 rounded-t-lg bg-white shadow-[0_10px_24px_-10px_rgb(var(--c-shadow)/0.45)] overflow-hidden">
                        <img src={thumbs[p.id]} alt="" className="w-full h-full object-cover object-top" />
                      </div>
                    ) : (
                      <Icon name="doc" size={34} className="text-on-surface/30" />
                    )}
                  </div>
                  <h3 className="px-1.5 text-[16px] font-semibold text-on-surface truncate mb-1 group-hover:text-secondary-container transition-colors">
                    {p.name}
                  </h3>
                  <div className="px-1.5 flex items-center justify-between text-on-surface-variant text-[13px]">
                    <span>{(p.setup?.picked?.length) || 0} field{(p.setup?.picked?.length) === 1 ? "" : "s"} to fill</span>
                    <span className="flex items-center gap-1">
                      <Icon name="doc" size={14} />
                      {p.pages || 1}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Recent activity */}
        <section className="mt-10">
          <div className="flex items-center justify-between mb-4 px-1">
            <h2 className={`${HEAD} text-[24px] flex items-center gap-3`}>
              <span className="w-9 h-9 rounded-xl bg-[rgb(var(--c-tint-sand))] grid place-items-center"><Icon name="history" size={20} /></span>
              {tab === "history" ? "History" : "Recent activity"}
            </h2>
            <span className="rounded-full bg-surface px-3 py-1 text-[13px] text-on-surface-variant">
              {docs.length} document{docs.length === 1 ? "" : "s"}
            </span>
          </div>

          {docs.length === 0 ? (
            <div className="rounded-[28px] bg-surface p-10 md:p-14 text-center">
              <div className="mx-auto w-16 h-16 rounded-2xl bg-[rgb(var(--c-tint-sand))] grid place-items-center">
                <Icon name="folder" size={30} strokeWidth={2} className="text-on-surface" />
              </div>
              <p className={`${HEAD} text-[22px] mt-4`}>Nothing here yet</p>
              <p className="mt-1.5 text-[16px] text-on-surface-variant">Upload a PDF and it will appear here.</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {docs.map((d, i) => (
                <div
                  key={d.id}
                  onClick={() => onOpen(d)}
                  style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}
                  className="group relative rounded-3xl bg-surface p-3 pb-4 cursor-pointer transition-transform duration-200 hover:-translate-y-1 animate-rise"
                >
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeDocUndoable(d.id, d.name);
                    }}
                    className="absolute top-5 right-5 z-10 w-8 h-8 rounded-full bg-surface text-on-surface-variant shadow-soft opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 hover:text-error transition-all grid place-items-center"
                    title="Remove from history"
                  >
                    <Icon name="close" size={16} />
                  </button>
                  <div className="aspect-[4/3] rounded-[20px] bg-[rgb(var(--c-tint-sand))] mb-3.5 relative overflow-hidden grid place-items-center">
                    <span className={`absolute top-3 left-3 z-10 rounded-full px-2.5 py-1 text-[12px] font-semibold ${STATUS[d.status] || STATUS.Draft}`}>
                      {d.status || "Draft"}
                    </span>
                    {d.thumb ? (
                      <div className="absolute left-[16%] right-[16%] top-9 bottom-0 rounded-t-lg bg-white shadow-[0_10px_24px_-10px_rgb(var(--c-shadow)/0.45)] overflow-hidden">
                        <img src={d.thumb} alt={d.name} className="w-full h-full object-cover object-top" />
                      </div>
                    ) : (
                      <Icon name="doc" size={34} className="text-on-surface/30" />
                    )}
                  </div>
                  <h3 className="px-1.5 text-[16px] font-semibold text-on-surface truncate mb-1 group-hover:text-secondary-container transition-colors">
                    {d.name}
                  </h3>
                  <div className="px-1.5 flex items-center justify-between text-on-surface-variant text-[13px]">
                    <span>Edited {ago(d.addedAt)}</span>
                    <span className="flex items-center gap-1">
                      <Icon name="doc" size={14} />
                      {d.pages || 1}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      {/* Start-from-text composer */}
      {showText && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-[rgb(var(--c-shadow)/0.5)] backdrop-blur-sm p-4 animate-fade"
          onMouseDown={(e) => e.target === e.currentTarget && !composing && setShowText(false)}
        >
          <div
            ref={composerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Start from text"
            onKeyDown={trapComposerTab}
            className="w-full max-w-[42rem] bg-surface rounded-[32px] shadow-panel overflow-hidden animate-drop"
          >
            <div className="px-7 pt-6 pb-2 flex items-start justify-between gap-4">
              <div>
                <p className="font-hand uppercase tracking-[0.06em] text-[19px] leading-none text-secondary-container">Start from text</p>
                <h3 className={`${HEAD} text-[30px] leading-[1.1] mt-2`}>Turn text into a clean PDF</h3>
              </div>
              <button
                onClick={() => !composing && setShowText(false)}
                aria-label="Close"
                className="w-10 h-10 shrink-0 rounded-full bg-surface-container text-on-surface hover:bg-surface-container-high transition-colors grid place-items-center"
              >
                <Icon name="close" size={18} />
              </button>
            </div>
            <div className="px-7 pb-2 space-y-3">
              <p className="text-[15px] leading-6 text-on-surface-variant">
                Paste a contract, a letter or an agreement. Redraft formats it into a clean PDF you can edit and sign right away.
              </p>
              <input
                value={txtTitle}
                onChange={(e) => setTxtTitle(e.target.value)}
                autoFocus
                placeholder="Title (optional), e.g. Service Agreement"
                className="w-full bg-surface-container rounded-2xl py-3.5 px-5 text-[16px] text-on-surface placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-2 focus:ring-secondary-container"
              />
              <textarea
                value={txtBody}
                onChange={(e) => setTxtBody(e.target.value)}
                placeholder={
                  "Paste your document text here…\n\nTip: leave a blank line between paragraphs. A short line in CAPITALS or ending with ':' becomes a section heading."
                }
                className="w-full h-64 bg-surface-container rounded-2xl p-5 text-[15px] text-on-surface leading-relaxed placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-2 focus:ring-secondary-container resize-y"
              />
              {txtError && <p className="text-[14px] text-error">{txtError}</p>}
            </div>
            <div className="px-7 py-6 flex items-center justify-end gap-2.5">
              <button onClick={() => !composing && setShowText(false)} className={PILL_SAND}>
                Cancel
              </button>
              <button onClick={createFromText} disabled={composing || !txtBody.trim()} className={PILL_BLUE}>
                <Icon name={composing ? "spinner" : "spark"} size={18} spin={composing} />
                {composing ? "Building…" : "Create & open in editor"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

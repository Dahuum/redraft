import { useEffect, useRef, useState } from "react";
import { useEditor } from "./useEditor.js";
import { ping, composeDoc } from "./api.js";
import { addDoc, getRecord, patchDoc, getAllRecords } from "./lib/history.js";
import { renderThumb } from "./lib/thumb.js";
import { usePath, parseRoute, navigate } from "./lib/router.js";
import HomeScreen from "./components/HomeScreen.jsx";
import EditorWorkspace from "./components/EditorWorkspace.jsx";
import BulkWorkspace from "./components/BulkWorkspace.jsx";
import AnnexWorkspace from "./components/AnnexWorkspace.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import { useAuth } from "./lib/useAuth.js";
import { usePlan } from "./lib/usePlan.js";
import { openProjectFile } from "./lib/cloud.js";

export default function App() {
  const route = parseRoute(usePath());
  const { view, mode, docId } = route;
  const ed = useEditor();
  const auth = useAuth();
  const { plan, refresh: refreshPlan } = usePlan(view); // re-checks usage on nav
  // A logged-out visitor using Redraft without an account (editor-only). Bulk,
  // annex and cloud save are gated on this below.
  const guestMode = auth.enabled && !auth.user;
  const loadedDocIdRef = useRef(null); // which history doc is currently in `ed`
  const [cloudProjectId, setCloudProjectId] = useState(null); // open saved template (live)
  // True while a ?compose= deep-link is being turned into a document, so we show
  // a loader and go straight to the editor — never flash the home/overview page.
  const [composing, setComposing] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).has("compose");
    } catch {
      return false;
    }
  });

  // Wake the (free-tier, sleeps-when-idle) backend the instant the app loads,
  // so it's warm by the time the user clicks Upload/Generate — no dropped first
  // click. Fire-and-forget; retry once a few seconds later in case it was cold.
  useEffect(() => {
    ping();
    const t = setTimeout(() => ping(), 4000);
    return () => clearTimeout(t);
  }, []);

  // Guest session: a logged-out visitor who arrived via a ?compose= link. They
  // get an editor-only session (compose → edit → sign → download, metered), but
  // not the home overview / bulk / annex / cloud — those still require login.
  const [guest, setGuest] = useState(false);

  // WS0 analytics: tie all events to the signed-in user (enables retention/WAC),
  // and record when an anonymous guest session begins.
  useEffect(() => {
    if (auth.user?.id) window.rdIdentify?.(auth.user.id, { email: auth.user.email });
  }, [auth.user]);
  useEffect(() => {
    if (guest) window.rdTrack?.("guest_start");
  }, [guest]);

  // WS1 — guest is the front door: a logged-out visitor uses Redraft as a guest
  // (home + editor: upload → edit → sign → download, IP-metered). Bulk, annex and
  // cloud save stay behind sign-in and are gated in the UI below. (Supabase not
  // configured → fully open, no gating at all.)
  useEffect(() => {
    if (!auth.enabled || !auth.ready || auth.user) return;
    setGuest(true);
  }, [auth.enabled, auth.ready, auth.user]);

  // Deep-link entry: /app.html?compose=<text>&title=<title> — compose the text
  // into a clean PDF and open it straight in the editor. This is the shareable
  // "Create in Redraft" button: any site links here, the user lands editing —
  // works for signed-in users AND logged-out guests.
  const composedRef = useRef(false);
  useEffect(() => {
    if (composedRef.current) return;
    if (auth.enabled && !auth.ready) return; // wait until auth resolves
    const params = new URLSearchParams(window.location.search);
    const text = params.get("compose");
    if (!text) return;
    composedRef.current = true;
    if (auth.enabled && !auth.user) setGuest(true);
    window.rdTrack?.("compose_submit", { source: "deeplink" });
    const title = params.get("title") || "";
    (async () => {
      try {
        const blob = await composeDoc(text, title);
        const name = (title.trim() || "document").replace(/[^\w \-]/g, "").slice(0, 60) || "document";
        // navigate() (inside handleUpload) rewrites the path to /editor and drops
        // the ?compose= query, so a refresh won't re-compose.
        await handleUpload(new File([blob], `${name}.pdf`, { type: "application/pdf" }));
      } catch (e) {
        window.alert(e.message || "Couldn't create the document from the link.");
      } finally {
        setComposing(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth.enabled, auth.ready, auth.user]);

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

  // Deep-link / refresh: if the URL points at an editor doc we don't have in
  // memory, load it from history. Bad/missing id → bounce home.
  useEffect(() => {
    if (view !== "editor") return;
    if (!docId) {
      navigate("/", { replace: true });
      return;
    }
    // "memory" = an in-memory doc (cloud template / composed / storage-off upload)
    // that is NOT in history. Trust loadedDocIdRef (set synchronously before we
    // navigated here) instead of the async ed.file state — otherwise a render
    // race makes us look it up, fail, and bounce home (the "click twice" bug).
    // On a hard refresh the in-memory doc is gone (ref reset) → go home cleanly.
    if (docId === "memory") {
      if (loadedDocIdRef.current !== "memory") navigate("/", { replace: true });
      return;
    }
    if (loadedDocIdRef.current === docId && ed.file) return; // already loaded
    let cancelled = false;
    (async () => {
      const rec = await getRecord(docId);
      if (cancelled) return;
      if (!rec || !rec.bytes) {
        navigate("/", { replace: true });
        return;
      }
      const file = new File([rec.bytes], rec.name || "document.pdf", {
        type: "application/pdf",
      });
      const res = await ed.loadFile(file);
      if (cancelled) return;
      if (res) loadedDocIdRef.current = docId;
      else navigate("/", { replace: true });
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, docId]);

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
    setCloudProjectId(null); // a fresh upload is not a saved template
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
    } catch {
      /* history is best-effort */
    }
    if (id) {
      loadedDocIdRef.current = id; // we already hold this doc in `ed`
      navigate(`/${bulk ? "bulk" : "editor"}/${id}`);
      renderThumb(buf).then((thumb) => thumb && patchDoc(id, { thumb }));
    } else {
      // No history id (storage off) — still open the editor with the in-memory doc.
      loadedDocIdRef.current = "memory";
      navigate(`/${bulk ? "bulk" : "editor"}/memory`);
    }
  }

  function openFromHistory(meta) {
    setCloudProjectId(null); // a local history doc is not a saved cloud template
    patchDoc(meta.id, { addedAt: Date.now() });
    navigate(`/editor/${meta.id}`);
  }

  // Open a cloud-saved template: download the PDF, seed its saved bulk setup so
  // BulkWorkspace applies it, then open the Bulk tab (rows start empty — paste
  // fresh data each time).
  // Open a saved template LIVE: load its PDF + setup, mark it the active cloud
  // project (so edits auto-sync back to it), and do NOT add a new history copy.
  async function openCloudProject(project) {
    try {
      const file = await openProjectFile(project);
      const res = await ed.loadFile(file);
      if (!res) return;
      const s = project.setup || {};
      try {
        localStorage.setItem(
          `redraft:bulk:${file.name}:${res.spans.length}`,
          JSON.stringify({
            picked: s.picked || [],
            rows: [],
            impMap: s.impMap || {},
            filenameId: s.filenameId ?? null,
            splits: s.splits || {},
          })
        );
      } catch {
        /* non-fatal */
      }
      setCloudProjectId(project.id); // edits now flow back to this saved template
      loadedDocIdRef.current = "memory";
      navigate(`/bulk/memory`);
    } catch (e) {
      window.alert(e.message || "Couldn't open this template.");
    }
  }

  async function handleDownload() {
    await ed.download();
    if (docId && docId !== "memory") patchDoc(docId, { status: "Final" });
    refreshPlan(); // usage may have gone up
  }

  // While auth is resolving (or redirecting an unauthenticated non-guest), hold a
  // loader. A guest compose session is allowed through.
  if (auth.enabled && (!auth.ready || (!auth.user && !guest))) {
    return (
      <div className="h-screen w-full flex items-center justify-center bg-background text-on-surface-variant">
        <span className="material-symbols-outlined animate-spin text-[28px]">progress_activity</span>
      </div>
    );
  }

  // Composing a ?compose= deep-link → loader, then straight into the editor
  // (never render the home/overview page in between).
  if (composing) {
    return (
      <div className="h-screen w-full flex flex-col items-center justify-center gap-3 bg-background text-on-surface-variant">
        <span className="material-symbols-outlined animate-spin text-[28px] text-accent-cyan">progress_activity</span>
        <p className="text-body-md">Preparing your document…</p>
      </div>
    );
  }

  if (view === "home") {
    return (
      <HomeScreen
        onUpload={handleUpload}
        onOpen={openFromHistory}
        onOpenCloud={openCloudProject}
        busy={ed.busy}
        error={ed.error}
        guest={guestMode}
        onSignOut={auth.enabled && !guestMode ? auth.signOut : null}
      />
    );
  }

  // Editor view (the approved dark "Text Fields" design)
  return (
    <div className="h-screen w-full flex flex-col overflow-hidden animate-fade bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-surface-container-high via-background to-background">
      {/* TopNavBar */}
      <header className="bg-surface/80 backdrop-blur-xl text-primary font-label-md text-label-md h-14 w-full border-b border-outline-variant flex justify-between items-center sticky top-0 z-30 px-6">
        <button
          onClick={() => navigate("/")}
          title="Back to your documents"
          className="flex items-center gap-2 text-on-surface hover:opacity-80 transition-opacity"
        >
          <span className="material-symbols-outlined text-[20px]">arrow_back</span>
          <span className="font-display-md text-[17px] font-bold tracking-tight">Redraft</span>
        </button>
        <div className="flex items-center gap-3">
          <ThemeToggle />
          <button className="h-8 px-3 inline-flex items-center rounded-md font-label-md text-[13px] text-on-surface border border-outline-variant hover:bg-surface-container-high transition-colors opacity-80 active:opacity-100">
            Share
          </button>
          <button
            onClick={handleDownload}
            disabled={ed.nEdits === 0 || ed.busy}
            className="h-8 px-3 rounded-md font-label-md text-[13px] bg-primary text-on-primary hover:bg-primary/90 transition-colors shadow-[0_0_15px_rgba(195,198,210,0.1)] opacity-80 active:opacity-100 inline-flex items-center gap-2 disabled:opacity-40"
          >
            <span className="material-symbols-outlined text-[18px]">download</span>
            Export PDF
          </button>
          {plan?.auth && plan.limit != null && (
            <span
              title="Documents generated this month"
              className={`h-8 px-2.5 inline-flex items-center rounded-md font-label-md text-[12px] border ${
                plan.used >= plan.limit
                  ? "border-error/40 text-error bg-error/10"
                  : "border-outline-variant text-on-surface-variant"
              }`}
            >
              {plan.used}/{plan.limit} docs
            </span>
          )}
          {auth.enabled && auth.user && (
            <button
              onClick={auth.signOut}
              title="Sign out"
              className="ml-1 h-8 px-3 rounded-md font-label-md text-[13px] text-on-surface border border-outline-variant hover:bg-surface-container-high transition-colors opacity-80 active:opacity-100 inline-flex items-center gap-1.5"
            >
              <span className="material-symbols-outlined text-[18px]">logout</span>
              Sign out
            </button>
          )}
          {auth.enabled && !auth.user && (
            <button
              onClick={() => { window.location.href = "/"; }}
              title="Sign in to save your work and get more documents"
              className="ml-1 h-8 px-3 rounded-md font-label-md text-[13px] bg-secondary-container text-white hover:bg-[#003ea8] transition-colors inline-flex items-center gap-1.5"
            >
              <span className="material-symbols-outlined text-[18px]">login</span>
              Sign in
            </button>
          )}
        </div>
      </header>

      {/* Mode toggle — guests get the editor only; Bulk/Annex need sign-in */}
      {guestMode ? (
        <div className="w-full flex justify-center py-2.5 bg-background border-b border-outline-variant/30">
          <div className="flex items-center gap-1.5 text-caption text-on-surface-variant bg-surface-container-high/60 border border-outline-variant/20 rounded-full px-4 py-1.5">
            <span className="material-symbols-outlined text-[15px] text-accent-cyan">lock_open</span>
            Bulk generation &amp; annex automation unlock when you
            <button
              onClick={() => { window.location.href = "/"; }}
              className="text-secondary font-medium hover:underline"
            >
              sign in
            </button>
          </div>
        </div>
      ) : (
        <div className="w-full flex justify-center py-3 bg-background border-b border-outline-variant/30">
          <div className="bg-surface-container-high p-1 rounded-full flex items-center gap-1 border border-outline-variant/20">
            <button
              onClick={() => navigate(`/editor/${docId}`)}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-full font-label-md text-sm transition-all ${
                mode === "editor"
                  ? "bg-secondary-container text-white shadow-lg"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">edit</span>
              PDF Editor
            </button>
            <button
              onClick={() => navigate(`/bulk/${docId}`)}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-full font-label-md text-sm transition-all ${
                mode === "bulk"
                  ? "bg-secondary-container text-white shadow-lg"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">layers</span>
              Bulk Generator
            </button>
            <button
              onClick={() => navigate(`/annex/${docId}`)}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-full font-label-md text-sm transition-all ${
                mode === "annex"
                  ? "bg-secondary-container text-white shadow-lg"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">rule</span>
              Annex Automation
            </button>
          </div>
        </div>
      )}

      {mode === "editor" || guestMode ? (
        <EditorWorkspace ed={ed} onDownload={handleDownload} guest={guestMode} />
      ) : mode === "bulk" ? (
        <BulkWorkspace
          file={ed.file}
          spans={ed.spans}
          data={ed.fileData}
          pages={ed.pages}
          cloudProjectId={cloudProjectId}
          onCloudSaved={setCloudProjectId}
        />
      ) : (
        <AnnexWorkspace
          file={ed.file}
          spans={ed.spans}
          data={ed.fileData}
          pages={ed.pages}
        />
      )}
    </div>
  );
}

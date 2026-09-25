import { useEffect, useMemo, useRef, useState } from "react";
import PdfCanvas from "./PdfCanvas.jsx";
import CanvasToolbar from "./CanvasToolbar.jsx";
import FontPanel from "./FontPanel.jsx";
import SignaturePanel from "./SignaturePanel.jsx";
import SplitField from "./SplitField.jsx";
import Icon from "./Icon.jsx";

export default function EditorWorkspace({ ed, onDownload, guest = false }) {
  const inputRef = useRef(null);
  const canvasBoxRef = useRef(null);
  const [boxW, setBoxW] = useState(0);
  const [boxH, setBoxH] = useState(0);
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
    const update = () => {
      setBoxW(el.clientWidth);
      setBoxH(el.clientHeight);
    };
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

  // 100% = the WHOLE page fits the board (width and height), so the design
  // around the document stays visible instead of one zoomed-in white slab.
  const pg = pages && pages[pageIndex];
  const ratio = pg && pg.width && pg.height ? pg.width / pg.height : 0.707;
  const fitW = Math.min((boxW || 640) - 48, ((boxH || 700) - 104) * ratio);
  const pdfWidth = Math.max(260, Math.round(fitW * zoom));

  // ---- Document-first editing: the box next to the selected text ----
  const [allOpen, setAllOpen] = useState(false); // the full field list, collapsed by default
  const editable = (x) => !x.invisible && !x.rtl;

  const changed = spans.filter((x) => edits[x.id] !== undefined && edits[x.id] !== x.text);

  // Focus the inline box as soon as a piece of text is picked.
  useEffect(() => {
    if (selectedId == null) return;
    const t = setTimeout(() => document.getElementById(`pop-${selectedId}`)?.focus(), 30);
    return () => clearTimeout(t);
  }, [selectedId, pageIndex]);

  function goField(dir) {
    const list = pageSpans.filter(editable);
    if (!list.length) return;
    const at = list.findIndex((x) => x.id === selectedId);
    const next = list[(at + dir + list.length) % list.length];
    setSelectedId(next.id);
  }

  // What changed, character-accurate: shared start/end kept, the middle marked.
  function diffParts(a, b) {
    let i = 0;
    while (i < a.length && i < b.length && a[i] === b[i]) i++;
    let ja = a.length, jb = b.length;
    while (ja > i && jb > i && a[ja - 1] === b[jb - 1]) { ja--; jb--; }
    return { pre: a.slice(0, i), del: a.slice(i, ja), ins: b.slice(i, jb), post: a.slice(ja) };
  }

  function renderPopover(sp, below = true, caretLeft = 28) {
    const now = edits[sp.id] ?? sp.text;
    const isEdited = now !== sp.text;
    const d = diffParts(sp.text, now);
    const delta = now.length - sp.text.length;
    return (
      <div
        className="pop-scope relative rounded-[22px] bg-surface text-on-surface p-4 animate-pop shadow-[0_24px_60px_-18px_rgb(var(--c-shadow)/0.55),0_0_0_1.5px_rgb(var(--c-on-surface)/0.9)]"
        onKeyDown={(e) => {
          if (e.key === "Escape") setSelectedId(null);
          else if (e.key === "Tab") {
            e.preventDefault();
            goField(e.shiftKey ? -1 : 1);
          } else if (e.key === "Enter") {
            e.preventDefault();
            setSelectedId(null);
          }
        }}
      >
        {/* pointer to the text being edited */}
        {caretLeft != null && <span
          aria-hidden="true"
          style={{ left: caretLeft - 7 }}
          className={`absolute w-3.5 h-3.5 rotate-45 bg-surface ${
            below ? "-top-[8px] shadow-[-1.5px_-1.5px_0_0_rgb(var(--c-on-surface)/0.9)]" : "-bottom-[8px] shadow-[1.5px_1.5px_0_0_rgb(var(--c-on-surface)/0.9)]"
          }`}
        />}
        <div className="flex items-center justify-between pb-2.5">
          <span className="font-hand uppercase tracking-[0.08em] text-[17px] leading-none text-secondary-container">Editing</span>
          <span className="flex items-center gap-1.5">
            {isEdited && (
              <button
                onClick={() => setFieldValue(sp.id, sp.text)}
                className="rounded-full px-3 py-1 text-[13px] bg-surface-container text-on-surface hover:bg-surface-container-high transition-colors"
              >
                Undo
              </button>
            )}
            <button
              onClick={() => setSelectedId(null)}
              aria-label="Close"
              className="w-7 h-7 rounded-full grid place-items-center text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors"
            >
              <Icon name="close" size={15} />
            </button>
          </span>
        </div>

        <SplitField
          inputId={`pop-${sp.id}`}
          span={sp}
          label="Value"
          fullValue={now}
          selected
          onFocus={() => setSelectedId(sp.id)}
          onChange={(val) => setFieldValue(sp.id, val)}
          override={splits[sp.id]}
          editing={splitEditingId === sp.id}
          onEnterSplit={() => setSplitEditingId(sp.id)}
          onSetSplit={(i) => {
            setSplits((m) => ({ ...m, [sp.id]: i }));
            setSplitEditingId(null);
          }}
          onWholeField={() => {
            setSplits((m) => ({ ...m, [sp.id]: -1 }));
            setSplitEditingId(null);
          }}
          onCloseSplit={() => setSplitEditingId(null)}
        />

        {/* Before / after, updating as you type */}
        <div className="mt-3 rounded-2xl bg-surface-container p-3 space-y-2 text-[14px] leading-5">
          <div className="flex items-baseline gap-3">
            <span className="w-11 shrink-0 font-hand uppercase tracking-[0.06em] text-[14px] text-on-surface-variant">Before</span>
            <span className="min-w-0 break-words">
              {d.pre}
              {d.del && (
                <mark className="rounded px-0.5 bg-[rgb(var(--c-tint-coral))] text-on-surface line-through decoration-[1.5px]">{d.del}</mark>
              )}
              {d.post}
            </span>
          </div>
          <div className="flex items-baseline gap-3">
            <span className="w-11 shrink-0 font-hand uppercase tracking-[0.06em] text-[14px] text-on-surface-variant">After</span>
            <span className="min-w-0 break-words font-medium">
              {isEdited ? (
                <>
                  {d.pre}
                  {d.ins && <mark className="rounded px-0.5 bg-[rgb(var(--c-tint-mint))] text-on-surface">{d.ins}</mark>}
                  {d.post}
                </>
              ) : (
                <span className="text-on-surface-variant font-normal">Type to change it</span>
              )}
            </span>
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="text-[12px] leading-4 text-on-surface-variant">
            {isEdited ? `${delta === 0 ? "Same length" : `${delta > 0 ? "+" : ""}${delta} character${Math.abs(delta) === 1 ? "" : "s"}`} · ` : ""}
            Tab next · Esc close
          </p>
          <button
            onClick={() => setSelectedId(null)}
            className="shrink-0 rounded-full bg-secondary-container px-5 py-2 text-[14px] text-white hover:bg-secondary-container-hover transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    );
  }

  const PILL = "inline-flex items-center justify-center gap-2 rounded-full text-[15px] leading-5 px-5 py-[11px] cursor-pointer select-none transition-[transform,background,box-shadow,opacity] duration-200 hover:-translate-y-0.5 disabled:opacity-40 disabled:hover:translate-y-0";

  return (
    // Stacks below lg. The app shell is a fixed-height, non-scrolling column
    // (h-screen + overflow-hidden), so when these panes stack THIS is the
    // element that has to scroll — side by side it must not, or the panes
    // lose their own internal scrolling.
    <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-3 px-3 pb-3 sm:px-4 sm:pb-4 overflow-y-auto lg:overflow-hidden max-w-[1500px] w-full mx-auto animate-rise">
      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])}
      />

      {/* Left pane: the document, on a tinted board */}
      <div className="flex-none h-[58vh] min-h-[320px] lg:flex-1 lg:min-w-0 lg:h-auto lg:min-h-0 rounded-[28px] bg-[rgb(var(--c-tint-sand))] flex flex-col overflow-hidden relative">
        {/* Font report — surfaced after a preview/download so substitutions are visible */}
        {problemFonts.length > 0 && !hideFontNote && (
          <div className="absolute top-4 left-1/2 z-20 flex max-w-[92%] -translate-x-1/2 items-start gap-2.5 rounded-2xl bg-[rgb(var(--c-tint-yellow))] px-4 py-2.5 text-[14px] leading-5 text-on-surface shadow-panel">
            <Icon name="warning" size={18} className="mt-0.5" />
            <span>
              Fonts replaced with lookalikes:{" "}
              <b className="font-semibold">
                {[...new Set(problemFonts.map((f) => f.font))].join(", ")}
              </b>
              . Upload the real files in the Fonts panel for an exact match.
            </span>
            <button onClick={() => setHideFontNote(true)} aria-label="Dismiss" className="shrink-0 mt-0.5 hover:opacity-70">
              <Icon name="close" size={16} />
            </button>
          </div>
        )}

        {/* Document canvas */}
        <div ref={canvasBoxRef} className="flex-1 overflow-auto px-6 pt-6 pb-20 flex justify-center">
          {file && fileData ? (
            <div className="paper-shadow rounded-sm h-fit">
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
                selectedPopover={renderPopover}
                liveEdits={!previewData}
              />
            </div>
          ) : (
            <button
              onClick={() => inputRef.current?.click()}
              className="bg-white w-full max-w-[640px] min-h-[400px] paper-shadow rounded-sm text-[#2d2323] flex flex-col items-center justify-center gap-3 mt-2 mb-6 hover:opacity-90 transition-opacity"
            >
              <Icon name={busy ? "spinner" : "upload"} size={40} spin={busy} className="text-[#2d2323]/40" />
              <p className="font-display-md font-black text-[22px] tracking-[-0.3px]">
                {busy ? "Reading PDF…" : "Upload a PDF to start editing"}
              </p>
              <p className="text-[15px] text-[#736b6b]">Drag &amp; drop or click to browse</p>
            </button>
          )}
        </div>

        {error && (
          <div className="absolute bottom-20 left-1/2 -translate-x-1/2 bg-error-container text-on-error-container rounded-2xl px-4 py-2.5 text-[14px] shadow-panel z-20">
            {error}
          </div>
        )}

        {/* Page and zoom dock */}
        <CanvasToolbar
          pageIndex={pageIndex}
          pageCount={pageCount}
          setPageIndex={setPageIndex}
          zoom={zoom}
          setZoom={setZoom}
        />
      </div>

      {/* Right pane: the ink sidebar, same frame as the landing product shots */}
      <div className="ink-scope flex-none lg:w-[360px] min-h-[45vh] lg:min-h-0 rounded-[28px] bg-[rgb(var(--c-sidebar))] text-on-surface flex flex-col overflow-hidden relative">
        {/* Header: Text / Sign switch + Find & Replace */}
        <div className="p-4 pb-3 sticky top-0 z-10 bg-[rgb(var(--c-sidebar))]">
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-black/25 p-1 rounded-full flex items-center gap-1">
              {[["fields", "text", "Text"], ["sign", "sign", "Sign"]].map(([k, icon, lbl]) => (
                <button
                  key={k}
                  onClick={() => setPanel(k)}
                  className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-full text-[15px] transition-all ${
                    panel === k ? "bg-primary text-on-primary" : "text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  <Icon name={icon} size={17} />
                  {lbl}
                </button>
              ))}
            </div>
            <button
              onClick={() => setFindOpen((v) => !v)}
              title="Find & replace"
              aria-label="Find and replace"
              className={`shrink-0 w-11 h-11 rounded-full inline-flex items-center justify-center transition-colors ${
                findOpen ? "bg-secondary-container text-white" : "bg-black/25 text-on-surface hover:bg-[rgb(var(--c-field))]"
              }`}
            >
              <Icon name="search" size={18} />
            </button>
          </div>

          {findOpen && (
            <div className="mt-3 space-y-2 animate-drop">
              <div className="flex items-center gap-2">
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
                  className="flex-1 min-w-0 bg-[rgb(var(--c-field))] rounded-2xl py-2.5 px-4 text-[15px] text-on-surface placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-2 focus:ring-secondary-container"
                />
                <button
                  onClick={() => setCaseSensitive((v) => !v)}
                  title={caseSensitive ? "Case sensitive" : "Case insensitive"}
                  aria-label="Toggle case sensitivity"
                  className={`shrink-0 h-10 w-10 rounded-full text-[13px] font-semibold transition-colors ${
                    caseSensitive ? "bg-secondary-container text-white" : "bg-[rgb(var(--c-field))] text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  Aa
                </button>
              </div>

              {findQuery.trim() !== "" && (
                <>
                  <div className="flex items-center justify-between text-[13px] text-on-surface-variant px-1">
                    <span>
                      {matches.length
                        ? `Match ${matchIdx + 1} of ${matches.length} field${matches.length === 1 ? "" : "s"}`
                        : "No matching fields"}
                    </span>
                    <span className="flex items-center gap-1">
                      <button
                        disabled={!matches.length}
                        onClick={() => goToMatch(matchIdx - 1)}
                        title="Previous match (Shift+Enter)"
                        aria-label="Previous match"
                        className="w-7 h-7 rounded-full grid place-items-center hover:bg-[rgb(var(--c-field))] disabled:opacity-30 transition-colors"
                      >
                        <Icon name="chevup" size={16} />
                      </button>
                      <button
                        disabled={!matches.length}
                        onClick={() => goToMatch(matchIdx + 1)}
                        title="Next match (Enter)"
                        aria-label="Next match"
                        className="w-7 h-7 rounded-full grid place-items-center hover:bg-[rgb(var(--c-field))] disabled:opacity-30 transition-colors"
                      >
                        <Icon name="chevdown" size={16} />
                      </button>
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      value={replaceWith}
                      onChange={(e) => setReplaceWith(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setFindOpen(false);
                      }}
                      placeholder="Replace with…"
                      className="flex-1 min-w-0 bg-[rgb(var(--c-field))] rounded-2xl py-2.5 px-4 text-[15px] text-on-surface placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-2 focus:ring-secondary-container"
                    />
                    <button
                      onClick={replaceCurrent}
                      disabled={!matches.length}
                      title="Replace in the current field"
                      className="shrink-0 rounded-full bg-[rgb(var(--c-field))] px-4 py-2.5 text-[14px] text-on-surface transition-colors hover:bg-[rgb(var(--c-field-hover))] disabled:opacity-40"
                    >
                      Replace
                    </button>
                    <button
                      onClick={replaceAllMatches}
                      disabled={!matches.length}
                      title="Replace in every matching field"
                      className="shrink-0 rounded-full bg-secondary-container px-4 py-2.5 text-[14px] text-white transition-colors hover:bg-secondary-container-hover disabled:opacity-40"
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
        <div className="flex-1 overflow-y-auto px-4 pb-4 pt-1 space-y-4">
          {/* How it works: only until something is selected or changed */}
          {file && selectedId == null && changed.length === 0 && (
            <div className="rounded-2xl bg-black/20 p-4">
              <p className="font-display-md font-black text-[19px] leading-tight tracking-[-0.3px]">Click any text on the page.</p>
              <p className="mt-1.5 text-[14px] leading-[21px] text-on-surface-variant">
                An edit box opens right where it is. Type the new value; Tab jumps to the next field.
              </p>
            </div>
          )}

          <button
            onClick={() => setPlacement(placement?.kind === "text" ? null : { kind: "text" })}
            disabled={!file}
            className={`w-full flex items-center justify-center gap-2 py-3 rounded-2xl border-2 border-dashed text-[15px] transition-colors disabled:opacity-40 ${
              placement?.kind === "text"
                ? "border-secondary-container bg-secondary-container/15 text-on-surface"
                : "border-outline-variant text-on-surface hover:border-on-surface/50"
            }`}
          >
            <Icon name={placement?.kind === "text" ? "target" : "plus"} size={18} />
            {placement?.kind === "text" ? "Click on the document…" : "Add text"}
          </button>
          {file && !guest && <FontPanel file={file} onChanged={() => nEdits > 0 && preview()} />}

          {/* Changes */}
          {changed.length > 0 && (
            <div>
              <div className="flex items-center justify-between px-1 pb-2">
                <h3 className="font-display-md font-black text-[16px] tracking-[-0.2px]">Changes</h3>
                <span className="rounded-full bg-black/25 px-2.5 py-0.5 text-[12px] text-on-surface-variant">{changed.length}</span>
              </div>
              <div className="space-y-2">
                {changed.map((x) => (
                  <div key={x.id} className={`group flex items-stretch rounded-2xl bg-[rgb(var(--c-field))] overflow-hidden ${selectedId === x.id ? "ring-2 ring-secondary-container" : ""}`}>
                    <button
                      onClick={() => {
                        setPageIndex(x.page);
                        setSelectedId(x.id);
                      }}
                      className="flex-1 min-w-0 text-left px-4 py-2.5 hover:bg-[rgb(var(--c-field-hover))] transition-colors"
                    >
                      <span className="block truncate text-[12px] text-on-surface-variant line-through">{x.text}</span>
                      <span className="block truncate text-[15px]">{edits[x.id]}</span>
                    </button>
                    <button
                      onClick={() => setFieldValue(x.id, x.text)}
                      title="Undo this change"
                      aria-label="Undo this change"
                      className="shrink-0 w-11 grid place-items-center text-on-surface-variant hover:text-on-surface hover:bg-[rgb(var(--c-field-hover))] transition-colors"
                    >
                      <Icon name="reset" size={16} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* All fields: the classic list, for scans, keyboard users and bulk edits */}
          {file && spans.length > 0 && (
            <div>
              <button
                onClick={() => setAllOpen((v) => !v)}
                aria-expanded={allOpen}
                className="w-full flex items-center justify-between rounded-2xl bg-black/20 px-4 py-3 text-[15px] hover:bg-black/30 transition-colors"
              >
                <span>All fields on this page</span>
                <span className="flex items-center gap-2 text-on-surface-variant">
                  <span className="text-[13px]">{pageSpans.length}</span>
                  <Icon name={allOpen ? "chevup" : "chevdown"} size={17} />
                </span>
              </button>
              {allOpen && (
                <div className="mt-3 space-y-3.5 animate-drop">
                  {pageSpans.length === 0 && (
                    <p className="text-[14px] leading-6 text-on-surface-variant">
                      No editable text on this page. Use the page arrows below the document to look at the others.
                    </p>
                  )}
                  {pageSpans.map((sp) => (
                    <SplitField
                      key={sp.id}
                      span={sp}
                      label={fieldLabel(sp)}
                      fullValue={edits[sp.id] ?? sp.text}
                      selected={selectedId === sp.id}
                      onFocus={() => setSelectedId(sp.id)}
                      onChange={(val) => setFieldValue(sp.id, val)}
                      override={splits[sp.id]}
                      editing={splitEditingId === sp.id}
                      onEnterSplit={() => setSplitEditingId(sp.id)}
                      onSetSplit={(i) => {
                        setSplits((m) => ({ ...m, [sp.id]: i }));
                        setSplitEditingId(null);
                      }}
                      onWholeField={() => {
                        setSplits((m) => ({ ...m, [sp.id]: -1 }));
                        setSplitEditingId(null);
                      }}
                      onCloseSplit={() => setSplitEditingId(null)}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* A scan has no fields at all: say so instead of an empty list */}
          {file && spans.length === 0 && (
            <p className="text-[14px] leading-6 text-on-surface-variant">
              There's no editable text on this document. It looks like a scan or an image. You can still add text and a
              signature on top of it.
            </p>
          )}
          {!file && <p className="text-[14px] leading-6 text-on-surface-variant">Upload a PDF to start editing.</p>}
        </div>
        )}

        {/* Footer controls */}
        <div className="p-4 pt-3 flex flex-col gap-2.5 sticky bottom-0 bg-[rgb(var(--c-sidebar))]">
          <div
            className={`rounded-full px-4 py-2 text-[14px] flex items-center gap-2 ${
              nEdits > 0 ? "bg-[#f7e7a6] text-[#2d2323] font-medium" : "bg-black/20 text-on-surface-variant"
            }`}
          >
            <Icon name="pen" size={16} />
            {nEdits === 0 ? "No changes yet" : `${nEdits} ${nEdits === 1 ? "field" : "fields"} modified`}
          </div>
          <div className="flex gap-2.5">
            <button
              onClick={preview}
              disabled={nEdits === 0 || busy}
              className={`${PILL} flex-1 bg-secondary-container text-white hover:bg-secondary-container-hover hover:shadow-[0_8px_22px_rgba(79,117,254,0.35)]`}
            >
              <Icon name={busy ? "spinner" : "eye"} size={18} spin={busy} />
              {busy ? "Working…" : "Preview"}
            </button>
            <button onClick={resetAll} className={`${PILL} flex-1 bg-black/25 text-on-surface hover:bg-[rgb(var(--c-field))]`}>
              <Icon name="reset" size={18} />
              Reset all
            </button>
          </div>
          <button
            onClick={doDownload}
            disabled={!hasChanges || busy}
            className={`${PILL} w-full bg-primary text-on-primary`}
          >
            <Icon name="download" size={18} />
            Download edited PDF
          </button>
        </div>
      </div>
    </div>
  );
}

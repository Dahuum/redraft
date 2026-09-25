import { useEffect, useRef, useState } from "react";
import { checkFonts, uploadFont } from "../api.js";
import Icon from "./Icon.jsx";

// status → chip styling + plain-language label
const CHIP = {
  match: "bg-[rgb(var(--c-tint-mint))] text-on-surface",
  builtin: "bg-[rgb(var(--c-tint-mint))] text-on-surface",
  substitute: "bg-[rgb(var(--c-tint-yellow))] text-on-surface",
  fallback: "bg-error-container text-on-error-container",
};
const LABEL = {
  match: "exact",
  builtin: "built-in",
  substitute: "lookalike",
  fallback: "missing",
};

/**
 * Shows the font health of the loaded PDF and lets the user upload the real
 * .ttf/.otf for any font that can't be matched exactly. Installing a font is
 * server-side + shared, so it fixes the editor AND bulk for every later edit.
 */
export default function FontPanel({ file, onChanged }) {
  const [fonts, setFonts] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(null); // raw_font currently uploading
  const [open, setOpen] = useState(false); // fonts list collapsed by default
  const inputs = useRef({});

  useEffect(() => {
    if (!file) {
      setFonts(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    checkFonts(file)
      .then((r) => !cancelled && setFonts(r.fonts))
      .catch((e) => !cancelled && setErr(e.message || "Couldn't check fonts."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [file]);

  async function onPick(rawFont, f) {
    if (!f) return;
    setBusy(rawFont);
    setErr(null);
    try {
      const r = await uploadFont(rawFont, f);
      setFonts((list) => list.map((x) => (x.raw_font === rawFont ? r.font : x)));
      onChanged && onChanged();
    } catch (e) {
      setErr(e.message || "Upload failed.");
    } finally {
      setBusy(null);
    }
  }

  if (!file) return null;

  const attention = (fonts || []).filter(
    (f) => f.status === "fallback" || f.status === "substitute"
  );
  const okCount = (fonts || []).length - attention.length;

  return (
    <div className="rounded-2xl bg-black/20 p-3.5 animate-rise">
      <button
        type="button"
        onClick={() => attention.length && setOpen((v) => !v)}
        className={`w-full flex items-center justify-between gap-2 text-left ${
          attention.length ? "cursor-pointer" : "cursor-default"
        }`}
      >
        <span className="text-label-md text-on-surface flex items-center gap-1.5">
          <Icon name="text" size={16} />
          Fonts
        </span>
        <span className="flex items-center gap-1.5">
          {loading ? (
            <span className="text-caption text-on-surface-variant">checking…</span>
          ) : fonts ? (
            <span className="text-caption text-on-surface-variant">
              {okCount === 0 && attention.length === 0 ? "none found" : `${okCount} matched`}
              {attention.length ? ` · ${attention.length} need the real file` : ""}
            </span>
          ) : null}
          {attention.length > 0 && (
            <Icon name={open ? "chevup" : "chevdown"} size={18} className="text-on-surface-variant" />
          )}
        </span>
      </button>

      {err && <p className="mt-2 text-caption text-error">{err}</p>}

      {/* A scan has no fonts at all. Reporting "0 matched · All fonts matched"
          with a green tick told the user everything was fine about a document
          they cannot edit a word of. Nothing matched because there was nothing
          to match. */}
      {!loading && fonts && okCount === 0 && attention.length === 0 && (
        <p className="mt-1 text-caption text-on-surface-variant">
          This document has no text fonts — it's most likely a scan.
        </p>
      )}

      {!loading && fonts && okCount > 0 && attention.length === 0 && (
        <p className="mt-1 text-[13px] text-on-surface-variant flex items-center gap-1.5">
          <Icon name="check" size={14} />
          All fonts matched.
        </p>
      )}

      {open && attention.length > 0 && (
        <div className="mt-2 animate-drop">
          {/* Collapsible + capped height so a font-heavy PDF (e.g. LaTeX) never
              pushes the rest of the panel off-screen. */}
          <div className="space-y-2 max-h-48 overflow-y-auto pr-1 -mr-1">
            {attention.map((f) => (
              <div key={f.raw_font} className="flex items-center gap-2">
                <span
                  className="flex-1 min-w-0 text-body-md text-on-surface truncate"
                  title={f.source}
                >
                  {f.font}
                </span>
                <span
                  className={`shrink-0 px-2.5 py-1 rounded-full text-[11px] font-semibold ${CHIP[f.status]}`}
                >
                  {LABEL[f.status]}
                </span>
                <button
                  onClick={() => inputs.current[f.raw_font]?.click()}
                  disabled={busy === f.raw_font}
                  className="shrink-0 text-[13px] px-3 py-1.5 rounded-full bg-[rgb(var(--c-field))] text-on-surface hover:bg-[rgb(var(--c-field-hover))] transition-colors disabled:opacity-50"
                >
                  {busy === f.raw_font ? "uploading…" : "Upload .ttf"}
                </button>
                <input
                  ref={(el) => (inputs.current[f.raw_font] = el)}
                  type="file"
                  accept=".ttf,.otf"
                  className="hidden"
                  onChange={(e) => onPick(f.raw_font, e.target.files?.[0])}
                />
              </div>
            ))}
          </div>
          <p className="mt-2 text-caption text-on-surface-variant">
            Upload the real font for a pixel-perfect match — <b>missing</b> = some characters
            won't render, <b>lookalike</b> = a close substitute. It applies to bulk too.
          </p>
        </div>
      )}
    </div>
  );
}

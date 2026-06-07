import { useEffect, useRef, useState } from "react";
import { checkFonts, uploadFont } from "../api.js";

// status → chip styling + plain-language label
const CHIP = {
  match: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  builtin: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  substitute: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  fallback: "bg-error/10 text-error border-error/20",
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
    <div className="rounded-lg border border-outline-variant/30 bg-surface-container-low p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-label-md text-on-surface flex items-center gap-1.5">
          <span className="material-symbols-outlined text-[16px]">font_download</span>
          Fonts
        </span>
        {loading ? (
          <span className="text-caption text-on-surface-variant">checking…</span>
        ) : fonts ? (
          <span className="text-caption text-on-surface-variant">
            {okCount} matched
            {attention.length ? ` · ${attention.length} need the real file` : ""}
          </span>
        ) : null}
      </div>

      {err && <p className="mt-2 text-caption text-error">{err}</p>}

      {!loading && fonts && attention.length === 0 && (
        <p className="mt-1 text-caption text-emerald-400/90 flex items-center gap-1">
          <span className="material-symbols-outlined text-[14px]">check_circle</span>
          All fonts matched.
        </p>
      )}

      {attention.length > 0 && (
        <div className="mt-2 space-y-2">
          {attention.map((f) => (
            <div key={f.raw_font} className="flex items-center gap-2">
              <span
                className="flex-1 min-w-0 text-body-md text-on-surface truncate"
                title={f.source}
              >
                {f.font}
              </span>
              <span
                className={`shrink-0 px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wide ${CHIP[f.status]}`}
              >
                {LABEL[f.status]}
              </span>
              <button
                onClick={() => inputs.current[f.raw_font]?.click()}
                disabled={busy === f.raw_font}
                className="shrink-0 text-caption px-2 py-1 rounded border border-outline-variant/50 text-on-surface hover:border-accent-cyan/50 hover:text-accent-cyan transition-colors disabled:opacity-50"
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
          <p className="text-caption text-on-surface-variant">
            Upload the real font for a pixel-perfect match — <b>missing</b> = some characters
            won't render, <b>lookalike</b> = a close substitute. It applies to bulk too.
          </p>
        </div>
      )}
    </div>
  );
}

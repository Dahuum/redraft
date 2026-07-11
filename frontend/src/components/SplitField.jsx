import { useState } from "react";
import { effectiveSplit } from "../lib/split.js";

/**
 * One editable text field in the PDF editor, with optional label/value split.
 * When split, the label (left of the split) is locked and only the value is
 * editable; the caller still stores/replaces the FULL text (label + value).
 * The ⋮ button opens click-to-split: click a character to set where the value
 * begins, "Whole field" to disable, "Done" to close.
 */
export default function SplitField({
  span,
  label,
  fullValue,
  selected,
  onFocus,
  onChange,
  override, // undefined | -1 (whole) | index
  editing,
  onEnterSplit,
  onSetSplit,
  onWholeField,
  onCloseSplit,
}) {
  const [hover, setHover] = useState(null);
  const split = effectiveSplit(override, span.text);
  const labelPart = split != null ? span.text.slice(0, split) : "";
  const valuePart = split != null ? fullValue.slice(split) : fullValue;

  // Reflect the span's real weight/style so editing is WYSIWYG — a bold field
  // looks bold while you type it. Uses the font flags (bit 16 = bold, bit 2 =
  // italic) with the font name as a fallback signal.
  const fnt = (span.font || "").toLowerCase();
  const fl = span.flags || 0;
  const isBold = (fl & 16) !== 0 || /bold|black|heavy|semibold|extrabold/.test(fnt);
  const isItalic = (fl & 2) !== 0 || /italic|oblique/.test(fnt);
  const valueStyle = {
    fontWeight: isBold ? 700 : 400,
    fontStyle: isItalic ? "italic" : "normal",
  };

  if (editing) {
    const chars = span.text.split("");
    const mark = hover != null ? hover : split ?? chars.length;
    return (
      <div className="space-y-1.5 animate-drop">
        <div className="flex items-center justify-between text-caption">
          <span className="text-on-surface-variant flex items-center gap-1">
            <span className="material-symbols-outlined text-[14px] text-accent-cyan">content_cut</span>
            Click where the value begins
          </span>
          <div className="flex items-center gap-3">
            <button onClick={onWholeField} className="text-on-surface-variant hover:text-on-surface transition-colors">
              Whole field
            </button>
            <button onClick={onCloseSplit} className="text-secondary-container font-semibold hover:underline">
              Done
            </button>
          </div>
        </div>
        <div
          onMouseLeave={() => setHover(null)}
          className="rounded-lg border border-secondary-container/50 bg-surface-container-lowest px-2 py-2 text-sm leading-relaxed flex flex-wrap select-none"
        >
          {chars.map((c, i) => (
            <span
              key={i}
              onMouseEnter={() => setHover(i)}
              onClick={() => onSetSplit(i)}
              className={`whitespace-pre cursor-pointer ${i === mark ? "shadow-[inset_2px_0_0_0_#00f5ff]" : ""} ${
                i < mark ? "text-on-surface-variant/45" : "text-accent-cyan"
              }`}
            >
              {c === " " ? " " : c}
            </span>
          ))}
          <span
            onMouseEnter={() => setHover(chars.length)}
            onClick={() => onSetSplit(chars.length)}
            className={`w-3 cursor-pointer ${mark === chars.length ? "shadow-[inset_2px_0_0_0_#00f5ff]" : ""}`}
          >
            &nbsp;
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 group">
      <div className="flex justify-between items-center text-label-md text-[13px] text-on-surface-variant group-focus-within:text-secondary-container transition-colors">
        <label htmlFor={`field-${span.id}`} className="truncate">
          {label}
        </label>
        <button
          onClick={onEnterSplit}
          title="Split — edit only part of this field"
          className="opacity-0 group-hover:opacity-100 transition-opacity text-on-surface-variant hover:text-secondary-container shrink-0"
        >
          <span className="material-symbols-outlined text-[16px]">more_horiz</span>
        </button>
      </div>
      <div
        className={`flex items-stretch rounded-lg border shadow-sm overflow-hidden transition-all ${
          selected
            ? "border-secondary-container ring-1 ring-secondary-container"
            : "border-outline-variant/50 hover:bg-surface-container-high"
        }`}
      >
        {split != null && (
          <span
            title={labelPart}
            style={valueStyle}
            className="shrink-0 max-w-[46%] truncate px-2 py-2 text-sm bg-surface-container-high text-on-surface-variant/70 border-r border-outline-variant/40 select-none flex items-center"
          >
            {labelPart}
          </span>
        )}
        <input
          id={`field-${span.id}`}
          type="text"
          value={valuePart}
          onFocus={onFocus}
          onChange={(e) => onChange(split != null ? labelPart + e.target.value : e.target.value)}
          style={valueStyle}
          className="flex-1 min-w-0 bg-surface-container-lowest py-2 px-3 text-sm text-on-surface focus:outline-none"
        />
      </div>
    </div>
  );
}

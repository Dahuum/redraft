import { useState } from "react";
import { effectiveSplit } from "../lib/split.js";
import Icon from "./Icon.jsx";

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
  inputId, // defaults to field-<id>; the inline editor uses its own so ids never collide
}) {
  const fid = inputId || `field-${span.id}`;
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
            <Icon name="scissors" size={14} className="text-accent-cyan" />
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
          className="rounded-2xl bg-[rgb(var(--c-field))] ring-2 ring-secondary-container/60 px-4 py-3 text-[15px] leading-relaxed flex flex-wrap select-none"
        >
          {chars.map((c, i) => (
            <span
              key={i}
              onMouseEnter={() => setHover(i)}
              onClick={() => onSetSplit(i)}
              className={`whitespace-pre cursor-pointer ${i === mark ? "shadow-[inset_2px_0_0_0_#4f75fe]" : ""} ${
                i < mark ? "text-on-surface-variant/45" : "text-accent-cyan"
              }`}
            >
              {c === " " ? " " : c}
            </span>
          ))}
          <span
            onMouseEnter={() => setHover(chars.length)}
            onClick={() => onSetSplit(chars.length)}
            className={`w-3 cursor-pointer ${mark === chars.length ? "shadow-[inset_2px_0_0_0_#4f75fe]" : ""}`}
          >
            &nbsp;
          </span>
        </div>
      </div>
    );
  }

  // Right-to-left text arrives from extraction in DRAWING order, which for
  // Arabic or Hebrew is the reverse of reading order — the editor would be
  // showing the user their own document backwards. Placing it again would
  // need shaping the engine does not do, so the field is shown as read-only
  // with the reason rather than as an input that cannot work.
  // Invisible text is the OCR layer a scanner lays over a PICTURE of the page.
  // Editing it would change nothing anyone can see — only what search and copy
  // return — so it is shown, like right-to-left text, as read-only with why.
  if (span.invisible) {
    return (
      <div className="space-y-1.5">
        <div className="flex justify-between items-center text-label-md text-[13px] text-on-surface-variant">
          <span className="truncate">{label}</span>
          <span className="shrink-0" title="Scanned page"><Icon name="scan" size={16} /></span>
        </div>
        <div className="rounded-2xl bg-black/20 px-4 py-3">
          <p className="text-sm text-on-surface-variant/70 truncate" title={span.text}>
            {span.text}
          </p>
          <p className="mt-1 text-caption text-on-surface-variant">
            This page is a scanned image. This text is the invisible layer that makes
            it searchable, so changing it wouldn't change what the page shows. You can
            add new text on top of it instead.
          </p>
        </div>
      </div>
    );
  }

  if (span.rtl) {
    return (
      <div className="space-y-1.5">
        <div className="flex justify-between items-center text-label-md text-[13px] text-on-surface-variant">
          <span className="truncate">{label}</span>
          <span className="shrink-0" title="Right-to-left text"><Icon name="lock" size={16} /></span>
        </div>
        <div className="rounded-2xl bg-black/20 px-4 py-3">
          <p dir="rtl" className="text-sm text-on-surface-variant/70 truncate" title={span.text}>
            {span.text}
          </p>
          <p className="mt-1 text-caption text-on-surface-variant">
            Right-to-left text can't be edited yet — Arabic and Hebrew need letter
            shaping Redraft doesn't do, and this document stores the text reversed.
            You can still add new text on top of it.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 group">
      <div className="flex justify-between items-center text-[13px] px-1 text-on-surface-variant group-focus-within:text-on-surface transition-colors">
        <label htmlFor={fid} className="truncate">
          {label}
        </label>
        <button
          onClick={onEnterSplit}
          title="Split — edit only part of this field"
          className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 transition-opacity text-on-surface-variant hover:text-secondary-container shrink-0"
        >
          <Icon name="dots" size={16} />
        </button>
      </div>
      <div
        className={`flex items-stretch rounded-2xl bg-[rgb(var(--c-field))] overflow-hidden transition-all ${
          selected ? "ring-2 ring-secondary-container" : "hover:bg-[rgb(var(--c-field-hover))]"
        }`}
      >
        {split != null && (
          <span
            title={labelPart}
            style={valueStyle}
            className="shrink-0 max-w-[46%] truncate pl-4 pr-2 py-2.5 text-[15px] text-on-surface-variant/70 select-none flex items-center"
          >
            {labelPart}
          </span>
        )}
        <input
          id={fid}
          type="text"
          value={valuePart}
          onFocus={onFocus}
          onChange={(e) => onChange(split != null ? labelPart + e.target.value : e.target.value)}
          style={valueStyle}
          className="flex-1 min-w-0 bg-transparent py-2.5 px-4 text-[15px] text-on-surface focus:outline-none"
        />
      </div>
    </div>
  );
}

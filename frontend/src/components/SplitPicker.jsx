import { useState } from "react";

/**
 * A click-to-split bar for the Bulk table. Shows a field's original text; click
 * a character to set where the editable value begins. Same interaction as the
 * editor's ⋮ split, adapted to a full-width bar above the table.
 */
export default function SplitPicker({ text, split, title, onSet, onWhole, onClose }) {
  const [hover, setHover] = useState(null);
  const chars = (text || "").split("");
  const mark = hover != null ? hover : split ?? chars.length;
  return (
    <div className="px-3 py-2.5 bg-surface-container-high border-b border-outline-variant/30 shrink-0 animate-drop">
      <div className="flex items-center justify-between text-caption mb-1.5 gap-2">
        <span className="text-on-surface-variant flex items-center gap-1 min-w-0">
          <span className="material-symbols-outlined text-[14px] text-accent-cyan">content_cut</span>
          <span className="truncate">Split “{title}” — click where the value begins</span>
        </span>
        <div className="flex items-center gap-3 shrink-0">
          <button onClick={onWhole} className="text-on-surface-variant hover:text-on-surface transition-colors">
            Whole field
          </button>
          <button onClick={onClose} className="text-secondary font-semibold hover:underline">
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
            onClick={() => onSet(i)}
            className={`whitespace-pre cursor-pointer ${i === mark ? "shadow-[inset_2px_0_0_0_#00f5ff]" : ""} ${
              i < mark ? "text-on-surface-variant/45" : "text-accent-cyan"
            }`}
          >
            {c === " " ? " " : c}
          </span>
        ))}
        <span
          onMouseEnter={() => setHover(chars.length)}
          onClick={() => onSet(chars.length)}
          className={`w-3 cursor-pointer ${mark === chars.length ? "shadow-[inset_2px_0_0_0_#00f5ff]" : ""}`}
        >
          &nbsp;
        </span>
      </div>
    </div>
  );
}

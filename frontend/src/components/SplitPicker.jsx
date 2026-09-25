import { useState } from "react";
import Icon from "./Icon.jsx";

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
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between text-caption mb-1.5 gap-1 sm:gap-2">
        <span className="text-on-surface-variant flex items-start sm:items-center gap-1 min-w-0">
          <Icon name="scissors" size={14} className="text-accent-cyan shrink-0" />
          {/* This sentence is the only instruction in the panel, so on a phone
              it wraps instead of truncating; from sm up it truncates as before. */}
          <span className="sm:truncate">Split “{title}” — click where the value begins</span>
        </span>
        <div className="flex items-center gap-3 shrink-0 self-end sm:self-auto">
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
            className={`whitespace-pre cursor-pointer ${i === mark ? "shadow-[inset_2px_0_0_0_#4f75fe]" : ""} ${
              i < mark ? "text-on-surface-variant/45" : "text-accent-cyan"
            }`}
          >
            {c === " " ? " " : c}
          </span>
        ))}
        <span
          onMouseEnter={() => setHover(chars.length)}
          onClick={() => onSet(chars.length)}
          className={`w-3 cursor-pointer ${mark === chars.length ? "shadow-[inset_2px_0_0_0_#4f75fe]" : ""}`}
        >
          &nbsp;
        </span>
      </div>
    </div>
  );
}

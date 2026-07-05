// Helpers for "Label: value" field splitting in the editor + bulk. A split is a
// character index (UTF-16, matching String.slice) where the editable VALUE
// begins; everything before it is the locked label.

// Auto-detect the split: after the first ": " (colon FOLLOWED BY A SPACE, so
// "10:30" or ratios aren't mistaken for labels). Returns the index, or null.
export function autoSplit(text) {
  const s = text || "";
  const i = s.indexOf(": ");
  if (i < 0) return null;
  if (!s.slice(0, i).trim()) return null; // need a real label on the left
  let j = i + 1;
  while (j < s.length && s[j] === " ") j++; // consume the gap into the label
  if (j >= s.length) return null; // nothing left to edit
  return j;
}

// Effective split from a per-field override:
//   undefined -> auto-detect; -1 -> whole field (no split); n>=0 -> manual.
export function effectiveSplit(override, text) {
  if (override === -1) return null;
  if (typeof override === "number" && override >= 0) return override;
  return autoSplit(text);
}

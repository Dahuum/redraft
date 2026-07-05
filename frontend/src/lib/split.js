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

// Aggressive label/value split used by AI auto-detect, where we already know the
// field is a labelled data field (e.g. "Nom Client : ACME", "ICE N° :001648",
// "Agence :NEARSHORE"). Handles a colon with or without a following space, so
// the label is kept and only the value gets replaced on generate. Returns the
// value-start index, or null when there's no label prefix (pure value / no colon).
export function smartSplit(text) {
  const s = text || "";
  // 1) Colon label: "Label: value" / "Label :value" / "Label:value".
  const i = s.indexOf(":");
  if (i > 0 && s[i + 1] !== "/" && /[A-Za-zÀ-ÿ]/.test(s.slice(0, i)) && i <= s.length * 0.75) {
    let j = i + 1;
    while (j < s.length && s[j] === " ") j++;
    if (j < s.length) return j;
  }
  // 2) "N°" label with no colon: "N°  W/2026/04/089", "Compte N° 007…".
  const m = s.match(/N[°º]\s+/);
  if (m) {
    const before = s.slice(0, m.index);
    if ((m.index === 0 || (/[A-Za-zÀ-ÿ]/.test(before) && before.length <= 12))) {
      const j = m.index + m[0].length;
      if (j < s.length) return j;
    }
  }
  return null;
}

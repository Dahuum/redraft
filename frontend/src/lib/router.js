// Tiny dependency-free router over the History API.
// Gives real URLs, working back/forward, and deep-linking — no library needed.
import { useSyncExternalStore } from "react";

const listeners = new Set();
const emit = () => listeners.forEach((l) => l());

// Make pushState/replaceState notify our subscribers (they don't fire popstate).
for (const m of ["pushState", "replaceState"]) {
  const orig = history[m];
  history[m] = function (...args) {
    const r = orig.apply(this, args);
    emit();
    return r;
  };
}
window.addEventListener("popstate", emit);

const subscribe = (cb) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const getPath = () => window.location.pathname;

export function navigate(to, { replace = false } = {}) {
  if (to === window.location.pathname) return;
  if (replace) history.replaceState({}, "", to);
  else history.pushState({}, "", to);
}

export function usePath() {
  return useSyncExternalStore(subscribe, getPath, getPath);
}

// "/editor/abc" → { view:"editor", mode:"editor", docId:"abc" }
export function parseRoute(path) {
  const parts = (path || "/").replace(/\/+$/, "").split("/").filter(Boolean);
  if (parts.length === 0) return { view: "home", mode: "editor", docId: null };
  if (parts[0] === "editor")
    return { view: "editor", mode: "editor", docId: parts[1] || null };
  if (parts[0] === "bulk")
    return { view: "editor", mode: "bulk", docId: parts[1] || null };
  if (parts[0] === "annex")
    return { view: "editor", mode: "annex", docId: parts[1] || null };
  return { view: "home", mode: "editor", docId: null };
}

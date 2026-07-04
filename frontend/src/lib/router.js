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

// The app is mounted under /app in production (the landing owns /). All routes
// are stored with the /app prefix in the URL but the router works in unprefixed
// paths ("/annex/x"), so add/strip the base at the boundary.
const BASE = "/app";
const stripBase = (p) => {
  if (p === BASE) return "/";
  if (p.startsWith(BASE + "/")) return p.slice(BASE.length) || "/";
  return p;
};
const withBase = (to) => (to.startsWith(BASE) ? to : BASE + (to === "/" ? "" : to));

const getPath = () => stripBase(window.location.pathname);

export function navigate(to, { replace = false } = {}) {
  const target = withBase(to);
  if (target === window.location.pathname) return;
  if (replace) history.replaceState({}, "", target);
  else history.pushState({}, "", target);
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

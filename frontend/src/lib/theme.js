// Light/dark theme store. Dark is the default; light is applied via
// data-theme="light" on <html> (which flips the CSS color variables).
import { useSyncExternalStore } from "react";

const KEY = "redraft-theme";
const listeners = new Set();

function read() {
  try {
    const t = localStorage.getItem(KEY);
    if (t === "light" || t === "dark") return t;
  } catch {
    /* localStorage unavailable */
  }
  return "dark";
}

let theme = read();

function apply(t) {
  const el = document.documentElement;
  if (t === "light") el.setAttribute("data-theme", "light");
  else el.removeAttribute("data-theme");
  // keep Tailwind's .dark class in sync (harmless; we theme via CSS vars)
  el.classList.toggle("dark", t !== "light");
}

apply(theme);

export function getTheme() {
  return theme;
}

export function setTheme(t) {
  if (t !== "light" && t !== "dark") return;
  theme = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* ignore */
  }
  const el = document.documentElement;
  el.classList.add("theme-switching");
  apply(t);
  window.setTimeout(() => el.classList.remove("theme-switching"), 320);
  listeners.forEach((l) => l());
}

export function toggleTheme() {
  setTheme(theme === "light" ? "dark" : "light");
}

function subscribe(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useTheme() {
  return useSyncExternalStore(subscribe, getTheme, getTheme);
}

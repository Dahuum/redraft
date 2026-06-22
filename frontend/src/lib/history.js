// Persistent document history backed by IndexedDB (stores the PDF bytes so a
// recent doc can be reopened) + a React store so the UI updates live.
import { useSyncExternalStore } from "react";

const DB_NAME = "redraft";
const STORE = "docs";
const VERSION = 1;
const MAX = 24;

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run(mode, fn) {
  return openDB().then(
    (db) =>
      new Promise((resolve, reject) => {
        const t = db.transaction(STORE, mode);
        const store = t.objectStore(STORE);
        const req = fn(store);
        t.oncomplete = () => resolve(req && req.result);
        t.onerror = () => reject(t.error);
        t.onabort = () => reject(t.error);
      })
  );
}

// ---- React store (metadata only; bytes fetched on demand) ----
let docs = [];
const listeners = new Set();
const emit = () => listeners.forEach((l) => l());
const subscribe = (cb) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const getSnapshot = () => docs;

const toMeta = (r) => ({
  id: r.id,
  name: r.name,
  status: r.status,
  addedAt: r.addedAt,
  pages: r.pages,
  fields: r.fields,
  thumb: r.thumb,
});

async function refresh() {
  try {
    const all = (await run("readonly", (s) => s.getAll())) || [];
    docs = all.map(toMeta).sort((a, b) => b.addedAt - a.addedAt);
    emit();
  } catch {
    /* IndexedDB unavailable — history just stays empty */
  }
}
refresh();

export async function addDoc({ name, bytes, pages = 0, fields = 0, status = "Draft", thumb = null }) {
  const id =
    (crypto.randomUUID && crypto.randomUUID()) || String(Date.now() + Math.random());
  await run("readwrite", (s) =>
    s.put({ id, name, bytes, pages, fields, status, thumb, addedAt: Date.now() })
  );
  await refresh();
  if (docs.length > MAX) {
    const extra = docs.slice(MAX).map((d) => d.id);
    await run("readwrite", (s) => extra.forEach((x) => s.delete(x)));
    await refresh();
  }
  return id;
}

export async function getBytes(id) {
  const rec = await run("readonly", (s) => s.get(id));
  return rec ? rec.bytes : null;
}

export async function getRecord(id) {
  return (await run("readonly", (s) => s.get(id))) || null;
}

export async function getAllRecords() {
  try {
    return (await run("readonly", (s) => s.getAll())) || [];
  } catch {
    return [];
  }
}

export async function patchDoc(id, patch) {
  const rec = await run("readonly", (s) => s.get(id));
  if (!rec) return;
  await run("readwrite", (s) => s.put({ ...rec, ...patch }));
  await refresh();
}

export async function removeDoc(id) {
  await run("readwrite", (s) => s.delete(id));
  await refresh();
}

export function useHistory() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function ago(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

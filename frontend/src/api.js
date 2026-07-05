// Tiny fetch wrapper around the Redraft FastAPI backend.
// Override the target with VITE_API_BASE when needed (defaults to localhost:8000).
import { supabase, hasSupabase } from "./lib/supabase.js";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// Attach the signed-in user's Supabase token so the backend can authorize +
// meter the request. Proactively refreshes an expired / near-expiry access token
// (getSession alone can hand back a stale one → 401 on every call). No session
// (or Supabase not configured) → no header, and the backend runs open.
async function authHeaders() {
  if (!hasSupabase) return {};
  try {
    let session = (await supabase.auth.getSession()).data?.session;
    const expMs = session?.expires_at ? session.expires_at * 1000 : 0;
    if (session && expMs && expMs < Date.now() + 60_000) {
      // token gone/expiring within 60s → refresh so the call isn't rejected
      const refreshed = (await supabase.auth.refreshSession()).data?.session;
      if (refreshed) session = refreshed;
    }
    const t = session?.access_token;
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

async function asError(res) {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body && body.detail) detail = body.detail;
  } catch {
    /* non-JSON error body — keep the status line */
  }
  const err = new Error(detail);
  err.status = res.status; // lets callers detect 401 (sign in) / 402 (upgrade)
  return err;
}

// GET / → true if the API is reachable (used by the connection badge)
export async function ping() {
  try {
    const res = await fetch(`${API_BASE}/`, { method: "GET" });
    return res.ok;
  } catch {
    return false;
  }
}

// GET /me → { auth, plan, used, limit } — the caller's plan + monthly usage.
export async function getMe() {
  const res = await fetch(`${API_BASE}/me`, { headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /extract → { filename, pages, span_count, spans }
export async function extractSpans(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/extract`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /edit → { blob, fontReport }
// `stamps` = added text / signature overlays to bake in. `final` = true only on
// the real download (counts toward the plan); previews leave it false.
export async function editPdf(file, edits, stamps = [], final = false) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("edits", JSON.stringify(edits));
  if (stamps && stamps.length) fd.append("stamps", JSON.stringify(stamps));
  if (final) fd.append("final", "1");
  const res = await fetch(`${API_BASE}/edit`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  let fontReport = null;
  const hdr = res.headers.get("X-Redraft-Font-Report");
  if (hdr) {
    try {
      fontReport = JSON.parse(decodeURIComponent(escape(atob(hdr))));
    } catch {
      /* ignore malformed header */
    }
  }
  return { blob: await res.blob(), fontReport };
}

// POST /fonts → { count, fonts: [{font, raw_font, family, weight, style, status, source, cache_name}] }
export async function checkFonts(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/fonts`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /font → install a real .ttf/.otf for an unmatched font → { ok, installed_as, font }
export async function uploadFont(fontname, file) {
  const fd = new FormData();
  fd.append("fontname", fontname);
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/font`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /annex/model → { filename, sections, items[], headers[], total, item_count, template }
// Pass a saved `profile` (layout template) to reuse it and skip re-detection.
export async function annexModel(file, profile = null) {
  const fd = new FormData();
  fd.append("file", file);
  if (profile) fd.append("template", JSON.stringify(profile));
  const res = await fetch(`${API_BASE}/annex/model`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /annex/generate → { blob, generated, failed } — one annex per data row.
// `profile` = saved layout template (auto-detected if null); `filenameCol` names
// each output file from a data column.
export async function annexGenerate(
  template, dataFile, mapping, headers = {}, profile = null, filenameCol = "",
) {
  const fd = new FormData();
  fd.append("template", template);
  fd.append("data", dataFile);
  fd.append("mapping", JSON.stringify(mapping));
  fd.append("headers", JSON.stringify(headers));
  if (profile) fd.append("profile", JSON.stringify(profile));
  if (filenameCol) fd.append("filename_col", filenameCol);
  const res = await fetch(`${API_BASE}/annex/generate`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return {
    blob: await res.blob(),
    generated: Number(res.headers.get("X-Redraft-Generated") || 0),
    failed: Number(res.headers.get("X-Redraft-Failed") || 0),
  };
}

// POST /automap → { mapping: {column: spanId}, source: "ai"|"values"|"none" }
// Matches spreadsheet columns to the PDF's text fields (AI when configured).
export async function autoMapFields(spans, columns, samples) {
  const fd = new FormData();
  fd.append("spans", JSON.stringify(spans.map((s) => ({ id: s.id, text: s.text }))));
  fd.append("columns", JSON.stringify(columns));
  fd.append("samples", JSON.stringify(samples));
  const res = await fetch(`${API_BASE}/automap`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /bulk → { blob, generated, failed }
// `filenameCol` (optional) names each output file from a data column.
// `output` = "zip" (one file per row) or "merged" (one combined PDF).
export async function bulkGenerate(template, dataFile, mapping, filenameCol = "", output = "zip") {
  const fd = new FormData();
  fd.append("template", template);
  fd.append("data", dataFile);
  fd.append("mapping", JSON.stringify(mapping));
  if (filenameCol) fd.append("filename_col", filenameCol);
  if (output && output !== "zip") fd.append("output", output);
  const res = await fetch(`${API_BASE}/bulk`, { method: "POST", body: fd, headers: await authHeaders() });
  if (!res.ok) throw await asError(res);
  return {
    blob: await res.blob(),
    generated: Number(res.headers.get("X-Redraft-Generated") || 0),
    failed: Number(res.headers.get("X-Redraft-Failed") || 0),
  };
}

// Tiny fetch wrapper around the Redraft FastAPI backend.
// Override the target with VITE_API_BASE when needed (defaults to localhost:8000).

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function asError(res) {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body && body.detail) detail = body.detail;
  } catch {
    /* non-JSON error body — keep the status line */
  }
  return new Error(detail);
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

// POST /extract → { filename, pages, span_count, spans }
export async function extractSpans(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/extract`, { method: "POST", body: fd });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /edit → { blob, fontReport }
export async function editPdf(file, edits) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("edits", JSON.stringify(edits));
  const res = await fetch(`${API_BASE}/edit`, { method: "POST", body: fd });
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
  const res = await fetch(`${API_BASE}/fonts`, { method: "POST", body: fd });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /font → install a real .ttf/.otf for an unmatched font → { ok, installed_as, font }
export async function uploadFont(fontname, file) {
  const fd = new FormData();
  fd.append("fontname", fontname);
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/font`, { method: "POST", body: fd });
  if (!res.ok) throw await asError(res);
  return res.json();
}

// POST /bulk → { blob, generated, failed }
export async function bulkGenerate(template, dataFile, mapping) {
  const fd = new FormData();
  fd.append("template", template);
  fd.append("data", dataFile);
  fd.append("mapping", JSON.stringify(mapping));
  const res = await fetch(`${API_BASE}/bulk`, { method: "POST", body: fd });
  if (!res.ok) throw await asError(res);
  return {
    blob: await res.blob(),
    generated: Number(res.headers.get("X-Redraft-Generated") || 0),
    failed: Number(res.headers.get("X-Redraft-Failed") || 0),
  };
}

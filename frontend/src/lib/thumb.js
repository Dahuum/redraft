// Render a small first-page thumbnail (JPEG data URL) of a PDF with PDF.js.
// Used for the history cards so they show the actual document (with edits).
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

export async function renderThumb(data, targetW = 420) {
  try {
    const bytes =
      data instanceof Uint8Array ? data.slice() : new Uint8Array(data.slice(0));
    const pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
    const page = await pdf.getPage(1);
    const base = page.getViewport({ scale: 1 });
    const scale = targetW / base.width;
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext("2d");
    await page.render({ canvasContext: ctx, viewport }).promise;
    return canvas.toDataURL("image/jpeg", 0.72);
  } catch {
    return null;
  }
}

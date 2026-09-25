import { useEffect, useRef, useState } from "react";
import SignaturePad from "signature_pad";
import { cloudEnabled, listSignatures, saveSignature, deleteSignature } from "../lib/cloud.js";
import { toast } from "./Toast.jsx";
import Notice from "./Notice.jsx";
import Icon from "./Icon.jsx";

// Real signature-style script fonts (loaded in app.html) — not casual handwriting.
const SIG_FONTS = [
  { name: "Alex Brush", css: "'Alex Brush', cursive" },
  { name: "Allura", css: "'Allura', cursive" },
  { name: "Mr Dafoe", css: "'Mr Dafoe', cursive" },
  { name: "Herr Von Muellerhoff", css: "'Herr Von Muellerhoff', cursive" },
  { name: "Pinyon Script", css: "'Pinyon Script', cursive" },
  { name: "Yellowtail", css: "'Yellowtail', cursive" },
];
const INK = "#12213a"; // dark navy — reads as a real signature, stamps clean on white
const SIG_STORE = "redraft:signatures"; // saved signatures persist on this device

// Iron out hand tremor: light moving-average per stroke, endpoints anchored so
// the signature keeps its extent. Preserves time/pressure so signature_pad's
// velocity-based pen width still varies naturally on re-render.
function smoothStroke(points, passes = 2, window = 1) {
  if (!points || points.length < 4) return points;
  let pts = points.map((p) => ({ ...p }));
  for (let pass = 0; pass < passes; pass++) {
    const out = pts.map((p) => ({ ...p }));
    for (let i = 1; i < pts.length - 1; i++) {
      let sx = 0, sy = 0, n = 0;
      for (let j = Math.max(0, i - window); j <= Math.min(pts.length - 1, i + window); j++) {
        sx += pts[j].x;
        sy += pts[j].y;
        n++;
      }
      out[i].x = sx / n;
      out[i].y = sy / n;
    }
    pts = out;
  }
  return pts;
}

/**
 * SignaturePanel. In the PDF Editor's right pane behind the Text / Sign toggle.
 *   Draw — signature_pad: velocity-variable, Bézier-smoothed pen strokes.
 *   Type — a real signature-style script font + an optional flourish, rendered
 *          to a transparent PNG (WYSIWYG preview).
 *   Saved — reusable gallery (persisted). Place → onPlace(pngDataUrl, ratio).
 */
export default function SignaturePanel({ onPlace, cloud = cloudEnabled }) {
  const [saved, setSaved] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(SIG_STORE) || "[]");
    } catch {
      return [];
    }
  }); // [{ id, url, ratio }]
  const [mode, setMode] = useState(() => (saved.length ? "saved" : "draw"));
  const [note, setNote] = useState(null);

  // Cloud sync: when signed in, the account is the source of truth (cross-device,
  // capped at 3). Otherwise fall back to this device's localStorage.
  useEffect(() => {
    if (cloud) {
      listSignatures().then((sigs) => {
        if (!sigs) return; // cloud unavailable → keep whatever's loaded locally
        setSaved(sigs);
        if (sigs.length) setMode("saved");
      }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (cloud) return; // cloud is authoritative; don't shadow it locally
    try {
      localStorage.setItem(SIG_STORE, JSON.stringify(saved));
    } catch {
      /* storage full / blocked — non-fatal */
    }
  }, [saved]);

  // ---- Draw pad (signature_pad) ----
  const canvasRef = useRef(null);
  const padRef = useRef(null);
  const [hasInk, setHasInk] = useState(false);

  useEffect(() => {
    if (mode !== "draw") return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    canvas.width = canvas.offsetWidth * ratio;
    canvas.height = canvas.offsetHeight * ratio;
    canvas.getContext("2d").scale(ratio, ratio);
    const pad = new SignaturePad(canvas, {
      penColor: INK,
      minWidth: 0.7,
      maxWidth: 2.9,
      velocityFilterWeight: 0.7,
      backgroundColor: "rgba(0,0,0,0)", // transparent → clean PNG
    });
    pad.addEventListener("endStroke", () => {
      // Auto-refine ONLY the stroke that just finished. Re-smoothing the earlier
      // ones every time would compound and turn the whole signature to mush — a
      // dot or a t-cross should refine itself, not re-blur everything before it.
      const d = pad.toData();
      const last = d[d.length - 1];
      if (last && last.points && last.points.length > 3) {
        last.points = smoothStroke(last.points);
        pad.fromData(d);
      }
      setHasInk(!pad.isEmpty());
    });
    padRef.current = pad;
    setHasInk(false);
    return () => {
      try {
        pad.off?.();
      } catch {
        /* noop */
      }
      padRef.current = null;
    };
  }, [mode]);

  const clearPad = () => {
    padRef.current?.clear();
    setHasInk(false);
  };
  function beautify() {
    const pad = padRef.current;
    if (!pad || pad.isEmpty()) return;
    const d = pad.toData();
    for (const g of d) if (g.points && g.points.length > 3) g.points = smoothStroke(g.points);
    pad.fromData(d);
  }
  function saveDrawn() {
    const pad = padRef.current;
    if (!pad || pad.isEmpty()) return;
    const c = canvasRef.current;
    addSig(pad.toDataURL("image/png"), c.height ? c.width / c.height : 3);
    clearPad();
  }

  // ---- Type (signature font + flourish) ----
  const [name, setName] = useState("");
  const [fontIdx, setFontIdx] = useState(0);
  const [flourish, setFlourish] = useState(true);
  const [preview, setPreview] = useState(null); // WYSIWYG data-URL

  function renderTyped(text, idx) {
    const f = SIG_FONTS[idx];
    const dpr = window.devicePixelRatio || 1;
    const H = 150, padX = 46, size = 84, font = `${size}px ${f.css}`;
    const meas = document.createElement("canvas").getContext("2d");
    meas.font = font;
    const tw = Math.ceil(meas.measureText(text).width);
    const W = Math.max(140, tw + padX * 2);
    const c = document.createElement("canvas");
    c.width = W * dpr;
    c.height = H * dpr;
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.fillStyle = INK;
    ctx.strokeStyle = INK;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = font;
    ctx.fillText(text, W / 2, H * 0.42);
    if (flourish) {
      const y = H * 0.74;
      ctx.lineWidth = 2.6;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.moveTo(W * 0.06, y);
      ctx.bezierCurveTo(W * 0.30, y + 14, W * 0.52, y - 16, W * 0.74, y - 2);
      ctx.bezierCurveTo(W * 0.84, y + 5, W * 0.92, y + 5, W * 0.97, y - 9);
      ctx.stroke();
    }
    return { url: c.toDataURL("image/png"), ratio: W / H };
  }

  // Regenerate the WYSIWYG preview when inputs change (after the font loads).
  useEffect(() => {
    if (mode !== "type") return;
    let cancelled = false;
    (async () => {
      const text = name.trim();
      if (!text) {
        setPreview(null);
        return;
      }
      try {
        await document.fonts?.load?.(`84px ${SIG_FONTS[fontIdx].css}`);
      } catch {
        /* already loaded / unavailable */
      }
      if (!cancelled) setPreview(renderTyped(text, fontIdx).url);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, fontIdx, flourish, mode]);

  async function saveTyped() {
    if (!name.trim()) return;
    try {
      await document.fonts?.load?.(`84px ${SIG_FONTS[fontIdx].css}`);
    } catch {
      /* noop */
    }
    const { url, ratio } = renderTyped(name.trim(), fontIdx);
    addSig(url, ratio);
  }

  // ---- Shared ----
  async function addSig(url, ratio) {
    setNote(null);
    if (cloud) {
      try {
        const sig = await saveSignature({ url, ratio: ratio || 3 });
        setSaved((s) => [sig, ...s]);
        setMode("saved");
      } catch (e) {
        setNote(e.message || "Couldn't save the signature."); // e.g. 3/3 limit
      }
      return;
    }
    const id = `${Date.now()}${Math.round(Math.random() * 1e4)}`;
    setSaved((s) => [{ id, url, ratio: ratio || 3 }, ...s].slice(0, 24));
    setMode("saved");
  }
  function place(url, ratio) {
    if (onPlace) onPlace(url, ratio);
    else setNote("Signature ready — placing it on the PDF is the next step.");
  }
  function removeSig(sig) {
    setSaved((s) => s.filter((x) => x.id !== sig.id));
    if (cloud) deleteSignature(sig.id).catch(() => {});
    toast("Signature deleted", {
      actionLabel: "Undo",
      onAction: async () => {
        if (cloud) {
          try {
            const back = await saveSignature({ url: sig.url, ratio: sig.ratio });
            setSaved((s) => [back, ...s]);
            return;
          } catch {
            /* cap may be filled meanwhile — fall back to local restore */
          }
        }
        setSaved((s) => [sig, ...s].slice(0, 24));
      },
    });
  }

  const TABS = [
    ["draw", "sign", "Draw"],
    ["type", "keyboard", "Type"],
    ["saved", "bookmark", saved.length ? `Saved (${saved.length})` : "Saved"],
  ];

  return (
    <div className="px-4 pb-4 pt-1 space-y-4 animate-fade">
      {/* Mode tabs */}
      <div className="flex items-center gap-1 bg-black/25 rounded-full p-1">
        {TABS.map(([k, icon, lbl]) => (
          <button
            key={k}
            onClick={() => setMode(k)}
            className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-full text-[14px] transition-all ${
              mode === k
                ? "bg-primary text-on-primary"
                : "text-on-surface-variant hover:text-on-surface"
            }`}
          >
            <Icon name={icon} size={16} />
            {lbl}
          </button>
        ))}
      </div>

      {/* Draw */}
      {mode === "draw" && (
        <div className="space-y-3">
          <p className="text-[14px] leading-5 text-on-surface-variant">
            Draw your signature — it auto-smooths when you lift the pen. Tap Smooth for more.
          </p>
          <div
            className="relative rounded-2xl bg-white overflow-hidden"
            style={{ height: 180 }}
          >
            <div className="absolute left-6 right-6 bottom-10 border-b-2 border-dashed border-[#2d2323]/25 pointer-events-none" />
            <span className="absolute left-6 bottom-4 text-[12px] text-[#736b6b] pointer-events-none">
              Sign here
            </span>
            <canvas ref={canvasRef} className="absolute inset-0 w-full h-full touch-none cursor-crosshair" />
            {!hasInk && (
              <Icon name="sign" size={40} className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-[60%] text-on-surface-variant/30 pointer-events-none" />
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={clearPad}
              className="px-4 py-2.5 rounded-full bg-black/25 text-on-surface hover:bg-[rgb(var(--c-field))] text-[14px] flex items-center gap-1.5 transition-colors"
            >
              <Icon name="eraser" size={16} />
              Clear
            </button>
            <button
              onClick={beautify}
              disabled={!hasInk}
              title="Smooth out the strokes"
              className="px-4 py-2.5 rounded-full bg-black/25 text-on-surface hover:bg-[rgb(var(--c-field))] text-[14px] flex items-center gap-1.5 transition-colors disabled:opacity-40"
            >
              <Icon name="wand" size={16} />
              Smooth
            </button>
            <button
              onClick={saveDrawn}
              disabled={!hasInk}
              className="flex-1 bg-secondary-container hover:bg-secondary-container-hover text-white py-2.5 rounded-full text-[14px] flex justify-center items-center gap-2 transition-colors disabled:opacity-40"
            >
              <Icon name="check" size={18} />
              Save signature
            </button>
          </div>
        </div>
      )}

      {/* Type */}
      {mode === "type" && (
        <div className="space-y-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="w-full bg-[rgb(var(--c-field))] rounded-2xl py-3 px-4 text-[15px] text-on-surface placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-2 focus:ring-secondary-container"
          />

          {/* WYSIWYG preview of the selected style */}
          <div className="rounded-2xl bg-white h-24 flex items-center justify-center overflow-hidden px-3">
            {preview ? (
              <img src={preview} alt="signature preview" className="max-h-20 max-w-full object-contain" />
            ) : (
              <span className="text-[#736b6b] text-[14px]">Type your name to preview</span>
            )}
          </div>

          <label className="flex items-center gap-2 text-caption text-on-surface-variant cursor-pointer select-none">
            <input
              type="checkbox"
              checked={flourish}
              onChange={(e) => setFlourish(e.target.checked)}
              className="accent-secondary-container w-4 h-4"
            />
            Add a flourish underline
          </label>

          {/* Style picker */}
          <div className="grid grid-cols-2 gap-2">
            {SIG_FONTS.map((f, i) => (
              <button
                key={f.name}
                onClick={() => setFontIdx(i)}
                className={`rounded-2xl bg-white h-14 flex items-center justify-center overflow-hidden transition-all ${
                  fontIdx === i ? "ring-2 ring-secondary-container" : "hover:ring-2 hover:ring-white/30"
                }`}
              >
                <span
                  style={{ fontFamily: f.css, color: INK, fontSize: 26, lineHeight: 1 }}
                  className="truncate px-2"
                >
                  {name.trim() || "Signature"}
                </span>
              </button>
            ))}
          </div>

          <button
            onClick={saveTyped}
            disabled={!name.trim()}
            className="w-full bg-secondary-container hover:bg-secondary-container-hover text-white py-3 rounded-full text-[15px] flex justify-center items-center gap-2 transition-colors disabled:opacity-40"
          >
            <Icon name="check" size={18} />
            Save signature
          </button>
        </div>
      )}

      {/* Saved */}
      {mode === "saved" && (
        <div className="space-y-3">
          {saved.length === 0 ? (
            <div className="rounded-2xl border-2 border-dashed border-outline-variant p-8 text-center text-on-surface-variant">
              <Icon name="sign" size={32} className="opacity-40" />
              <p className="mt-2 text-body-md text-on-surface">No saved signatures yet.</p>
              <p className="text-caption">Draw or type one — it stays here for next time.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {saved.map((s) => (
                <div
                  key={s.id}
                  className="group flex items-center gap-3 rounded-2xl bg-white p-2 pl-3"
                >
                  <img src={s.url} alt="signature" className="h-12 flex-1 object-contain min-w-0" />
                  <button
                    onClick={() => place(s.url, s.ratio)}
                    title="Place on document"
                    className="shrink-0 px-4 py-2 rounded-full bg-secondary-container text-white text-[14px] flex items-center gap-1.5 hover:bg-secondary-container-hover transition-colors"
                  >
                    <Icon name="pen" size={16} />
                    Place
                  </button>
                  <button
                    onClick={() => removeSig(s)}
                    title="Delete"
                    className="shrink-0 w-9 h-9 rounded-full text-[#736b6b] hover:text-[#d64545] hover:bg-[#d64545]/10 flex items-center justify-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100 transition-all"
                  >
                    <Icon name="trash" size={18} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {note && <Notice tone="info">{note}</Notice>}
    </div>
  );
}

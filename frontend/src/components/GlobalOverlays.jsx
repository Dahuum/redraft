import { useEffect, useState } from "react";
import ToastHost from "./Toast.jsx";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function useApiStatus() {
  const [slow, setSlow] = useState(false);
  const [down, setDown] = useState(false);
  useEffect(() => {
    const wake = () => setSlow(true);
    const woke = () => setSlow(false);
    const broke = () => setDown(true);
    const fixed = () => setDown(false);
    window.addEventListener("rd:api-slow", wake);
    window.addEventListener("rd:api-fast", woke);
    window.addEventListener("rd:api-down", broke);
    window.addEventListener("rd:api-ok", fixed);
    return () => {
      window.removeEventListener("rd:api-slow", wake);
      window.removeEventListener("rd:api-fast", woke);
      window.removeEventListener("rd:api-down", broke);
      window.removeEventListener("rd:api-ok", fixed);
    };
  }, []);
  return { slow, down };
}

export default function GlobalOverlays() {
  const { slow, down } = useApiStatus();
  return (
    <>
      <ToastHost />
      {down && (
        <div className="fixed bottom-4 left-4 z-[110] max-w-sm flex items-start gap-2 rounded-xl border border-error/40 bg-surface-container-high/95 px-4 py-2.5 shadow-panel backdrop-blur-md">
          <span className="material-symbols-outlined text-[18px] text-error">
            cloud_off
          </span>
          <span className="text-caption text-on-surface">
            Can't reach the API at{" "}
            <b className="font-semibold">{API_BASE}</b>. Start it with{" "}
            <code className="text-accent-cyan">uvicorn api:app --app-dir backend --port 8000</code>
            {" "}— or open this app via localhost, not a network URL.
          </span>
        </div>
      )}
      {!down && slow && (
        <div className="fixed bottom-4 left-4 z-[110] flex items-center gap-2 rounded-full border border-outline-variant/50 bg-surface-container-high/95 px-4 py-2 shadow-panel backdrop-blur-md">
          <span className="material-symbols-outlined animate-spin text-[18px] text-accent-cyan">
            progress_activity
          </span>
          <span className="text-caption text-on-surface">
            Waking the server… a cold start can take up to ~40&nbsp;s.
          </span>
        </div>
      )}
    </>
  );
}

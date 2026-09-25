import { useEffect, useState } from "react";
import ToastHost from "./Toast.jsx";
import Icon from "./Icon.jsx";

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
        <div className="fixed bottom-4 left-4 right-4 sm:right-auto z-[110] max-w-[384px] flex items-start gap-2 rounded-[24px] bg-[rgb(var(--c-tint-coral))] text-on-surface px-5 py-3.5 shadow-panel">
          <Icon name="cloudoff" size={18} className="text-error" />
          <span className="text-[14px] leading-5 text-on-surface">
            Can't reach the API at{" "}
            <b className="font-semibold">{API_BASE}</b>. Start it with{" "}
            <code className="rounded bg-black/10 px-1.5 py-0.5 text-[12px]">uvicorn api:app --app-dir backend --port 8000</code>
            {" "}— or open this app via localhost, not a network URL.
          </span>
        </div>
      )}
      {!down && slow && (
        <div className="fixed bottom-4 left-4 right-4 sm:right-auto z-[110] flex items-center gap-2 rounded-full bg-surface px-5 py-3 shadow-panel">
          <Icon name="spinner" size={18} spin className="text-accent-cyan" />
          <span className="text-[14px] leading-5 text-on-surface">
            Waking the server… a cold start can take up to ~40&nbsp;s.
          </span>
        </div>
      )}
    </>
  );
}

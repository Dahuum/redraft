import { useEffect, useState } from "react";
import ToastHost from "./Toast.jsx";
import Icon from "./Icon.jsx";
import { saveApiKey } from "../api.js";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// A self-hosted container with REDRAFT_API_KEY asks for the key once; it stays in this browser.
function KeyPrompt() {
  const [open, setOpen] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [value, setValue] = useState("");
  useEffect(() => {
    const ask = (e) => {
      setRejected(Boolean(e.detail?.rejected));
      setOpen(true);
    };
    window.addEventListener("rd:api-key", ask);
    return () => window.removeEventListener("rd:api-key", ask);
  }, []);
  if (!open) return null;
  const submit = (e) => {
    e.preventDefault();
    if (!value.trim()) return;
    saveApiKey(value);
    window.location.reload();
  };
  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-[rgb(var(--c-shadow)/0.5)] backdrop-blur-sm p-4 animate-fade">
      <form
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-label="Access key"
        className="w-full max-w-[28rem] bg-surface rounded-[32px] shadow-panel p-7 animate-drop"
      >
        <p className="font-hand uppercase tracking-[0.06em] text-[19px] leading-none text-secondary-container">Private server</p>
        <h3 className="font-display-md font-black tracking-[-0.5px] text-on-surface text-[28px] leading-[1.1] mt-2">Enter the access key</h3>
        <p className="text-[15px] leading-6 text-on-surface-variant mt-3">
          This Redraft runs on your network and is locked. Ask whoever set it up for the key
          (<code className="rounded bg-black/10 px-1.5 py-0.5 text-[12px]">REDRAFT_API_KEY</code>). It stays in this browser.
        </p>
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
          autoComplete="off"
          aria-label="Access key"
          placeholder="Access key"
          className="w-full mt-4 bg-surface-container rounded-2xl py-3.5 px-5 text-[16px] text-on-surface placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-2 focus:ring-secondary-container"
        />
        {rejected && <p className="text-[14px] text-error mt-2">That key was not accepted.</p>}
        <div className="mt-5 flex justify-end">
          <button
            type="submit"
            disabled={!value.trim()}
            className="inline-flex items-center justify-center gap-2 rounded-full text-[16px] leading-5 px-[26px] py-[14px] bg-secondary-container text-white hover:bg-secondary-container-hover disabled:opacity-60"
          >
            <Icon name="unlock" size={18} />
            Unlock
          </button>
        </div>
      </form>
    </div>
  );
}

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
      <KeyPrompt />
      {down && (
        <div className="fixed bottom-4 left-4 right-4 sm:right-auto z-[110] max-w-[384px] flex items-start gap-2 rounded-[24px] bg-[rgb(var(--c-tint-coral))] text-on-surface px-5 py-3.5 shadow-panel">
          <Icon name="cloudoff" size={18} className="text-error" />
          <span className="text-[14px] leading-5 text-on-surface">
            {API_BASE === "" ? (
              <>Can't reach the Redraft server. Check that its container is running, then reload.</>
            ) : (
              <>
                Can't reach the API at <b className="font-semibold">{API_BASE}</b>. Start it with{" "}
                <code className="rounded bg-black/10 px-1.5 py-0.5 text-[12px]">uvicorn api:app --app-dir backend --port 8000</code>
                {" "}— or open this app via localhost, not a network URL.
              </>
            )}
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

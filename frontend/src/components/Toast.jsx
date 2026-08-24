import { useSyncExternalStore } from "react";

let toasts = [];
const listeners = new Set();
const emit = () => listeners.forEach((l) => l());
const subscribe = (cb) => {
  listeners.add(cb);
  return () => listeners.delete(cb);
};
const getSnapshot = () => toasts;

export function toast(message, { actionLabel, onAction, duration = 5000 } = {}) {
  const id = `${Date.now()}${Math.round(Math.random() * 1e4)}`;
  toasts = [...toasts, { id, message, actionLabel, onAction }];
  emit();
  if (duration > 0) setTimeout(() => dismiss(id), duration);
  return id;
}

export function dismiss(id) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export default function ToastHost() {
  const items = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  if (!items.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[120] flex flex-col items-end gap-2">
      {items.map((t) => (
        <div
          key={t.id}
          className="animate-drop flex max-w-sm items-center gap-3 rounded-xl border border-outline-variant/50 bg-surface-container-high px-4 py-2.5 shadow-panel"
        >
          <span className="text-caption text-on-surface">{t.message}</span>
          {t.actionLabel && (
            <button
              onClick={() => {
                t.onAction?.();
                dismiss(t.id);
              }}
              className="shrink-0 font-label-md text-[13px] font-semibold text-secondary hover:underline"
            >
              {t.actionLabel}
            </button>
          )}
          <button
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
            className="text-on-surface-variant hover:text-on-surface"
          >
            <span className="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      ))}
    </div>
  );
}

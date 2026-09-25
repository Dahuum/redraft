import { useSyncExternalStore } from "react";
import Icon from "./Icon.jsx";

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
    // Bounded on BOTH sides so the card keeps a gutter on a phone, where 384px
    // is wider than the screen. The cap below is an arbitrary value rather than
    // max-w-sm: this theme's named spacing keys shadow the built-in max-width
    // scale, so max-w-sm resolved to --spacing-sm (8px) and the toast rendered
    // as a 34px sliver off the right edge, one word per line, with its buttons
    // off-screen. See the note above @theme in index.css. The strip is
    // click-through except on the cards themselves.
    <div className="pointer-events-none fixed bottom-4 left-4 right-4 z-[120] flex flex-col items-end gap-2">
      {items.map((t) => (
        <div
          key={t.id}
          className="ink-scope animate-drop pointer-events-auto flex max-w-[400px] items-center gap-3 rounded-full bg-[rgb(var(--c-sidebar))] text-on-surface pl-5 pr-3 py-2.5 shadow-[0_18px_44px_-12px_rgb(0_0_0/0.5)]"
        >
          <span className="min-w-0 text-[14px] leading-5 text-on-surface">{t.message}</span>
          {t.actionLabel && (
            <button
              onClick={() => {
                t.onAction?.();
                dismiss(t.id);
              }}
              className="shrink-0 rounded-full bg-[#f6f1ea] px-3.5 py-1.5 text-[13px] font-semibold text-[#2d2323] hover:bg-white transition-colors"
            >
              {t.actionLabel}
            </button>
          )}
          <button
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
            className="w-8 h-8 shrink-0 rounded-full grid place-items-center text-on-surface-variant hover:bg-white/10 hover:text-on-surface transition-colors"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

import Icon from "./Icon.jsx";

const TONES = {
  error: { box: "bg-error-container text-on-error-container", icon: "warning" },
  success: { box: "bg-[rgb(var(--c-tint-mint))] text-on-surface", icon: "check" },
  info: { box: "bg-[rgb(var(--c-tint-blue))] text-on-surface", icon: "info" },
};

/**
 * Inline status pill used across workspace footers (generation results,
 * save notes, hints). tone: error | success | info; icon overrides the default.
 */
export default function Notice({ tone = "info", icon, children }) {
  const t = TONES[tone] || TONES.info;
  return (
    <div className={`rounded-2xl px-4 py-2.5 text-[14px] leading-5 flex items-center gap-2.5 ${t.box}`}>
      <Icon name={icon || t.icon} size={17} className="shrink-0" />
      <span className="min-w-0">{children}</span>
    </div>
  );
}

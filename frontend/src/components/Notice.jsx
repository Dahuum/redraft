const TONES = {
  error: { box: "border-error/30 bg-error/10 text-error", icon: "error" },
  success: {
    box: "border-secondary-container/30 bg-secondary-container/10 text-secondary",
    icon: "check_circle",
  },
  info: {
    box: "border-accent-cyan/30 bg-accent-cyan/10 text-accent-cyan",
    icon: "info",
  },
};

/**
 * Inline status pill used across workspace footers (generation results,
 * save notes, hints). tone: error | success | info; icon overrides the default.
 */
export default function Notice({ tone = "info", icon, children }) {
  const t = TONES[tone] || TONES.info;
  return (
    <div className={`rounded-lg px-3 py-2 text-caption flex items-center gap-2 border ${t.box}`}>
      <span className="material-symbols-outlined text-[16px] shrink-0">{icon || t.icon}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

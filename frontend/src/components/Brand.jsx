/** The Redraft mark: the blue sparkle tile, same as the landing page nav. */
export default function Brand({ size = 32, wordmark = true, className = "" }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <svg width={size} height={size} viewBox="0 0 58 58" aria-hidden="true" className="shrink-0">
        <rect x="3" y="3" width="52" height="52" rx="15" fill="#8aa0ff" stroke="rgb(var(--c-on-surface))" strokeWidth="3.5" />
        <path d="M29 13l3.6 10.4L43 27l-10.4 3.6L29 41l-3.6-10.4L15 27l10.4-3.6z" fill="#fff" stroke="rgb(var(--c-on-surface))" strokeWidth="3.2" strokeLinejoin="round" />
      </svg>
      {wordmark && (
        <span className="font-display-md font-black text-[20px] leading-none tracking-[-0.5px] text-on-surface">Redraft</span>
      )}
    </span>
  );
}

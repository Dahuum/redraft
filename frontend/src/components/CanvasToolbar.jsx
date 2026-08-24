const BTN = "text-on-surface-variant hover:text-primary transition-colors disabled:opacity-30";

/**
 * Floating page / zoom toolbar over a document canvas. One source of truth for
 * the editor, bulk and annex panes (they used to be three drifting copies).
 */
export default function CanvasToolbar({ pageIndex, pageCount, setPageIndex, zoom, setZoom }) {
  return (
    <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-surface/90 backdrop-blur-md border border-outline-variant/50 rounded-full px-3 py-1.5 flex items-center gap-3 z-10 shadow-xl">
      {pageCount > 1 && (
        <>
          <button
            disabled={pageIndex === 0}
            onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
            aria-label="Previous page"
            className={BTN}
          >
            <span className="material-symbols-outlined text-[18px]">chevron_left</span>
          </button>
          <span className="text-caption font-medium">
            {pageIndex + 1} / {pageCount}
          </span>
          <button
            disabled={pageIndex >= pageCount - 1}
            onClick={() => setPageIndex((p) => Math.min(pageCount - 1, p + 1))}
            aria-label="Next page"
            className={BTN}
          >
            <span className="material-symbols-outlined text-[18px]">chevron_right</span>
          </button>
          <div className="w-px h-4 bg-outline-variant"></div>
        </>
      )}
      <button
        onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}
        aria-label="Zoom out"
        className={BTN}
      >
        <span className="material-symbols-outlined text-[18px]">zoom_out</span>
      </button>
      <span className="text-caption font-medium">{Math.round(zoom * 100)}%</span>
      <button
        onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(2)))}
        aria-label="Zoom in"
        className={BTN}
      >
        <span className="material-symbols-outlined text-[18px]">zoom_in</span>
      </button>
      <div className="w-px h-4 bg-outline-variant"></div>
      <button
        onClick={() => setZoom(1)}
        title="Fit width"
        aria-label="Fit width"
        className={BTN}
      >
        <span className="material-symbols-outlined text-[18px]">fit_screen</span>
      </button>
    </div>
  );
}

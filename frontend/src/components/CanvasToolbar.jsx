import Icon from "./Icon.jsx";

const BTN = "w-9 h-9 rounded-full grid place-items-center text-on-surface hover:bg-surface-container transition-colors disabled:opacity-30 disabled:hover:bg-transparent";

/**
 * Floating page / zoom dock over a document canvas. One source of truth for
 * the editor, bulk and annex panes. Sits at the bottom, like the dock in the
 * landing page's product frame.
 */
export default function CanvasToolbar({ pageIndex, pageCount, setPageIndex, zoom, setZoom }) {
  return (
    <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-surface/95 backdrop-blur-md rounded-full px-2 py-1.5 flex items-center gap-1 z-10 shadow-panel">
      {pageCount > 1 && (
        <>
          <button
            disabled={pageIndex === 0}
            onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
            aria-label="Previous page"
            className={BTN}
          >
            <Icon name="chevleft" size={18} />
          </button>
          <span className="text-[14px] font-medium tabular-nums min-w-[3.2rem] text-center text-on-surface">
            {pageIndex + 1} / {pageCount}
          </span>
          <button
            disabled={pageIndex >= pageCount - 1}
            onClick={() => setPageIndex((p) => Math.min(pageCount - 1, p + 1))}
            aria-label="Next page"
            className={BTN}
          >
            <Icon name="chevright" size={18} />
          </button>
          <div className="w-px h-5 bg-outline-variant mx-1"></div>
        </>
      )}
      <button
        onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.1).toFixed(2)))}
        aria-label="Zoom out"
        className={BTN}
      >
        <Icon name="zoomout" size={18} />
      </button>
      <span className="text-[14px] font-medium tabular-nums min-w-[3rem] text-center text-on-surface">{Math.round(zoom * 100)}%</span>
      <button
        onClick={() => setZoom((z) => Math.min(2.5, +(z + 0.1).toFixed(2)))}
        aria-label="Zoom in"
        className={BTN}
      >
        <Icon name="zoomin" size={18} />
      </button>
      <div className="w-px h-5 bg-outline-variant mx-1"></div>
      <button onClick={() => setZoom(1)} title="Fit width" aria-label="Fit width" className={BTN}>
        <Icon name="fit" size={18} />
      </button>
    </div>
  );
}

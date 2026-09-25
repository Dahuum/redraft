/**
 * Redraft icon set — one hand-tuned family (2.2px round strokes on a 24 grid)
 * so every screen speaks the same visual language as the landing page.
 * Replaces the generic Material Symbols font, screen by screen.
 */
const P = {
  upload: <><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5" /><path d="M4.5 14.5v3A2.5 2.5 0 0 0 7 20h10a2.5 2.5 0 0 0 2.5-2.5v-3" /></>,
  download: <><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M4.5 14.5v3A2.5 2.5 0 0 0 7 20h10a2.5 2.5 0 0 0 2.5-2.5v-3" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="9.5" rx="2.5" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></>,
  unlock: <><rect x="5" y="10.5" width="14" height="9.5" rx="2.5" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.6-1.6" /></>,
  pen: <><path d="M4.5 19.5 5.6 15 16 4.6a2.1 2.1 0 0 1 3 3L8.5 18z" /><path d="m14.2 6.5 3 3" /></>,
  trash: <><path d="M5 7h14M9.5 7V5a1.5 1.5 0 0 1 1.5-1.5h2A1.5 1.5 0 0 1 14.5 5v2" /><path d="M6.5 7l.8 11.2A2 2 0 0 0 9.3 20h5.4a2 2 0 0 0 2-1.8L17.5 7" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  history: <><path d="M4 12a8 8 0 1 0 2.5-5.8" /><path d="M4 4.5v4h4M12 8v4.2l2.8 1.8" /></>,
  layers: <><path d="M12 4 20.5 8.5 12 13 3.5 8.5z" /><path d="m3.5 12.5 8.5 4.5 8.5-4.5M3.5 16.5 12 21l8.5-4.5" /></>,
  doc: <><path d="M6.5 3.5h7L18 8v11.5a1 1 0 0 1-1 1H6.5a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z" /><path d="M13.5 3.5V8H18M8.5 12.5h7M8.5 16h5" /></>,
  login: <><path d="M14 5h3.5A1.5 1.5 0 0 1 19 6.5v11a1.5 1.5 0 0 1-1.5 1.5H14" /><path d="M4.5 12h9M10 8l4 4-4 4" /></>,
  logout: <><path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H10" /><path d="M19.5 12h-9M16 8l4 4-4 4" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.1" /></>,
  spark: <path d="M12 3.5 14.3 10l6.5 2.3-6.5 2.3L12 21l-2.3-6.4L3.2 12.3 9.7 10z" />,
  spinner: <path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5" />,
  cloud: <><path d="M7 18.5a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.6a4.5 4.5 0 0 1-.5 8.9z" /><path d="m9.5 13.5 2 2 3.5-4" /></>,
  folder: <path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4.2l2 2.2H19a1.5 1.5 0 0 1 1.5 1.5V17.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z" />,
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  text: <path d="M5 6.5V5h14v1.5M12 5v14M9 19h6" />,
  sign: <><path d="M3.5 17c2.5-6 4.5-8 5.5-5.5S7.5 18 9.7 16.5s2.3-5 4-4.5 0 4.5 2 4.5 2.3-1.5 4.3-3" /><path d="M4 20.5h16" /></>,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  reset: <><path d="M4 12a8 8 0 1 1 2.6 5.9" /><path d="M4 18.5v-5h5" /></>,
  rule: <><path d="M4.5 7h9M4.5 12h6M4.5 17h9" /><path d="m15.5 12.5 2 2 3.5-4" /></>,
  zoomin: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4M11 8.5v5M8.5 11h5" /></>,
  zoomout: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4M8.5 11h5" /></>,
  fit: <path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15" />,
  chevleft: <path d="m14.5 6-6 6 6 6" />,
  chevright: <path d="m9.5 6 6 6-6 6" />,
  chevup: <path d="m6 14.5 6-6 6 6" />,
  chevdown: <path d="m6 9.5 6 6 6-6" />,
  warning: <><path d="M12 4.2 21 19.5H3z" /><path d="M12 10v4.5M12 17.2v.1" /></>,
  scissors: <><circle cx="6.5" cy="7" r="2.5" /><circle cx="6.5" cy="17" r="2.5" /><path d="M8.7 8.4 20 17M8.7 15.6 20 7" /></>,
  dots: <><circle cx="6" cy="12" r="1.2" /><circle cx="12" cy="12" r="1.2" /><circle cx="18" cy="12" r="1.2" /></>,
  eraser: <><path d="m8 19-4.2-4.2a1.6 1.6 0 0 1 0-2.3l8.7-8.7a1.6 1.6 0 0 1 2.3 0l4.4 4.4a1.6 1.6 0 0 1 0 2.3L12 19z" /><path d="M8 19h11M8.7 8.9l6.4 6.4" /></>,
  wand: <><path d="m5 19 9.5-9.5" /><path d="m13 7 4 4M18.5 3.5l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z" /></>,
  scan: <><path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16" /><path d="M4 12h16" /></>,
  cloudoff: <><path d="M7 18.5a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 12 4.5" /><path d="M9 18.5h8.5a4 4 0 0 0 1.3-7.8M4 4l16 16" /></>,
  keyboard: <><rect x="3" y="6.5" width="18" height="11" rx="2.5" /><path d="M7 10.5h.1M11 10.5h.1M15 10.5h.1M7.5 14h9" /></>,
  bookmark: <path d="M7 4h10a1 1 0 0 1 1 1v15l-6-4-6 4V5a1 1 0 0 1 1-1z" />,
  pin: <path d="M12 21v-7M8 4h8l-1.4 6 2.4 4H7l2.4-4z" />,
  target: <><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="3.5" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3" /></>,
  bolt: <path d="M13 3 5 13.5h5.5L10 21l8-10.5h-5.5z" />,
  cloudup: <><path d="M7 18.5a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.6a4.5 4.5 0 0 1-.5 8.9" /><path d="M12 19v-6M9.5 15l2.5-2.5 2.5 2.5" /></>,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2.5" /><path d="M15.5 8.5V6A2 2 0 0 0 13.5 4H6A2 2 0 0 0 4 6v7.5a2 2 0 0 0 2 2h2.5" /></>,
  zip: <><path d="M4.5 7.5 12 3.5l7.5 4v9L12 20.5l-7.5-4z" /><path d="M12 11.5v9M4.5 7.5l7.5 4 7.5-4" /></>,
  searchoff: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4M8.5 8.5l5 5M13.5 8.5l-5 5" /></>,
  tag: <><path d="M4 12.5V5.5A1.5 1.5 0 0 1 5.5 4h7l8 8-8.5 8.5z" /><circle cx="8.5" cy="8.5" r="1.2" /></>,
  table: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M3.5 10h17M3.5 15h17M9.5 4.5v15" /></>,
  tune: <><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></>,
  arrowright: <path d="M5 12h14M13 6l6 6-6 6" />,
  folderopen: <path d="M3.5 18V7A1.5 1.5 0 0 1 5 5.5h4l2 2H19A1.5 1.5 0 0 1 20.5 9v1.5M3.5 18l2.2-6.5A1.5 1.5 0 0 1 7.1 10.5H21l-2.3 6.6A1.5 1.5 0 0 1 17.3 18z" />,
  user: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.6-3.6 3.3-5.5 7-5.5s6.4 1.9 7 5.5" /></>,
};

export default function Icon({ name, size = 20, className = "", strokeWidth = 2.2, spin = false, ...rest }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={`shrink-0 ${spin ? "animate-spin" : ""} ${className}`}
      {...rest}
    >
      {P[name] || null}
    </svg>
  );
}

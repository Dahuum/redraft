// 100% zoom = the whole page fits the board (width and height), leaving room for the dock.
export function fitWidth({ boxW, boxH, pages, pageIndex, zoom }) {
  const pg = pages && pages[pageIndex];
  const ratio = pg && pg.width && pg.height ? pg.width / pg.height : 0.707;
  const fit = Math.min((boxW || 640) - 48, ((boxH || 700) - 120) * ratio);
  return Math.max(260, Math.round(fit * zoom));
}

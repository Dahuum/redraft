/* Redraft tab icon: the sparkle tile, with a short twinkle now and then.
 * Plays on load, whenever you come back to the tab, and once every 12 seconds while it is
 * visible; the rest of the time it is still. Skipped for prefers-reduced-motion.
 * (Browsers draw the tab title in their own font, so only the icon can be animated.) */
(function () {
  var link = document.querySelector('link[rel~="icon"]');
  if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link); }
  link.type = 'image/svg+xml';

  function svg(rot, k) {
    var s = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 58 58">' +
      '<rect x="3" y="3" width="52" height="52" rx="15" fill="#8aa0ff" stroke="#2d2323" stroke-width="3.5"/>' +
      '<g transform="translate(29 29) rotate(' + rot + ') scale(' + k + ') translate(-29 -29)">' +
      '<path d="M29 13l3.6 10.4L43 27l-10.4 3.6L29 41l-3.6-10.4L15 27l10.4-3.6z" fill="#fff" stroke="#2d2323" stroke-width="3.2" stroke-linejoin="round"/></g></svg>';
    return 'data:image/svg+xml,' + encodeURIComponent(s);
  }
  var rest = svg(0, 1);
  link.href = rest;

  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var timer = null;
  function play() {
    if (timer || document.hidden) return;
    var f = 0, N = 14;
    timer = setInterval(function () {
      f++;
      if (f > N) { clearInterval(timer); timer = null; link.href = rest; return; }
      var t = f / N, e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;   // ease in-out
      var k = 1 + 0.32 * Math.sin(Math.PI * t);                                     // swell and settle
      link.href = svg(Math.round(e * 180), k.toFixed(3));                           // half turn (the star is 4-fold symmetric)
    }, 70);
  }
  play();
  document.addEventListener('visibilitychange', function () { if (!document.hidden) play(); });
  setInterval(play, 12000);
})();

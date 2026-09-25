/* Homerun-style landing behaviour: hero product tabs (yellow load bar), banner nav
 * dropdowns and phone drawer, resource tabs and the draggable guide slider. */
(function () {
  /* ── hero tabs ── */
  var shell = document.querySelector('[data-tabs]');
  if (shell) {
    var tabs = [].slice.call(shell.querySelectorAll('.hr-tab'));
    var imgs = [].slice.call(shell.querySelectorAll('.hr-shell__stage img'));
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var cur = 0;
    var show = function (n) {
      cur = (n + tabs.length) % tabs.length;
      tabs.forEach(function (t, i) {
        var on = i === cur;
        t.classList.toggle('is-on', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        var bar = t.querySelector('i');
        if (bar) { bar.style.display = 'none'; void bar.offsetWidth; bar.style.display = ''; }
      });
      imgs.forEach(function (im, i) {
        im.classList.toggle('is-on', i === cur);
        if (i === cur && im.loading === 'lazy') im.loading = 'eager';
      });
    };
    tabs.forEach(function (t, i) { t.addEventListener('click', function () { show(i); }); });
    if (!still) shell.addEventListener('animationend', function (e) { if (e.animationName === 'hr-load') show(cur + 1); });
  }

  /* ── nav: dropdowns (click, for touch and keyboard) + phone drawer ── */
  var links = document.getElementById('hrLinks');
  var burger = document.querySelector('.hr-burger');
  var dds = [].slice.call(document.querySelectorAll('.hr-dd'));
  dds.forEach(function (dd) {
    var b = dd.querySelector('.hr-nav__link');
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = !dd.classList.contains('is-open');
      dds.forEach(function (o) { o.classList.remove('is-open'); o.querySelector('.hr-nav__link').setAttribute('aria-expanded', 'false'); });
      dd.classList.toggle('is-open', open);
      b.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });
  document.addEventListener('click', function () {
    dds.forEach(function (o) { o.classList.remove('is-open'); o.querySelector('.hr-nav__link').setAttribute('aria-expanded', 'false'); });
  });
  if (burger && links) {
    burger.addEventListener('click', function (e) {
      e.stopPropagation();
      var open = links.classList.toggle('is-open');
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    links.addEventListener('click', function (e) {
      if (e.target.closest('a') || e.target.closest('[data-modal]')) {
        links.classList.remove('is-open'); burger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ── resources: tabs + drag-to-scroll ── */
  var slider = document.querySelector('[data-slider]');
  var seg = document.querySelector('.hr-seg');
  if (slider && seg) {
    seg.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-res]'); if (!b) return;
      [].forEach.call(seg.querySelectorAll('button'), function (x) { var on = x === b; x.classList.toggle('is-on', on); x.setAttribute('aria-selected', on ? 'true' : 'false'); });
      [].forEach.call(slider.querySelectorAll('.hr-track'), function (t) { t.classList.toggle('is-on', t.getAttribute('data-panel') === b.getAttribute('data-res')); });
      slider.scrollLeft = 0;
    });
    var down = false, sx = 0, sl = 0, moved = false;
    slider.addEventListener('pointerdown', function (e) { if (e.pointerType === 'touch') return; down = true; moved = false; sx = e.clientX; sl = slider.scrollLeft; });
    window.addEventListener('pointermove', function (e) {
      if (!down) return;
      var dx = e.clientX - sx; if (Math.abs(dx) > 4) { moved = true; slider.classList.add('is-drag'); }
      slider.scrollLeft = sl - dx;
    });
    window.addEventListener('pointerup', function () { down = false; slider.classList.remove('is-drag'); });
    slider.addEventListener('click', function (e) { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }, true);
  }
})();

/* ───────── motion ─────────
   Everything below is progressive enhancement: it only runs when the visitor has
   not asked for reduced motion, and the page reads fine without it. */
(function () {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var root = document.documentElement;
  root.classList.add('mo');

  /* 1. Scroll reveals: tag groups of elements, stagger them, reveal once in view. */
  var groups = [
    ['.hr-fame__t', 0], ['.hr-fame__logos li', 60],
    ['.hr-row__text > *', 80], ['.hr-row__img', 0, 'rv--img'],
    ['.hr-dark > .hr-eyebrow, .hr-dark > .hr-h2, .hr-dark > .hr-pill', 90], ['.hr-tpl', 110],
    ['.hr-res > .hr-eyebrow, .hr-res > .hr-h2, .hr-res > .hr-big, .hr-res > .hr-seg', 90],
    ['.hr-club > *:not(.hr-marq)', 90], ['.hr-marq', 0],
    ['.hr-gtile', 120],
    ['.pricing__header > *', 80], ['.pricing-card', 140],
    ['.hr-cta__icons svg', 130, 'rv--pop'], ['.hr-cta > *:not(.hr-cta__icons)', 90],
    ['.hr-foot__grid > div', 90]
  ];
  var els = [];
  groups.forEach(function (g) {
    [].forEach.call(document.querySelectorAll(g[0]), function (el, i) {
      // stagger only within a run of siblings that reveal together
      el.style.setProperty('--d', (i % 6) * g[1] + 'ms');
      el.classList.add('rv');
      if (g[2]) el.classList.add(g[2]);
      els.push(el);
    });
  });

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      var el = e.target; io.unobserve(el);
      el.classList.add('rv-in');
      // hand the element back to its own hover transitions once it has landed
      setTimeout(function () { el.classList.remove('rv', 'rv-in', 'rv--img', 'rv--pop'); el.style.removeProperty('--d'); }, 1500);
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
  els.forEach(function (el) { io.observe(el); });

  /* Slider cards sit partly off-screen to the right, so reveal them together when the slider arrives. */
  var slider = document.querySelector('[data-slider]');
  if (slider) {
    var cards = [].slice.call(slider.querySelectorAll('.hr-gcard'));
    cards.forEach(function (c, i) { c.style.setProperty('--d', (i % 4) * 110 + 'ms'); c.classList.add('rv'); });
    var sio = new IntersectionObserver(function (es) {
      if (!es[0].isIntersecting) return; sio.disconnect();
      cards.forEach(function (c) { c.classList.add('rv-in'); });
      setTimeout(function () { cards.forEach(function (c) { c.classList.remove('rv', 'rv-in'); c.style.removeProperty('--d'); }); }, 1600);
    }, { threshold: 0.2 });
    sio.observe(slider);
  }

  /* 2. Count-up for the stat numbers. */
  [].forEach.call(document.querySelectorAll('.hr-num'), function (n) {
    var to = parseInt(n.textContent, 10);
    if (!to) return;
    n.textContent = '0';
    var seen = new IntersectionObserver(function (es) {
      if (!es[0].isIntersecting) return; seen.disconnect();
      var t0 = null;
      (function step(t) {
        if (t0 === null) t0 = t;
        var k = Math.min(1, (t - t0) / 1100), eased = 1 - Math.pow(1 - k, 3);
        n.textContent = String(Math.round(to * eased));
        if (k < 1) requestAnimationFrame(step);
      })(performance.now());
    }, { threshold: 0.6 });
    seen.observe(n);
  });
  [].forEach.call(document.querySelectorAll('.hr-dots'), function (d) {
    var seen = new IntersectionObserver(function (es) { if (es[0].isIntersecting) { d.classList.add('is-in'); seen.disconnect(); } }, { threshold: 0.6 });
    seen.observe(d);
  });

  /* 3. Scroll-linked: the product frame tilts flat as it arrives, row images drift, nav compacts. */
  var shell = document.querySelector('.hr-shell');
  var imgs = [].slice.call(document.querySelectorAll('.hr-row__img img'));
  var nav = document.getElementById('hrNav');
  var ticking = false;
  function clamp(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function frame() {
    ticking = false;
    var vh = window.innerHeight;
    if (shell) {
      var r = shell.getBoundingClientRect();
      var p = clamp((vh - r.top) / (vh * 0.95));
      shell.style.transform = 'perspective(1600px) rotateX(' + ((1 - p) * 9).toFixed(2) + 'deg) scale(' + (0.9 + 0.1 * p).toFixed(3) + ')';
      shell.style.transformOrigin = '50% 0%';
    }
    imgs.forEach(function (im) {
      var b = im.parentNode.getBoundingClientRect();
      if (b.bottom < -80 || b.top > vh + 80) return;
      var q = (b.top + b.height / 2 - vh / 2) / vh;
      im.style.transform = 'translateY(' + (q * -34).toFixed(1) + 'px)';
    });
    if (nav) nav.classList.toggle('is-compact', window.scrollY > 40);
  }
  function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(frame); } }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  frame();
})();

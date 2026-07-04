/* ──────────────────────────────────────────────────────────────
 * Redraft landing — animation runtime
 * ────────────────────────────────────────────────────────────── */

// ────── Theme toggle (light / dark) ──────
(function initTheme() {
  const root   = document.documentElement;
  const btn    = document.getElementById('theme-toggle');
  const STORE  = 'redraft-theme';

  const saved = localStorage.getItem(STORE);
  if (saved === 'light') root.setAttribute('data-theme', 'light');

  btn?.addEventListener('click', () => {
    const isLight = root.getAttribute('data-theme') === 'light';
    const next    = isLight ? 'dark' : 'light';

    root.classList.add('theme-transitioning');
    if (next === 'light') {
      root.setAttribute('data-theme', 'light');
    } else {
      root.removeAttribute('data-theme');
    }
    localStorage.setItem(STORE, next);

    btn.setAttribute('aria-label',
      next === 'light' ? 'Switch to dark mode' : 'Switch to light mode');

    setTimeout(() => root.classList.remove('theme-transitioning'), 320);
  });

  if (btn) {
    const current = root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    btn.setAttribute('aria-label',
      current === 'light' ? 'Switch to dark mode' : 'Switch to light mode');
  }
})();

// ────── Pricing billing toggle ──────
(function initPricingToggle() {
  const btnMonthly = document.getElementById('btn-monthly');
  const btnYearly  = document.getElementById('btn-yearly');
  const priceEl    = document.getElementById('pro-price');
  const billedEl   = document.getElementById('pro-billed');
  if (!btnMonthly || !btnYearly) return;

  function setPeriod(period) {
    const yearly = period === 'yearly';
    btnMonthly.classList.toggle('pricing__period--active', !yearly);
    btnYearly.classList.toggle('pricing__period--active',  yearly);
    if (!priceEl) return;
    priceEl.style.opacity = '0';
    setTimeout(() => {
      priceEl.textContent = yearly ? '2.79' : '3.99';
      if (billedEl) {
        billedEl.textContent = yearly
          ? 'Billed $33.48 / year  —  save $14.40'
          : 'Billed monthly';
      }
      priceEl.style.opacity = '1';
    }, 150);
  }

  btnMonthly.addEventListener('click', () => setPeriod('monthly'));
  btnYearly.addEventListener('click',  () => setPeriod('yearly'));
})();

// ────── Easing library ──────
const Easing = {
  linear:  (t) => t,
  sine:    (t) => 0.5 - 0.5 * Math.cos(Math.PI * t),
  inOutCubic: (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2,
  outCubic: (t) => 1 - Math.pow(1 - t, 3),
  inOutQuad: (t) => t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2,
};

const TAU = Math.PI * 2;
const lerp = (a, b, t) => a + (b - a) * t;

// ────── Mobile menu + scroll shadow ──────
const burger = document.querySelector('.nav__burger');
const links  = document.querySelector('.nav__links');
burger?.addEventListener('click', () => {
  const open = links.classList.toggle('nav__links--open');
  burger.setAttribute('aria-expanded', open);
});
const navWrap = document.querySelector('.nav-wrap');
const onScroll = () => navWrap.classList.toggle('nav-wrap--scrolled', window.scrollY > 8);
onScroll();
window.addEventListener('scroll', onScroll, { passive: true });

/* 1. Floating doc-card animator */
class CardSprite {
  constructor(el, seed) {
    this.el = el;
    this.baseRot = parseFloat(el.style.getPropertyValue('--rot') || '0');
    this.seed = seed;
    this.visible = true;
  }
  update(t) {
    if (!this.visible) return;
    const s = this.seed;
    const ny = Math.sin(t * s.freqY * TAU + s.phase) * 0.5 + 0.5;
    const nx = Math.sin(t * s.freqX * TAU + s.phase * 1.7) * 0.5 + 0.5;
    const nr = Math.sin(t * s.swayFreq * TAU + s.phase * 0.6);
    const ey = (s.ease(ny) - 0.5) * 2;
    const ex = (s.ease(nx) - 0.5) * 2;

    const dy = ey * s.ampY;
    const dx = ex * s.ampX;
    const dr = this.baseRot + nr * s.swayDeg;

    this.el.style.transform =
      `translate3d(${dx.toFixed(2)}px, ${dy.toFixed(2)}px, 0) rotate(${dr.toFixed(2)}deg)`;
  }
}

const floatingRoot = document.querySelector('.floating');
const cardEls = floatingRoot ? Array.from(floatingRoot.querySelectorAll('.doc-card')) : [];

const seeds = [
  { ampY: 9,  ampX: 2.5, freqY: 0.14, freqX: 0.07, phase: 0.2,  swayDeg: 1.2, swayFreq: 0.10, ease: Easing.sine },
  { ampY: 7,  ampX: 4.0, freqY: 0.18, freqX: 0.09, phase: 1.4,  swayDeg: 1.5, swayFreq: 0.13, ease: Easing.inOutCubic },
  { ampY: 11, ampX: 3.0, freqY: 0.11, freqX: 0.06, phase: 2.7,  swayDeg: 1.0, swayFreq: 0.08, ease: Easing.inOutQuad },
  { ampY: 6,  ampX: 5.0, freqY: 0.20, freqX: 0.10, phase: 3.9,  swayDeg: 2.0, swayFreq: 0.15, ease: Easing.outCubic },
  { ampY: 8,  ampX: 3.5, freqY: 0.16, freqX: 0.08, phase: 0.7,  swayDeg: 1.3, swayFreq: 0.11, ease: Easing.sine },
  { ampY: 10, ampX: 2.0, freqY: 0.12, freqX: 0.05, phase: 2.1,  swayDeg: 0.8, swayFreq: 0.09, ease: Easing.inOutCubic },
  { ampY: 7.5,ampX: 4.5, freqY: 0.17, freqX: 0.085,phase: 4.6,  swayDeg: 1.7, swayFreq: 0.14, ease: Easing.inOutQuad },
  { ampY: 9,  ampX: 3.0, freqY: 0.13, freqX: 0.065,phase: 5.2,  swayDeg: 1.1, swayFreq: 0.10, ease: Easing.outCubic },
];

const sprites = cardEls.map((el, i) => new CardSprite(el, seeds[i % seeds.length]));

let heroInView = true;
const heroEl = document.querySelector('.hero');
if (heroEl && 'IntersectionObserver' in window) {
  new IntersectionObserver(
    ([entry]) => { heroInView = entry.isIntersecting; },
    { rootMargin: '100px' }
  ).observe(heroEl);
}

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let startMs = performance.now();
function tick(now) {
  const t = (now - startMs) / 1000;
  if (heroInView && !reducedMotion) {
    for (let i = 0; i < sprites.length; i++) sprites[i].update(t);
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

/* 2. Scroll-driven WORD-by-WORD text reveal for `.reveal-text` */
const revealEls = Array.from(document.querySelectorAll('.reveal-text'));
const clamp01 = (v) => v < 0 ? 0 : (v > 1 ? 1 : v);

function buildWords(el) {
  if (!el.dataset.original) {
    el.dataset.original = el.textContent.trim().replace(/\s+/g, ' ');
  }
  const original = el.dataset.original;

  el.textContent = '';
  const tokens = original.split(/(\s+)/);
  const wordSpans = [];
  for (const tok of tokens) {
    if (!tok) continue;
    if (/^\s+$/.test(tok)) {
      el.appendChild(document.createTextNode(tok));
    } else {
      const s = document.createElement('span');
      s.className = 'reveal-word';
      s.textContent = tok;
      el.appendChild(s);
      wordSpans.push(s);
    }
  }
  el._revealWords = wordSpans;
}

function updateReveal(el) {
  const words = el._revealWords;
  if (!words || !words.length) return;

  const vh   = window.innerHeight || document.documentElement.clientHeight;
  const rect = el.getBoundingClientRect();
  const top  = rect.top;

  const startY   = vh * 0.90;
  const distance = vh * 0.45 + rect.height * 0.5;
  const progress = clamp01((startY - top) / distance);

  const N = words.length;
  const slice = 1 / N;
  const overlap = slice * 0.15;

  for (let i = 0; i < N; i++) {
    const start = i * slice - overlap;
    const end   = (i + 1) * slice + overlap;
    const local = clamp01((progress - start) / (end - start));
    const pct   = (local * 100).toFixed(2) + '%';
    words[i].style.setProperty('--reveal', pct);
  }
}

let resizeTO;
function rebuildAndUpdate() {
  for (const el of revealEls) {
    buildWords(el);
    updateReveal(el);
  }
}

if (revealEls.length) {
  rebuildAndUpdate();

  window.addEventListener('scroll', () => {
    for (const el of revealEls) updateReveal(el);
  }, { passive: true });

  window.addEventListener('resize', () => {
    clearTimeout(resizeTO);
    resizeTO = setTimeout(rebuildAndUpdate, 120);
  });

  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(rebuildAndUpdate);
  }
}

/* 3. Scroll-reveal entrance animations */
(function initScrollReveal() {
  if (!('IntersectionObserver' in window)) return;

  const groups = [
    { sel: '.showcase',             delay: 0                           },
    { sel: '.bento .card',          stagger: 110                       },
    { sel: '.pricing__header',      delay: 0                           },
    { sel: '.pricing__toggle',      delay: 80                          },
    { sel: '.pricing-card',         stagger: 100                       },
    { sel: '.pricing__footnote',    delay: 60                          },
    { sel: '.footer__brand',        delay: 0                           },
    { sel: '.footer__col',          stagger: 70                        },
  ];

  groups.forEach(({ sel, stagger = 0, delay = 0, variant = '' }) => {
    document.querySelectorAll(sel).forEach((el, i) => {
      el.classList.add('sr');
      if (variant) el.classList.add(variant);
      const d = delay + i * stagger;
      if (d) el.style.setProperty('--sr-delay', d + 'ms');
    });
  });

  const io = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('in');
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.10, rootMargin: '0px 0px -40px 0px' });

  document.querySelectorAll('.sr').forEach(el => io.observe(el));
})();

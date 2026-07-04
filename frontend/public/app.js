/* app.js — landing interactions: theme toggle, pricing toggle,
 * sticky nav, mobile menu, scroll-reveal. Framework-free. */
(function () {
  var root = document.documentElement;

  /* Theme (dark default; persisted) */
  try {
    if (localStorage.getItem('redraft-theme') === 'light') root.setAttribute('data-theme', 'light');
  } catch (e) {}
  function toggleTheme() {
    var light = root.getAttribute('data-theme') === 'light';
    root.classList.add('theme-transitioning');
    if (light) { root.removeAttribute('data-theme'); }
    else { root.setAttribute('data-theme', 'light'); }
    try { localStorage.setItem('redraft-theme', light ? 'dark' : 'light'); } catch (e) {}
    setTimeout(function () { root.classList.remove('theme-transitioning'); }, 320);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var tt = document.getElementById('theme-toggle');
    if (tt) tt.addEventListener('click', toggleTheme);

    /* Sticky nav shadow on scroll */
    var navWrap = document.querySelector('.nav-wrap');
    function onScroll() {
      if (!navWrap) return;
      navWrap.classList.toggle('nav-wrap--scrolled', window.scrollY > 12);
    }
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    /* Mobile menu */
    var burger = document.querySelector('.nav__burger');
    var links = document.querySelector('.nav__links');
    if (burger && links) {
      burger.addEventListener('click', function () {
        var open = links.classList.toggle('nav__links--open');
        burger.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      links.querySelectorAll('a').forEach(function (a) {
        a.addEventListener('click', function () { links.classList.remove('nav__links--open'); });
      });
    }

    /* Pricing monthly / yearly */
    var mo = document.getElementById('btn-monthly');
    var yr = document.getElementById('btn-yearly');
    var price = document.getElementById('pro-price');
    var billed = document.getElementById('pro-billed');
    function setPeriod(yearly) {
      if (mo) mo.classList.toggle('pricing__period--active', !yearly);
      if (yr) yr.classList.toggle('pricing__period--active', yearly);
      if (price) price.textContent = yearly ? '2.79' : '3.99';
      if (billed) billed.textContent = yearly ? 'Billed yearly' : 'Billed monthly';
    }
    if (mo) mo.addEventListener('click', function () { setPeriod(false); });
    if (yr) yr.addEventListener('click', function () { setPeriod(true); });

    /* Scroll-reveal */
    var srs = document.querySelectorAll('.sr');
    if ('IntersectionObserver' in window && srs.length) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
        });
      }, { threshold: 0.12 });
      srs.forEach(function (el) { io.observe(el); });
    } else {
      srs.forEach(function (el) { el.classList.add('in'); });
    }
  });
})();

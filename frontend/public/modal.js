/* ──────────────────────────────────────────────────────────────
 * modal.js — Auth modal controller, wired to Supabase Auth.
 * Loads @supabase/supabase-js (UMD) from CDN; config comes from
 * window.REDRAFT_SB = { url, anon } (set inline in index.html).
 * On successful sign-in the user is sent to /app (the React tool).
 * ────────────────────────────────────────────────────────────── */

(function () {
  var overlay   = document.getElementById('auth-overlay');
  var modalSI   = document.getElementById('modal-signin');
  var modalSU   = document.getElementById('modal-signup');
  var activeModal = null;

  var APP_URL = '/app';

  /* ── Supabase client ── */
  var sb = null;
  function getSb() {
    if (sb) return sb;
    var cfg = window.REDRAFT_SB || {};
    if (window.supabase && cfg.url && cfg.anon) {
      sb = window.supabase.createClient(cfg.url, cfg.anon);
    }
    return sb;
  }

  /* ── Open / close ── */
  function openModal(which) {
    overlay.style.display = 'flex';
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { overlay.classList.add('is-open'); });
    });
    if (which === 'signup') {
      modalSI.style.display = 'none';
      modalSU.style.display = 'block';
      activeModal = modalSU;
    } else {
      modalSU.style.display = 'none';
      modalSI.style.display = 'block';
      activeModal = modalSI;
    }
    document.body.style.overflow = 'hidden';
    overlay.removeAttribute('aria-hidden');
  }

  function closeModal() {
    overlay.classList.remove('is-open');
    document.body.style.overflow = '';
    overlay.setAttribute('aria-hidden', 'true');
    setTimeout(function () {
      overlay.style.display = 'none';
      goToStep(modalSI, 'login');
      goToStep(modalSU, 'signup');
    }, 280);
  }

  function goToStep(modal, stepName) {
    modal.querySelectorAll('.modal-step').forEach(function (s) {
      s.hidden = (s.dataset.step !== stepName);
    });
  }

  window.openModal  = openModal;
  window.closeModal = closeModal;

  /* ── Password eye toggle ── */
  window.authTogglePw = function (btn) {
    var input = btn.parentElement.querySelector('input');
    var hide  = input.type === 'password';
    input.type = hide ? 'text' : 'password';
    btn.setAttribute('aria-label', hide ? 'Hide password' : 'Show password');
    btn.querySelector('svg').style.opacity = hide ? '0.45' : '1';
  };

  /* ── Resend verification email ── */
  window.authResend = function (btn) {
    var email = (document.getElementById('su-email') || {}).value;
    var client = getSb();
    if (client && email) {
      client.auth.resend({ type: 'signup', email: email,
        options: { emailRedirectTo: window.location.origin + APP_URL } });
    }
    btn.disabled = true;
    btn.style.opacity = '0.5';
    btn.textContent = 'Email sent';
    setTimeout(function () {
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.textContent = 'Resend again';
    }, 30000);
  };

  /* ── Inline error under a form ── */
  function showError(form, msg) {
    var box = form.querySelector('.auth-error');
    if (!box) {
      box = document.createElement('p');
      box.className = 'auth-error';
      box.style.cssText = 'color:#f87171;font-size:13px;margin:4px 0 0;line-height:1.4;';
      form.insertBefore(box, form.querySelector('.auth-submit'));
    }
    box.textContent = msg || 'Something went wrong. Try again.';
  }
  function clearError(form) {
    var box = form.querySelector('.auth-error');
    if (box) box.textContent = '';
  }

  /* ── Submit with spinner; `handler` returns a Promise ── */
  function bindForm(formId, handler) {
    var form = document.getElementById(formId);
    if (!form) return;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var btn = form.querySelector('.auth-submit');
      if (btn.dataset.loading) return;
      clearError(form);
      var orig = btn.innerHTML;
      var label = btn.textContent.trim();
      btn.dataset.loading = '1';
      btn.disabled = true;
      btn.innerHTML = '<span class="auth-spinner"></span><span>' + label + '</span>';
      Promise.resolve()
        .then(function () { return handler(form); })
        .catch(function (err) { showError(form, err && err.message); })
        .finally(function () {
          btn.innerHTML = orig;
          btn.disabled = false;
          delete btn.dataset.loading;
        });
    });
  }

  function val(id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; }
  function goApp() { window.location.href = APP_URL; }

  /* ── Wire everything once DOM ready ── */
  document.addEventListener('DOMContentLoaded', function () {
    var client = getSb();

    // If already signed in, turn the nav CTAs into "Open app".
    if (client) {
      client.auth.getSession().then(function (res) {
        if (res && res.data && res.data.session) {
          document.querySelectorAll('[data-modal]').forEach(function (el) {
            el.textContent = 'Open app';
            el.dataset.modal = '';
            el.addEventListener('click', function (e) { e.preventDefault(); goApp(); });
          });
        }
      });
    }

    var modalToggle = document.getElementById('modal-theme-toggle');
    if (modalToggle) {
      modalToggle.addEventListener('click', function () {
        var t = document.getElementById('theme-toggle');
        if (t) t.click();
      });
    }

    document.querySelectorAll('[data-modal]').forEach(function (el) {
      el.addEventListener('click', function (e) {
        e.preventDefault();
        if (el.dataset.modal) openModal(el.dataset.modal);
      });
    });
    document.querySelectorAll('[data-open-modal]').forEach(function (el) {
      el.addEventListener('click', function () { openModal(el.dataset.openModal); });
    });
    document.querySelectorAll('[data-goto]').forEach(function (el) {
      el.addEventListener('click', function () {
        goToStep(el.closest('.auth-modal'), el.dataset.goto);
      });
    });
    document.querySelectorAll('.auth-modal__close').forEach(function (btn) {
      btn.addEventListener('click', closeModal);
    });
    overlay.addEventListener('click', function (e) { if (e.target === overlay) closeModal(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && overlay.classList.contains('is-open')) closeModal();
    });

    // Google (both modals)
    document.querySelectorAll('.auth-google').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var c = getSb();
        if (!c) return;
        btn.disabled = true;
        c.auth.signInWithOAuth({ provider: 'google',
          options: { redirectTo: window.location.origin + APP_URL } })
          .then(function (r) {
            // On success the browser redirects to Google; only surface errors.
            if (r && r.error) {
              var step = btn.closest('.modal-step') || btn.parentElement;
              var box = step.querySelector('.auth-error');
              if (!box) {
                box = document.createElement('p');
                box.className = 'auth-error';
                box.style.cssText = 'color:#f87171;font-size:13px;margin:10px 0 0;text-align:center;line-height:1.4;';
                step.appendChild(box);
              }
              box.textContent = (r.error.message || 'Google sign-in is not enabled yet.') +
                ' Use email for now.';
            }
          })
          .finally(function () { btn.disabled = false; });
      });
    });

    // Sign in
    bindForm('form-signin', function (form) {
      var c = getSb();
      if (!c) throw new Error('Auth is not configured.');
      return c.auth.signInWithPassword({ email: val('si-email'), password: val('si-password') })
        .then(function (r) { if (r.error) throw r.error; goApp(); });
    });

    // Sign up → verify step (or straight to app if email confirmation is off)
    bindForm('form-signup', function (form) {
      var c = getSb();
      if (!c) throw new Error('Auth is not configured.');
      return c.auth.signUp({
        email: val('su-email'), password: val('su-password'),
        options: { data: { full_name: val('su-name') },
                   emailRedirectTo: window.location.origin + APP_URL },
      }).then(function (r) {
        if (r.error) throw r.error;
        if (r.data && r.data.session) goApp();     // confirmation disabled
        else goToStep(modalSU, 'verify');          // confirmation email sent
      });
    });

    // Forgot password
    bindForm('form-forgot', function (form) {
      var c = getSb();
      if (!c) throw new Error('Auth is not configured.');
      return c.auth.resetPasswordForEmail(val('fp-email'),
        { redirectTo: window.location.origin + APP_URL })
        .then(function (r) { if (r.error) throw r.error; goToStep(modalSI, 'forgot-sent'); });
    });
  });
})();

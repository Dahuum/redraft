/* ──────────────────────────────────────────────────────────────
 * analytics.js — Redraft product analytics (PostHog).
 *
 * Shared by BOTH entry points: the marketing landing (index.html) and the
 * React app (app.html). It exposes two globals used everywhere:
 *
 *     window.rdTrack(event, props)     — record a funnel event
 *     window.rdIdentify(id, props)     — tie events to a signed-in user
 *
 * Both are ALWAYS defined and ALWAYS safe: if PostHog isn't configured (no real
 * key in window.REDRAFT_PH) or fails to load, every call is a silent no-op, so
 * the product never breaks whether or not analytics is turned on.
 *
 * Privacy (deliberate): autocapture and session recording are OFF. Redraft
 * documents can hold sensitive data (invoices, contracts) — we record ONLY the
 * explicit funnel events below, never document content, inputs, or keystrokes.
 * That keeps the "processed in memory, never stored" promise honest.
 *
 * Funnel events (see WS0):
 *   landing_view · guest_start · compose_submit · doc_opened · edit_preview ·
 *   download_final (ACTIVATION) · signup · bulk_generate · annex_generate ·
 *   template_saved · footer_referral
 * ────────────────────────────────────────────────────────────── */
(function () {
  var cfg = window.REDRAFT_PH || {};
  var key = cfg.key || "";
  // A real PostHog project key starts with "phc_". The committed placeholder
  // does not, so nothing is sent until you paste the real key.
  var configured = key.indexOf("phc_") === 0;

  if (configured) {
    // Official PostHog loader snippet (queues calls until the SDK loads).
    !(function (t, e) {
      var o, n, p, r;
      e.__SV ||
        ((window.posthog = e),
        (e._i = []),
        (e.init = function (i, s, a) {
          function g(t, e) {
            var o = e.split(".");
            2 == o.length && ((t = t[o[0]]), (e = o[1])),
              (t[e] = function () {
                t.push([e].concat(Array.prototype.slice.call(arguments, 0)));
              });
          }
          ((p = t.createElement("script")).type = "text/javascript"),
            (p.crossOrigin = "anonymous"),
            (p.async = !0),
            (p.src =
              (s.api_host || "https://us.i.posthog.com").replace(
                ".i.posthog.com",
                "-assets.i.posthog.com"
              ) + "/static/array.js"),
            (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(p, r);
          var u = e;
          for (
            void 0 !== a ? (u = e[a] = []) : (a = "posthog"),
              u.people = u.people || [],
              u.toString = function (t) {
                var e = "posthog";
                return "posthog" !== a && (e += "." + a), t || (e += " (stub)"), e;
              },
              u.people.toString = function () {
                return u.toString(1) + ".people (stub)";
              },
              o =
                "init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug getPageViewId".split(
                  " "
                ),
              n = 0;
            n < o.length;
            n++
          )
            g(u, o[n]);
          e._i.push([i, s, a]);
        }),
        (e.__SV = 1));
    })(document, window.posthog || []);

    try {
      window.posthog.init(key, {
        api_host: cfg.host || "https://us.i.posthog.com",
        capture_pageview: true,
        autocapture: false, // never scrape document DOM / inputs
        disable_session_recording: true, // never record the screen
        persistence: "localStorage+cookie",
      });
    } catch (e) {
      /* analytics must never break the page */
    }
  }

  // Public, always-safe API. No-ops when analytics isn't configured/loaded.
  window.rdTrack = function (event, props) {
    try {
      window.posthog &&
        window.posthog.capture &&
        window.posthog.capture(event, props || {});
    } catch (e) {}
  };
  window.rdIdentify = function (id, props) {
    try {
      id &&
        window.posthog &&
        window.posthog.identify &&
        window.posthog.identify(String(id), props || {});
    } catch (e) {}
  };

  // Loop A (WS2) prep: a click from a "Made with Redraft" PDF footer arrives
  // with ?utm_source=redraft-pdf — record it so footer referrals show up in the
  // funnel the moment that loop ships. Harmless until then.
  try {
    var qs = new URLSearchParams(window.location.search);
    if (qs.get("utm_source") === "redraft-pdf")
      window.rdTrack("footer_referral", { medium: qs.get("utm_medium") || "" });
  } catch (e) {}
})();

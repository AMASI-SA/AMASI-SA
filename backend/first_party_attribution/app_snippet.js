/* Mezan First-Party Attribution v1
 * Salla App Snippet (Body). No secrets, payment data, raw email or raw phone.
 */
(function mezanFirstPartyAttribution() {
  "use strict";

  if (window.__mezanFirstPartyAttributionV1) return;
  window.__mezanFirstPartyAttributionV1 = true;

  var ENDPOINT = "https://mezansalla.com/api/first-party-attribution/v1/events";
  var VISITOR_KEY = "mz_visitor_id_v1";
  var SESSION_KEY = "mz_session_v1";
  var TOUCH_KEY = "mz_touch_v1";
  var TOUCH_MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000;
  var SESSION_MAX_IDLE_MS = 30 * 60 * 1000;
  // Pilot safety gate: saving an App Snippet publishes it to every store that
  // installed the app. Keep collection disabled outside the demo store until
  // the end-to-end attribution test is approved.
  var PILOT_STORE_NAME = "Mezan Attribution Test";

  function randomId(prefix) {
    var value = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    return prefix + "-" + value;
  }

  function safeGet(key) {
    try { return window.localStorage.getItem(key); } catch (_) { return null; }
  }

  function safeSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (_) { /* no-op */ }
  }

  function readJson(key) {
    try { return JSON.parse(safeGet(key) || "null"); } catch (_) { return null; }
  }

  function visitorId() {
    var current = safeGet(VISITOR_KEY);
    if (current) return current;
    current = randomId("mv");
    safeSet(VISITOR_KEY, current);
    try {
      document.cookie = VISITOR_KEY + "=" + encodeURIComponent(current)
        + "; Max-Age=" + (90 * 24 * 60 * 60) + "; Path=/; SameSite=Lax; Secure";
    } catch (_) { /* no-op */ }
    return current;
  }

  function sessionId() {
    var now = Date.now();
    var current = readJson(SESSION_KEY);
    if (!current || !current.id || now - Number(current.touched_at || 0) > SESSION_MAX_IDLE_MS) {
      current = { id: randomId("ms"), touched_at: now };
    } else {
      current.touched_at = now;
    }
    safeSet(SESSION_KEY, JSON.stringify(current));
    return current.id;
  }

  function clean(value, maximum) {
    if (value === null || value === undefined || typeof value === "object") return null;
    var text = String(value).trim();
    return text ? text.slice(0, maximum || 160) : null;
  }

  function queryTouch() {
    var query = new URLSearchParams(window.location.search || "");
    var source = clean(query.get("mz_source") || query.get("utm_source"), 40);
    var token = clean(query.get("mzt"), 3000);
    if (!source && document.referrer) {
      try {
        var referrerHost = new URL(document.referrer).hostname.toLowerCase();
        if (referrerHost === "google.com" || referrerHost.endsWith(".google.com")) {
          source = "google_organic";
        }
      } catch (_) { /* no-op */ }
    }
    if (!source && !token) return null;
    return {
      captured_at: Date.now(),
      link_token: token,
      source: source || "direct",
      medium: clean(query.get("utm_medium"), 80),
      campaign_id: clean(query.get("mz_campaign_id") || query.get("utm_campaign"), 160),
      ad_group_id: clean(query.get("mz_ad_squad_id"), 160),
      ad_id: clean(query.get("mz_ad_id") || query.get("utm_content"), 160),
      creative_id: clean(query.get("mz_creative_id"), 160)
    };
  }

  function activeTouch() {
    var incoming = queryTouch();
    if (incoming) {
      safeSet(TOUCH_KEY, JSON.stringify(incoming));
      return incoming;
    }
    var stored = readJson(TOUCH_KEY);
    if (!stored || Date.now() - Number(stored.captured_at || 0) > TOUCH_MAX_AGE_MS) return {};
    return stored;
  }

  function deepFirst(value, keys, depth) {
    if (!value || depth > 6) return null;
    if (Array.isArray(value)) {
      for (var index = 0; index < Math.min(value.length, 30); index += 1) {
        var arrayResult = deepFirst(value[index], keys, depth + 1);
        if (arrayResult !== null) return arrayResult;
      }
      return null;
    }
    if (typeof value !== "object") return null;
    var names = Object.keys(value);
    for (var i = 0; i < names.length; i += 1) {
      if (keys.indexOf(names[i].toLowerCase()) !== -1) {
        var direct = clean(value[names[i]], 500);
        if (direct !== null) return direct;
      }
    }
    for (var j = 0; j < names.length; j += 1) {
      var nested = deepFirst(value[names[j]], keys, depth + 1);
      if (nested !== null) return nested;
    }
    return null;
  }

  async function sha256(value) {
    if (!value || !window.crypto || !window.crypto.subtle || !window.TextEncoder) {
      return null;
    }
    try {
      var buffer = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
      return Array.prototype.map.call(new Uint8Array(buffer), function (byte) {
        return byte.toString(16).padStart(2, "0");
      }).join("");
    } catch (_) {
      return null;
    }
  }

  async function identityHashes(payload) {
    var email = deepFirst(payload, ["email"], 0);
    var phone = deepFirst(payload, ["phone", "mobile", "mobile_number"], 0);
    var tasks = [];
    if (email) tasks.push(sha256("email:" + email.trim().toLowerCase()));
    if (phone) tasks.push(sha256("phone:" + phone.replace(/\D/g, "").replace(/^00/, "")));
    var rows = await Promise.all(tasks);
    return rows.filter(Boolean).slice(0, 8);
  }

  function storeConfig(key) {
    try {
      return window.salla && window.salla.config
        ? window.salla.config.get(key)
        : null;
    } catch (_) { return null; }
  }

  function storeId() {
    return clean(storeConfig("store.id"), 160);
  }

  function isPilotStore() {
    return clean(storeConfig("store.name"), 160) === PILOT_STORE_NAME;
  }

  function normalizeEventName(eventName) {
    var compact = String(eventName || "").toLowerCase().replace(/[\s._-]+/g, "");
    return ({
      productviewed: "view_item",
      productadded: "add_to_cart",
      productremoved: "remove_from_cart",
      cartviewed: "view_cart",
      cartupdated: "view_cart",
      checkoutstarted: "begin_checkout",
      ordercompleted: "purchase"
    })[compact] || null;
  }

  async function send(eventName, payload) {
    if (!eventName) return;
    var touch = activeTouch();
    var hashes = await identityHashes(payload || {});
    var body = {
        event_id: randomId("me"),
        visitor_id: visitorId(),
        session_id: sessionId(),
        event_name: eventName,
        occurred_at: new Date().toISOString(),
        store_id: storeId(),
        link_token: touch.link_token || null,
        source: touch.source || "direct",
        medium: touch.medium || null,
        campaign_id: touch.campaign_id || null,
        ad_group_id: touch.ad_group_id || null,
        ad_id: touch.ad_id || null,
        creative_id: touch.creative_id || null,
        product_id: deepFirst(payload, ["product_id", "item_id"], 0),
        cart_id: deepFirst(payload, ["cart_id"], 0),
        customer_id: deepFirst(payload, ["customer_id"], 0),
        order_number: deepFirst(payload, ["order_number", "reference_id"], 0),
        identity_hashes: hashes,
        page_url: window.location.href.slice(0, 3000),
        referrer: (document.referrer || "").slice(0, 3000)
    };
    try {
      await fetch(ENDPOINT, {
        method: "POST",
        mode: "cors",
        credentials: "omit",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
    } catch (_) {
      /* Attribution never blocks the storefront. */
    }
  }

  function register() {
    var Salla = window.Salla || window.salla;
    if (!Salla || !Salla.analytics || typeof Salla.analytics.registerTracker !== "function") return;
    if (!isPilotStore()) return;
    Salla.analytics.registerTracker({
      name: "MezanFirstPartyAttributionV1",
      track: function (eventName, payload) {
        send(normalizeEventName(eventName), payload || {});
      },
      page: function (payload) {
        send("page_view", payload || {});
      }
    });
  }

  var sdk = window.Salla || window.salla;
  if (sdk && typeof sdk.onReady === "function") {
    sdk.onReady(register);
  } else {
    window.addEventListener("load", register, { once: true });
  }
}());

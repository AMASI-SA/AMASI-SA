const FAILURE_STORAGE_KEY = "mezan-spa-runtime-failures-v2";
const RECOVERY_STORAGE_PREFIX = "mezan-spa-auto-recovery-v2:";
const RECOVERY_COOLDOWN_MS = 15 * 60 * 1000;
const MAX_FAILURES = 12;
const LOCATION_CHANGE_EVENT = "mezan:spa-location-change";

let installed = false;
let rootObserver = null;
let healthTimer = 0;
let reloadTimer = 0;
let cleanupCallbacks = [];

function safeStorage(storage) {
  if (storage) return storage;
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function normalizedText(value, limit = 1200) {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
}

function safeError(error) {
  if (!error) return { name: "Error", message: "Unknown client error", stack: "" };
  if (typeof error === "string") {
    return { name: "Error", message: normalizedText(error, 500), stack: "" };
  }
  return {
    name: normalizedText(error.name || "Error", 120),
    message: normalizedText(error.message || String(error), 500),
    stack: normalizedText(error.stack || "", 1800),
  };
}

function safeSearch(search = "") {
  try {
    const params = new URLSearchParams(search || "");
    const output = new URLSearchParams();
    params.forEach((value, key) => {
      const sensitive = /token|secret|password|authorization|code|state/i.test(key);
      output.append(key, sensitive ? "[redacted]" : String(value).slice(0, 160));
    });
    const serialized = output.toString();
    return serialized ? `?${serialized}` : "";
  } catch {
    return "";
  }
}

export function safeRoute(locationLike = typeof window !== "undefined" ? window.location : {}) {
  const pathname = String(locationLike?.pathname || "/").slice(0, 300) || "/";
  return `${pathname}${safeSearch(locationLike?.search || "")}`;
}

function recoveryStorageKey(route = safeRoute()) {
  return `${RECOVERY_STORAGE_PREFIX}${encodeURIComponent(route).slice(0, 500)}`;
}

export function readSpaFailures(storage = safeStorage()) {
  if (!storage) return [];
  try {
    const parsed = JSON.parse(storage.getItem(FAILURE_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function recordSpaFailure(
  kind,
  error,
  extra = {},
  storage = safeStorage(),
  now = Date.now(),
) {
  const failure = {
    id: `${now}-${Math.random().toString(36).slice(2, 9)}`,
    occurred_at: new Date(now).toISOString(),
    kind: normalizedText(kind || "spa_runtime_error", 120),
    route: safeRoute(),
    error: safeError(error),
    component_stack: normalizedText(extra?.componentStack || "", 1800),
    source: normalizedText(extra?.source || "frontend", 120),
  };
  if (!storage) return failure;
  try {
    const current = readSpaFailures(storage);
    storage.setItem(
      FAILURE_STORAGE_KEY,
      JSON.stringify([...current, failure].slice(-MAX_FAILURES)),
    );
  } catch {
    // Diagnostics must never create a second application failure.
  }
  return failure;
}

export function canAttemptAutomaticRecovery({
  route = safeRoute(),
  storage = safeStorage(),
  now = Date.now(),
  cooldownMs = RECOVERY_COOLDOWN_MS,
} = {}) {
  if (!storage) return true;
  const key = recoveryStorageKey(route);
  try {
    const previous = Number(storage.getItem(key) || 0);
    if (Number.isFinite(previous) && previous > 0 && now - previous < cooldownMs) {
      return false;
    }
    storage.setItem(key, String(now));
    return true;
  } catch {
    return true;
  }
}

function runWhenVisible(callback) {
  if (typeof document === "undefined" || document.visibilityState !== "hidden") {
    callback();
    return;
  }
  const resume = () => {
    if (document.visibilityState === "hidden") return;
    document.removeEventListener("visibilitychange", resume);
    callback();
  };
  document.addEventListener("visibilitychange", resume);
}

export function attemptAutomaticRecovery({
  reason = "spa_runtime_error",
  route = safeRoute(),
  storage = safeStorage(),
  delayMs = 650,
  reload = null,
  now = Date.now(),
  cooldownMs = RECOVERY_COOLDOWN_MS,
} = {}) {
  const reloadPage = reload || (() => window.location.reload());
  if (process.env.NODE_ENV === "test" && !reload) return false;
  if (!canAttemptAutomaticRecovery({ route, storage, now, cooldownMs })) return false;
  if (reloadTimer) return false;

  runWhenVisible(() => {
    reloadTimer = window.setTimeout(() => {
      reloadTimer = 0;
      try {
        reloadPage(reason);
      } catch {
        // The visible crash fallback still offers a manual reload action.
      }
    }, Math.max(0, Number(delayMs) || 0));
  });
  return true;
}

export function isApplicationShellHealthy(
  rootElement = typeof document !== "undefined" ? document.getElementById("root") : null,
) {
  if (!rootElement) return false;
  if (rootElement.querySelector("[data-mezan-crash-recovery]")) return true;
  if (rootElement.querySelector('[data-testid="main-content"]')) return true;
  if (rootElement.querySelector('[data-testid="auth-loading"]')) return true;
  if (rootElement.querySelector('[data-testid="auth-unavailable"]')) return true;
  if (rootElement.querySelector("form")) return true;
  return rootElement.childElementCount > 0
    && normalizedText(rootElement.textContent, 200).length > 0;
}

function scheduleHealthCheck(reason, delayMs = 1800) {
  if (typeof window === "undefined") return;
  if (healthTimer) window.clearTimeout(healthTimer);
  healthTimer = window.setTimeout(() => {
    healthTimer = 0;
    const root = document.getElementById("root");
    if (isApplicationShellHealthy(root)) return;
    recordSpaFailure("blank_application_shell", new Error(reason), {
      source: "spa_navigation_watchdog",
    });
    attemptAutomaticRecovery({ reason: "blank_application_shell" });
  }, Math.max(250, Number(delayMs) || 1800));
}

function internalAnchorFromEvent(event) {
  if (event.defaultPrevented || event.button !== 0) return null;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
  const anchor = event.target?.closest?.("a[href]");
  if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return null;
  try {
    const target = new URL(anchor.href, window.location.href);
    return target.origin === window.location.origin ? anchor : null;
  } catch {
    return null;
  }
}

function patchHistoryMethod(methodName) {
  const historyObject = window.history;
  const original = historyObject?.[methodName];
  if (typeof original !== "function" || original._mezanSpaRecoveryWrapped) return;
  const wrapped = function wrappedHistoryMethod(...args) {
    const result = original.apply(this, args);
    window.dispatchEvent(new Event(LOCATION_CHANGE_EVENT));
    return result;
  };
  wrapped._mezanSpaRecoveryWrapped = true;
  wrapped._mezanSpaRecoveryOriginal = original;
  historyObject[methodName] = wrapped;
  cleanupCallbacks.push(() => {
    if (historyObject[methodName] === wrapped) historyObject[methodName] = original;
  });
}

export function installSpaRuntimeRecovery() {
  if (installed || typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  installed = true;

  patchHistoryMethod("pushState");
  patchHistoryMethod("replaceState");

  const onInternalClick = (event) => {
    if (!internalAnchorFromEvent(event)) return;
    scheduleHealthCheck("internal_link_navigation", 2200);
  };
  const onLocationChange = () => scheduleHealthCheck("history_navigation", 2200);
  const onRuntimeError = (event) => {
    recordSpaFailure("window_error", event?.error || event?.message || "window error", {
      source: "window.error",
    });
    scheduleHealthCheck("window_error", 900);
  };
  const onUnhandledRejection = (event) => {
    recordSpaFailure("unhandled_rejection", event?.reason || "unhandled rejection", {
      source: "window.unhandledrejection",
    });
    scheduleHealthCheck("unhandled_rejection", 900);
  };

  document.addEventListener("click", onInternalClick, true);
  window.addEventListener("popstate", onLocationChange);
  window.addEventListener(LOCATION_CHANGE_EVENT, onLocationChange);
  window.addEventListener("error", onRuntimeError);
  window.addEventListener("unhandledrejection", onUnhandledRejection);
  cleanupCallbacks.push(
    () => document.removeEventListener("click", onInternalClick, true),
    () => window.removeEventListener("popstate", onLocationChange),
    () => window.removeEventListener(LOCATION_CHANGE_EVENT, onLocationChange),
    () => window.removeEventListener("error", onRuntimeError),
    () => window.removeEventListener("unhandledrejection", onUnhandledRejection),
  );

  const root = document.getElementById("root");
  if (root && typeof MutationObserver !== "undefined") {
    rootObserver = new MutationObserver(() => {
      if (root.childElementCount === 0) scheduleHealthCheck("react_root_became_empty", 700);
    });
    rootObserver.observe(root, { childList: true });
  }

  scheduleHealthCheck("initial_application_boot", 5000);
  return true;
}

export function uninstallSpaRuntimeRecovery() {
  if (!installed) return;
  installed = false;
  if (rootObserver) rootObserver.disconnect();
  rootObserver = null;
  cleanupCallbacks.forEach((cleanup) => {
    try {
      cleanup();
    } catch {
      // Best effort for tests and hot reload only.
    }
  });
  cleanupCallbacks = [];
  if (healthTimer) window.clearTimeout(healthTimer);
  if (reloadTimer) window.clearTimeout(reloadTimer);
  healthTimer = 0;
  reloadTimer = 0;
}

export const SPA_RECOVERY_POLICY = Object.freeze({
  failure_storage_key: FAILURE_STORAGE_KEY,
  recovery_cooldown_ms: RECOVERY_COOLDOWN_MS,
  max_failures: MAX_FAILURES,
  automatic_reload_is_bounded: true,
});

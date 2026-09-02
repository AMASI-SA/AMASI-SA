import api from "./api";

const QOYOD_UNSENT_ORDERS_URL = "/integrations/qoyod/unsent-orders";
const CACHE_TTL_MS = 20_000;

const inFlight = new Map();
const cache = new Map();

function stableKey(params = {}) {
  return JSON.stringify(
    Object.keys(params)
      .sort()
      .map((key) => [key, params[key] ?? null]),
  );
}

function abortError() {
  if (typeof DOMException === "function") {
    return new DOMException("The request was aborted", "AbortError");
  }
  const error = new Error("The request was aborted");
  error.name = "AbortError";
  return error;
}

function consumeShared(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abortError());

  return new Promise((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      reject(abortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

export function loadQoyodUnsentOrders(
  params = {},
  { signal, force = false } = {},
) {
  const key = stableKey(params);
  const now = Date.now();
  const cached = cache.get(key);
  if (!force && cached && cached.expiresAt > now) {
    return consumeShared(Promise.resolve(cached.data), signal);
  }
  if (cached && cached.expiresAt <= now) cache.delete(key);

  let shared = inFlight.get(key);
  if (!shared) {
    shared = api.get(QOYOD_UNSENT_ORDERS_URL, { params })
      .then(({ data }) => {
        cache.set(key, {
          data,
          expiresAt: Date.now() + CACHE_TTL_MS,
        });
        return data;
      });
    inFlight.set(key, shared);
    const cleanup = () => {
      if (inFlight.get(key) === shared) inFlight.delete(key);
    };
    shared.then(cleanup, cleanup);
  }
  return consumeShared(shared, signal);
}

export function isQoyodRequestAbort(error) {
  return error?.name === "AbortError";
}

export function __resetQoyodUnsentOrdersClientForTests() {
  inFlight.clear();
  cache.clear();
}

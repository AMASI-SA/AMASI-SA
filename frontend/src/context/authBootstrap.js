export const AUTH_BOOTSTRAP_MAX_ATTEMPTS = 2;
export const AUTH_BOOTSTRAP_RETRY_DELAY_MS = 750;
export const AUTH_BOOTSTRAP_DEADLINE_MS = 8_000;
export const AUTH_SESSION_REQUEST_TIMEOUT_MS = 6_000;

export class AuthBootstrapTimeoutError extends Error {
    constructor() {
        super("auth_bootstrap_deadline_exceeded");
        this.name = "AuthBootstrapTimeoutError";
        this.code = "AUTH_BOOTSTRAP_TIMEOUT";
    }
}

function abortReason(signal) {
    if (signal?.reason instanceof Error) return signal.reason;
    const error = new Error("auth_bootstrap_aborted");
    error.name = "AbortError";
    error.code = "AUTH_BOOTSTRAP_ABORTED";
    return error;
}

export function isAuthBootstrapAbort(error) {
    return error?.name === "AbortError"
        || error?.code === "AUTH_BOOTSTRAP_ABORTED"
        || error?.code === "ERR_CANCELED";
}

function abortable(promise, signal) {
    if (!signal) return Promise.resolve(promise);
    if (signal.aborted) return Promise.reject(abortReason(signal));

    return new Promise((resolve, reject) => {
        const onAbort = () => reject(abortReason(signal));
        signal.addEventListener("abort", onAbort, { once: true });
        Promise.resolve(promise).then(
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

function waitForRetry(delayMs, signal) {
    if (signal?.aborted) return Promise.reject(abortReason(signal));
    return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
            signal?.removeEventListener("abort", onAbort);
            resolve();
        }, delayMs);
        const onAbort = () => {
            window.clearTimeout(timer);
            reject(abortReason(signal));
        };
        signal?.addEventListener("abort", onAbort, { once: true });
    });
}

function retryAfterMs(error, nowMs) {
    const raw = error?.response?.headers?.["retry-after"];
    if (raw == null || raw === "") return 0;

    const seconds = Number(raw);
    if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1_000;

    const retryAt = Date.parse(String(raw));
    if (!Number.isFinite(retryAt)) return 0;
    return Math.max(0, retryAt - nowMs);
}

/**
 * Run the initial cookie-session probe inside one wall-clock deadline.
 *
 * The probe may include the Axios 401 -> refresh -> retry interceptor chain,
 * so a transport timeout alone is not a sufficient bound. This controller
 * also makes a promise that ignores AbortSignal fail closed at the deadline.
 */
export async function runBoundedAuthBootstrap({
    probe,
    signal,
    maxAttempts = AUTH_BOOTSTRAP_MAX_ATTEMPTS,
    retryDelayMs = AUTH_BOOTSTRAP_RETRY_DELAY_MS,
    deadlineMs = AUTH_BOOTSTRAP_DEADLINE_MS,
    requestTimeoutMs = AUTH_SESSION_REQUEST_TIMEOUT_MS,
    now = () => Date.now(),
} = {}) {
    if (typeof probe !== "function") {
        throw new TypeError("auth_bootstrap_probe_required");
    }

    const controller = new AbortController();
    const relayAbort = () => controller.abort(abortReason(signal));
    if (signal?.aborted) relayAbort();
    else signal?.addEventListener("abort", relayAbort, { once: true });

    const startedAt = now();
    let deadlineError = null;
    const deadlineTimer = window.setTimeout(() => {
        deadlineError = new AuthBootstrapTimeoutError();
        controller.abort(deadlineError);
    }, Math.max(1, deadlineMs));

    let lastError = new AuthBootstrapTimeoutError();
    try {
        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            if (controller.signal.aborted) {
                throw deadlineError || abortReason(controller.signal);
            }

            const elapsedMs = Math.max(0, now() - startedAt);
            const remainingMs = deadlineMs - elapsedMs;
            if (remainingMs <= 0) throw new AuthBootstrapTimeoutError();

            try {
                return await abortable(
                    probe({
                        signal: controller.signal,
                        timeoutMs: Math.max(
                            1,
                            Math.min(requestTimeoutMs, remainingMs),
                        ),
                    }),
                    controller.signal,
                );
            } catch (error) {
                if (controller.signal.aborted) {
                    throw deadlineError || abortReason(controller.signal);
                }
                lastError = error;
                if (attempt >= maxAttempts) throw error;

                const remainingAfterFailure = deadlineMs
                    - Math.max(0, now() - startedAt);
                const requestedDelay = Math.max(
                    retryDelayMs,
                    retryAfterMs(error, now()),
                );
                if (requestedDelay >= remainingAfterFailure) throw error;
                await waitForRetry(requestedDelay, controller.signal);
            }
        }
        throw lastError;
    } finally {
        window.clearTimeout(deadlineTimer);
        signal?.removeEventListener("abort", relayAbort);
    }
}

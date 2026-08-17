export const AUTH_RECOVERY_DELAYS_MS = Object.freeze([
    2_000,
    5_000,
    10_000,
    20_000,
    30_000,
]);

export function authRecoveryDelayMs(attempt) {
    const index = Math.max(
        0,
        Math.min(
            AUTH_RECOVERY_DELAYS_MS.length - 1,
            Number.isFinite(Number(attempt)) ? Math.floor(Number(attempt)) : 0,
        ),
    );
    return AUTH_RECOVERY_DELAYS_MS[index];
}

export function browserCanRetryAuth({
    online = typeof navigator === "undefined" || navigator.onLine !== false,
    hidden = typeof document !== "undefined" && document.hidden,
} = {}) {
    return Boolean(online) && !Boolean(hidden);
}

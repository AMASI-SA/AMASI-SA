const RETRYABLE_HTTP_STATUSES = new Set([
    408,
    500,
    502,
    503,
    504,
    520,
    521,
    522,
    523,
    524,
]);

const TRANSIENT_MESSAGE_RE = /cloudflare|origin web server|invalid or incomplete response|network error|network request failed|timeout|timed out|failed to fetch|\b52[0-4]\b/i;

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && typeof detail.message === "string") {
        return detail.message;
    }
    return String(error?.message || "");
}

export function isTransientAdsReadFailure(error) {
    const status = Number(error?.response?.status || 0);
    if (RETRYABLE_HTTP_STATUSES.has(status)) return true;
    return TRANSIENT_MESSAGE_RE.test(errorMessage(error));
}

function wait(delayMs) {
    return new Promise((resolve) => {
        setTimeout(resolve, delayMs);
    });
}

export async function retryAdsRead(
    operation,
    {
        attempts = 3,
        delays = [450, 1250],
        sleep = wait,
    } = {},
) {
    const maxAttempts = Math.max(1, Number(attempts) || 1);
    let lastError;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
        try {
            return await operation(attempt);
        } catch (error) {
            lastError = error;
            if (attempt >= maxAttempts || !isTransientAdsReadFailure(error)) {
                throw error;
            }
            const delayMs = Number(delays[attempt - 1] ?? delays.at(-1) ?? 0);
            if (delayMs > 0) await sleep(delayMs);
        }
    }

    throw lastError;
}

export const ADS_READ_RETRY_POLICY = Object.freeze({
    read_only: true,
    maximum_attempts: 3,
    retryable_statuses: [...RETRYABLE_HTTP_STATUSES],
    provider_writes_allowed: false,
});

// One user-owned read, deferred while the page is hidden/offline. No interval,
// timer, polling, or work survives cleanup. A completed read stays completed.
export const canReadOperationalPage = () => document.visibilityState !== "hidden" && navigator.onLine !== false;

export function visibleOperationalRead(read, { onStart, onData, onError, onEnd } = {}, lane = {}) {
    let alive = true, pending = false, queued = true, waiting = false, controller = null;
    const run = async () => {
        if (!alive || pending || !queued || !canReadOperationalPage()) return;
        if (lane.pending) {
            if (!waiting) {
                waiting = true;
                const resume = () => { waiting = false; run(); };
                lane.pending.then(resume, resume);
            }
            return;
        }
        queued = false; pending = true;
        const request = controller = new AbortController();
        onStart?.();
        const operation = Promise.resolve().then(() => {
            if (request.signal.aborted || !canReadOperationalPage()) throw new DOMException("Page is not active", "AbortError");
            return read({ signal: request.signal });
        });
        lane.pending = operation;
        try {
            const result = await operation;
            if (alive && !request.signal.aborted) onData?.(result);
        } catch (error) {
            if (alive && !request.signal.aborted) onError?.(error);
        } finally {
            if (lane.pending === operation) lane.pending = null;
            pending = false;
            if (alive && !request.signal.aborted) onEnd?.();
            if (alive && queued) run();
        }
    };
    const activity = () => {
        if (!canReadOperationalPage()) {
            if (pending) { queued = true; controller?.abort(); }
            return;
        }
        if (queued) run();
    };
    document.addEventListener("visibilitychange", activity);
    window.addEventListener("online", activity);
    window.addEventListener("offline", activity);
    run();
    return () => {
        alive = false; queued = false; controller?.abort();
        document.removeEventListener("visibilitychange", activity);
        window.removeEventListener("online", activity);
        window.removeEventListener("offline", activity);
    };
}

export async function refreshVisibleSnapshot(api, config) {
    if (config.signal?.aborted || !canReadOperationalPage()) throw new DOMException("Page is not active", "AbortError");
    try {
        const result = await api.refreshBalances(config);
        if (config.signal?.aborted || !canReadOperationalPage()) throw new DOMException("Page is not active", "AbortError");
        return result;
    } catch (error) {
        // A simultaneous visible request owns the same refresh. Keep the saved
        // balances readable; never launch a polling/retry loop to wait for it.
        if (config.signal?.aborted || !canReadOperationalPage()) throw error;
        const status = error?.response?.status, detail = error?.response?.data?.detail;
        if (status === 409 && detail?.code === "operational_refresh_in_progress") return {refresh_warning: "يوجد تحديث جارٍ من صفحة أخرى؛ تظهر آخر لقطة محفوظة بتاريخها دون تكرار الاحتساب."};
        const sourceFailure = ["operational_capacity_requires_archive", "operational_source_setup_incomplete", "operational_refresh_failed",
            "operational_source_read_timeout", "operational_source_read_invalid", "operational_refresh_lease_lost"].includes(detail?.code);
        if (sourceFailure || status >= 500 || ["ECONNABORTED", "ETIMEDOUT", "ERR_NETWORK"].includes(error?.code)) {
            return {refresh_warning: "تعذر تحديث المصادر؛ تُعرض آخر أرصدة محفوظة بتاريخها. " + (detail?.message || "أعد التحديث عند الحاجة من الصفحة.")};
        }
        throw error;
    }
}

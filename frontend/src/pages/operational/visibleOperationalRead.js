// One user-owned read, deferred while the page is hidden/offline. No interval,
// timer, polling, or work survives cleanup. A completed read stays completed.
export const canReadOperationalPage = () => document.visibilityState !== "hidden" && navigator.onLine !== false;

// Opt-in for a costly report load: one visible intent has one deadline,
// including any wait for the previous physical request. Losing visibility
// consumes the intent; only an explicit user retry may start another one.
function singleVisibleAttempt(read, callbacks, lane, { singleAttemptTimeoutMs, deferUntilVisible = true }) {
    const controller = new AbortController();
    let done = false, started = false, timer = null, deadlineAt = null, operation = null, waitingFor = null;
    const detach = () => {
        if (timer !== null) { clearTimeout(timer); timer = null; }
        document.removeEventListener("visibilitychange", activity);
        window.removeEventListener("online", activity);
        window.removeEventListener("offline", activity);
    };
    const finish = (kind, value) => {
        if (done) return;
        done = true; detach();
        if (kind === "paused" || kind === "timeout") controller.abort();
        try {
            if (kind === "data") {
                try { callbacks.onData?.(value); } catch (error) { callbacks.onError?.(error); }
            } else if (kind === "paused") callbacks.onPaused?.();
            else {
                const error = kind === "timeout" ? Object.assign(new Error("operational_visible_read_timeout"), {
                    code: "OPERATIONAL_VISIBLE_READ_TIMEOUT",
                }) : value;
                callbacks.onError?.(error);
            }
        } finally { callbacks.onEnd?.(); }
    };
    const run = () => {
        if (done || operation) return;
        if (!canReadOperationalPage()) {
            if (started || !deferUntilVisible) finish("paused");
            return;
        }
        if (!started) {
            started = true;
            deadlineAt = Date.now() + singleAttemptTimeoutMs;
            timer = setTimeout(() => finish("timeout"), singleAttemptTimeoutMs);
            callbacks.onStart?.();
        }
        if (lane.pending) {
            if (waitingFor !== lane.pending) {
                waitingFor = lane.pending;
                waitingFor.then(run, run);
            }
            return;
        }
        operation = Promise.resolve().then(() => {
            if (done || controller.signal.aborted || !canReadOperationalPage()) throw new DOMException("Page is not active", "AbortError");
            return read({ signal: controller.signal, _mezanAuthDeadlineAt: deadlineAt });
        });
        // A presentation timeout does not prove the physical request finished.
        // Register its release before publishing the lane to waiting readers.
        const release = () => { if (lane.pending === operation) lane.pending = null; };
        operation.then(release, release);
        lane.pending = operation;
        operation.then(result => {
            if (done) return;
            if (!canReadOperationalPage()) finish("paused");
            else finish("data", result);
        }, error => {
            if (done) return;
            if (!canReadOperationalPage()) finish("paused");
            else finish("error", error);
        });
    };
    const activity = () => {
        if (!canReadOperationalPage()) {
            if (started || !deferUntilVisible) finish("paused");
        } else run();
    };
    document.addEventListener("visibilitychange", activity);
    window.addEventListener("online", activity);
    window.addEventListener("offline", activity);
    run();
    return () => { done = true; detach(); controller.abort(); };
}

export function visibleOperationalRead(read, callbacks = {}, lane = {}, policy = {}) {
    if (Number.isFinite(policy.singleAttemptTimeoutMs) && policy.singleAttemptTimeoutMs > 0) {
        return singleVisibleAttempt(read, callbacks, lane, policy);
    }
    const { onStart, onData, onError, onEnd } = callbacks;
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

const REFRESH_TIMING_STAGES = new Set([
    "native_evidence", "aggregate", "source_collection", "source_collision", "reconciliation", "profit_snapshot",
    "bank_claims", "party_catalogs", "snapshot_load", "snapshot_compare", "snapshot_pack", "snapshot_commit",
    "source_primary_reads", "source_fee_settings", "source_setup", "source_statements", "source_delivery_history",
    "source_transactions", "source_native_links", "source_bundles", "source_option_catalog", "source_orders",
    "source_order_indexes", "source_bank_catalog", "source_invoices", "source_other_sources", "source_native_overlaps", "source_finalize",
]);

// Local diagnostic evidence only. Nested stages overlap; their durations must
// not be added. Reconstruct the fixed fields instead of logging response data.
function logOperationalRefreshTimings(detail, label) {
    if (!Array.isArray(detail?.stage_timings)) return;
    const timings = detail.stage_timings.filter(row => row && REFRESH_TIMING_STAGES.has(row.stage)
        && typeof row.duration_ms === "number" && Number.isFinite(row.duration_ms)
        && row.duration_ms >= 0 && row.duration_ms <= 300000
        && ["complete", "cancelled", "failed"].includes(row.outcome))
        .slice(-64).map(({ stage, duration_ms, outcome }) => ({ stage, duration_ms, outcome }));
    if (timings.length) console.info(label, timings);
}

export function logOperationalRefreshTimeoutTimings(detail) {
    logOperationalRefreshTimings(detail, "operational_refresh_timeout_stage_timings");
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
        const refreshTimedOut = status === 408 && detail?.code === "operational_refresh_timeout";
        if (refreshTimedOut) logOperationalRefreshTimeoutTimings(detail);
        if (status === 409 && detail?.code === "operational_source_setup_incomplete") {
            logOperationalRefreshTimings(detail, "operational_refresh_source_stage_timings");
        }
        if (refreshTimedOut || sourceFailure || status >= 500 || ["ECONNABORTED", "ETIMEDOUT", "ERR_NETWORK"].includes(error?.code)) {
            return {refresh_warning: "تعذر تحديث المصادر؛ تُعرض آخر أرصدة محفوظة بتاريخها. " + (detail?.message || "أعد التحديث عند الحاجة من الصفحة.")};
        }
        throw error;
    }
}

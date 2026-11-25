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
    "snapshot_raw_read", "snapshot_unpack", "snapshot_copy_prepare", "orders_cursor_build", "orders_cursor_read", "orders_rows_validate",
    "source_primary_reads", "source_fee_settings", "source_setup", "source_statements", "source_delivery_history",
    "source_transactions", "source_native_links", "source_bundles", "source_option_catalog", "source_orders",
    "source_order_indexes", "source_bank_catalog", "source_invoices", "source_other_sources", "source_native_overlaps", "source_finalize",
    "profit_context", "profit_sales", "profit_products", "profit_order_costs", "profit_ads", "profit_payroll", "profit_recurring",
    "profit_context_settings", "profit_context_orders", "profit_context_raw", "profit_context_prepare",
    "profit_product_catalog", "profit_product_inputs", "profit_product_authority", "profit_product_supplier", "profit_product_reduce",
]);

// Local diagnostic evidence only. Nested stages overlap; their durations must
// not be added. Reconstruct the fixed fields instead of logging response data.
function logOperationalRefreshTimings(detail, label) {
    try {
        const timings = (Array.isArray(detail?.stage_timings) ? detail.stage_timings : []).filter(row => row && REFRESH_TIMING_STAGES.has(row.stage)
            && typeof row.duration_ms === "number" && Number.isFinite(row.duration_ms)
            && row.duration_ms >= 0 && row.duration_ms <= 300000
            && ["complete", "cancelled", "failed"].includes(row.outcome))
            .slice(-64).map(({ stage, duration_ms, outcome }) => ({ stage, duration_ms, outcome }));
        if (timings.length) console.info(label + " " + JSON.stringify(timings));
        const reads = (Array.isArray(detail?.primary_orders_reads) ? detail.primary_orders_reads : [])
            .slice(-12).map(safePrimaryOrdersRead).filter(Boolean);
        if (reads.length) console.info(label.replace(/_stage_timings$/, "_primary_orders_reads") + " " + JSON.stringify(reads));
    } catch (_) {
        // A local diagnostic failure must not replace the refresh outcome.
    }
}

function safePrimaryOrdersRead(row) {
    if (!row || row.collection !== "unified_orders" || !["complete", "cancelled", "failed"].includes(row.outcome)
        || !["motor", "coroutine", "unknown"].includes(row.cursor_kind)) return null;
    const result = {collection: "unified_orders", outcome: row.outcome, cursor_kind: row.cursor_kind};
    for (const key of ["rows_retained", "last_checkpoint_rows"]) {
        if (!Number.isInteger(row[key]) || row[key] < 0 || row[key] > 20001) return null;
        result[key] = row[key];
    }
    if (result.last_checkpoint_rows > result.rows_retained) return null;
    const nullable = new Set(["first_row_ms", "last_checkpoint_ms", "read_elapsed_ms", "close_ms"]);
    for (const key of ["queue_ms", "budget_ms", "first_row_ms", "last_checkpoint_ms", "read_elapsed_ms", "cancel_join_ms", "close_ms"]) {
        const value = row[key];
        if (value === null && nullable.has(key)) result[key] = null;
        else if (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 300000) result[key] = value;
        else return null;
    }
    return result;
}

export function logOperationalRefreshTimeoutTimings(detail) {
    logOperationalRefreshTimings(detail, "operational_refresh_timeout_stage_timings");
}

const COURIER_OUTCOME_STATUSES = new Set(["saved", "unchanged", "already_eligible", "blocked", "unavailable", "source_changed"]);
const COURIER_OUTCOME_REASONS = new Set([
    "courier_native_shipment_ambiguous", "courier_native_shipment_mismatch", "courier_native_history_order_mismatch",
    "courier_native_timeline_conflict", "courier_native_source_conflict", "courier_native_pagination_incomplete",
    "courier_native_pagination_missing", "courier_native_page_repeated", "courier_native_source_refresh_required",
    "courier_native_source_version_mismatch", "courier_native_shipment_identity_missing", "courier_native_order_not_delivered",
    "courier_native_order_mismatch", "courier_native_carrier_missing", "courier_native_source_changed",
    "courier_native_evidence_invalid", "native_evidence_unavailable", "courier_native_response_invalid",
    "courier_native_clock_in_future", "courier_native_order_transition_missing",
    "native_evidence_access_denied", "native_evidence_request_timeout",
]);

// A failed aggregate may still follow a completed native acquisition. Accept
// only the bounded receipt for this explicit target and the current session.
function courierReceiptFromFailure(status, detail, config) {
    const allowedFailure = (status === 408 && detail?.code === "operational_refresh_timeout")
        || (status === 409 && ["operational_source_setup_incomplete", "operational_capacity_requires_archive"].includes(detail?.code));
    if (!allowedFailure || typeof config.courierOrderId !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(config.courierOrderId)
        || typeof config.expectedSessionScope !== "string" || !/^[a-f0-9]{64}$/.test(config.expectedSessionScope)
        || detail?.session_scope !== config.expectedSessionScope) return null;
    const courier = detail?.native_evidence?.courier;
    if (!["completed", "deferred"].includes(courier?.status) || ![0, 1].includes(courier?.selected)
        || !Array.isArray(courier.outcomes) || courier.outcomes.length !== courier.selected) return null;
    const outcomes = [];
    if (courier.selected === 1) {
        const row = courier.outcomes[0];
        if (row?.order_id !== config.courierOrderId || !COURIER_OUTCOME_STATUSES.has(row?.status)) return null;
        const outcome = {order_id: row.order_id, status: row.status};
        if (COURIER_OUTCOME_REASONS.has(row.reason)) outcome.reason = row.reason;
        outcomes.push(outcome);
    }
    return {courier: {status: courier.status, selected: courier.selected, outcomes}};
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
            const native = courierReceiptFromFailure(status, detail, config);
            return {refresh_warning: "تعذر تحديث المصادر؛ تُعرض آخر أرصدة محفوظة بتاريخها. " + (detail?.message || "أعد التحديث عند الحاجة من الصفحة."),
                ...(native ? {native_evidence: native} : {})};
        }
        throw error;
    }
}

const COURIER_RECOVERY_CODES = new Set([
    "operational_delivery_shipment_not_delivered", "operational_delivery_timestamp_missing",
    "operational_delivery_shipment_source_missing", "operational_delivery_order_waybill_unproven",
    "operational_prepaid_shipping_completion_timestamp_missing",
]);

export function canMatchCourierOrder(row) {
    return typeof row?.order_id === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(row.order_id)
        && Array.isArray(row.reasons) && row.reasons.some(reason => COURIER_RECOVERY_CODES.has(reason?.code));
}

// Native acquisition and financial reconciliation have different outcomes. A
// saved proof never means all other review reasons or balances are resolved.
export function courierRecoveryMessage(result, orderId) {
    const warning = result?.refresh_warning;
    const message = courierNativeRecoveryMessage(result, orderId, !warning);
    return warning ? (message ? message + " " : "") + warning
        : message || "أُعيدت قراءة الأرصدة، لكن لم تصل نتيجة مؤكدة لمطابقة هذا الطلب مع سلة.";
}

function courierNativeRecoveryMessage(result, orderId, refreshed) {
    const courier = result?.native_evidence?.courier;
    const outcome = Array.isArray(courier?.outcomes)
        ? courier.outcomes.find(row => row?.order_id === orderId) : null;
    if (outcome?.status === "saved") {
        return refreshed ? "تم التحقق من دليل الطلب وحفظه من سلة، وأُعيد احتساب الأرصدة. قد تبقى للطلب أسباب مراجعة أخرى."
            : "تم التحقق من دليل الطلب وحفظه من سلة. قد تبقى للطلب أسباب مراجعة أخرى.";
    }
    if (outcome?.status === "unchanged") return refreshed
        ? "تم التحقق من دليل الطلب المحفوظ؛ لم يحتج إلى تغيير. أُعيد احتساب الأرصدة، وقد تبقى للطلب أسباب مراجعة أخرى."
        : "تم التحقق من دليل الطلب المحفوظ؛ لم يحتج إلى تغيير. قد تبقى للطلب أسباب مراجعة أخرى.";
    if (outcome?.status === "already_eligible") return refreshed
        ? "دليل الطلب المحفوظ مستوفٍ لهذا الفحص؛ أُعيد احتساب الأرصدة للتحقق من حالته."
        : "دليل الطلب المحفوظ مستوفٍ لهذا الفحص.";
    if (outcome?.status === "source_changed") return "تغيرت بيانات الطلب أثناء المطابقة؛ لم يُحفظ دليل على بيانات قديمة. راجع الحالة المحدثة قبل إعادة المحاولة.";
    if (outcome?.status === "unavailable") {
        if (outcome.reason === "native_evidence_access_denied") {
            return "رفضت سلة قراءة دليل هذا الطلب. لم تُثبت المطابقة؛ راجع صلاحيات ربط سلة وإتاحة المصدر قبل إعادة المحاولة.";
        }
        if (outcome.reason === "native_evidence_request_timeout") {
            return "انتهت مهلة الاتصال بسلة أثناء قراءة دليل هذا الطلب. لم تُثبت المطابقة؛ أعد المحاولة عند الحاجة بعد مهلة إعادة المحاولة.";
        }
        return "تعذرت قراءة دليل هذا الطلب من سلة. لم تُثبت المطابقة؛ أعد المحاولة عند الحاجة بعد مهلة إعادة المحاولة.";
    }
    if (outcome?.status === "blocked") {
        const reasons = {
            courier_native_shipment_ambiguous: "توجد بوالص متعددة أو مستبدلة؛ يلزم تحديد البوليصة التي أُنجز تسليمها.",
            courier_native_shipment_mismatch: "لم تتطابق البوليصة أو شركة الشحن مع حالة الشحنة في سلة.",
            courier_native_history_order_mismatch: "سجل التسليم لا يخص الطلب المطلوب.",
            courier_native_timeline_conflict: "تسلسل تواريخ الطلب والشحنة لا يكفي لإثبات تاريخ التسليم.",
            courier_native_source_conflict: "دليل التسليم يتعارض مع بيانات الشحنة المحفوظة.",
            courier_native_pagination_incomplete: "لم يكتمل سجل سلة اللازم للتحقق من التسليم وعدم وجود حركة لاحقة.",
            courier_native_pagination_missing: "لم يكتمل سجل سلة اللازم للتحقق من التسليم وعدم وجود حركة لاحقة.",
            courier_native_page_repeated: "لم يكتمل سجل سلة اللازم للتحقق من التسليم وعدم وجود حركة لاحقة.",
            courier_native_source_refresh_required: "بيانات الطلب الأساسية غير مكتملة أو تحتاج إلى تحديث موثوق من سلة قبل اعتماد الدليل.",
            courier_native_source_version_mismatch: "تاريخ نسخة الطلب لا يطابق النسخة المحفوظة؛ يلزم تحديث بيانات الطلب قبل اعتماد الدليل.",
            courier_native_shipment_identity_missing: "معرف الشحنة أو رقم البوليصة غير متاح؛ لا يمكن ربط الحالة بالشحنة الفعلية.",
            courier_native_order_not_delivered: "حالة الطلب المحفوظة لا تثبت التسليم المطلوب لهذا الفحص.",
            courier_native_order_mismatch: "هوية الطلب لا تطابق بيانات المصدر المحفوظة.",
            courier_native_carrier_missing: "شركة الشحن غير محددة في بيانات الطلب المحفوظة.",
        };
        return "لم تُثبت المطابقة: " + (reasons[outcome.reason] || "الدليل المتاح لا يستوفي شروط التحقق من الطلب والبوليصة وحالة الشحنة وتواريخها.");
    }
    if (courier?.status === "deferred" || result?.native_evidence?.status === "deferred") {
        return refreshed ? "لم تكتمل محاولة مطابقة الطلب ضمن الوقت المتاح. لا تعني إعادة احتساب الأرصدة أن دليل الطلب قد ثبت."
            : "لم تكتمل محاولة مطابقة الطلب ضمن الوقت المتاح؛ لم تصل نتيجة تثبت دليل الطلب.";
    }
    if (courier?.status === "completed" && courier.selected === 0) {
        return "لم تُجرَ محاولة جديدة لهذا الطلب؛ قد يكون ضمن مهلة إعادة المحاولة البالغة 15 دقيقة أو تغيرت أهليته للمطابقة. راجع الحالة المحدثة.";
    }
    return null;
}

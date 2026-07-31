const AI_ANALYSIS_PATH = "/ai/analyze";
const AI_ANALYSIS_ASYNC_PATH = "/ai/analyze-async";
const ACTIVE_STATUSES = new Set(["queued", "running"]);

// Temporary business guard: Qoyod/accounting analysis remains paused until the
// Qoyod integration is completed and an order carries explicit, verified proof
// that it was posted successfully. This is enforced before the payload leaves
// the browser, so under-review/unsent orders cannot be interpreted as failures.
export const QOYOD_ANALYSIS_PAUSED = true;

const QOYOD_REFERENCE_PATTERN = /(?:قيود|qoyod)/i;
const REAL_QOYOD_POSTING_STATUSES = new Set([
    "sent",
    "posted",
    "success",
    "succeeded",
    "completed",
    "paid",
]);

function pathOnly(url) {
    return String(url || "").split("?", 1)[0];
}

function replacePathPreservingQuery(url, nextPath) {
    const raw = String(url || "");
    const queryIndex = raw.indexOf("?");
    return queryIndex === -1 ? nextPath : `${nextPath}${raw.slice(queryIndex)}`;
}

function analysisError(message, status = 502, extra = {}) {
    const error = new Error(message);
    error.response = {
        status,
        data: {
            detail: message,
            ...extra,
        },
    };
    return error;
}

function failureFromJob(job) {
    const message = job?.error?.message || "تعذر إكمال تحليل ميزان.";
    return analysisError(
        message,
        Number(job?.error?.http_status || 502),
        { code: job?.error?.code, retryable: Boolean(job?.error?.retryable) },
    );
}

function terminalResult(job) {
    if (job?.status === "complete") {
        if (!job?.analysis) {
            throw analysisError("اكتملت مهمة التحليل دون نتيجة صالحة.", 502);
        }
        return job;
    }
    if (job?.status === "failed") {
        throw failureFromJob(job);
    }
    return null;
}

function isRealQoyodInvoiceId(value) {
    if (value == null || String(value).trim() === "") return false;
    const normalized = String(value).trim().toUpperCase();
    return !normalized.startsWith("DRY:") && !normalized.startsWith("PREVIEW:");
}

function isVerifiedQoyodSentOrder(order) {
    if (!order || typeof order !== "object" || Array.isArray(order)) return false;
    const qoyod = order.qoyod && typeof order.qoyod === "object" ? order.qoyod : {};
    const accounting = order.accounting && typeof order.accounting === "object"
        ? order.accounting
        : {};
    const invoiceId = (
        order.qoyod_invoice_id
        || qoyod.qoyod_invoice_id
        || qoyod.invoice_id
        || accounting.qoyod_invoice_id
    );
    const status = String(
        order.qoyod_posting_status
        || qoyod.posting_status
        || qoyod.status
        || accounting.qoyod_status
        || "",
    ).trim().toLowerCase();
    const explicitlyVerified = (
        order.sent_to_qoyod === true
        || order.qoyod_posting_verified === true
        || qoyod.sent === true
        || qoyod.verified === true
        || accounting.sent_to_qoyod === true
        || REAL_QOYOD_POSTING_STATUSES.has(status)
    );
    return explicitlyVerified && isRealQoyodInvoiceId(invoiceId);
}

function stripQoyodPostingMarkers(order) {
    const {
        qoyod_invoice_id,
        qoyod_posting_status,
        qoyod_posting_verified,
        sent_to_qoyod,
        qoyod,
        accounting,
        ...safeOrder
    } = order;
    void qoyod_invoice_id;
    void qoyod_posting_status;
    void qoyod_posting_verified;
    void sent_to_qoyod;
    void qoyod;
    void accounting;
    return safeOrder;
}

function containsQoyodReference(value) {
    try {
        return QOYOD_REFERENCE_PATTERN.test(
            typeof value === "string" ? value : JSON.stringify(value),
        );
    } catch {
        return false;
    }
}

function sanitizeOperationalContext(operationalContext) {
    if (!operationalContext || typeof operationalContext !== "object" || Array.isArray(operationalContext)) {
        return operationalContext;
    }
    const sourceOrders = Array.isArray(operationalContext.orders)
        ? operationalContext.orders
        : [];
    const verifiedOrders = sourceOrders
        .filter(isVerifiedQoyodSentOrder)
        .map(stripQoyodPostingMarkers);
    const directives = Array.isArray(operationalContext.analysis_directives)
        ? operationalContext.analysis_directives.filter((item) => !containsQoyodReference(item))
        : [];

    return {
        ...operationalContext,
        sample_selection: "qoyod_sent_verified_only",
        sample_count: verifiedOrders.length,
        orders: verifiedOrders,
        excluded_unverified_or_unsent_orders: sourceOrders.length - verifiedOrders.length,
        observed_paths: Array.isArray(operationalContext.observed_paths)
            ? operationalContext.observed_paths.filter((item) => !containsQoyodReference(item))
            : operationalContext.observed_paths,
        privacy: {
            ...(operationalContext.privacy || {}),
            qoyod_posting_references_removed: true,
        },
        analysis_directives: [
            ...directives,
            "Qoyod integration analysis is temporarily disabled until the integration is completed.",
            "Do not assess Qoyod health, invoice posting, payment posting, or accounting reconciliation.",
            "Only explicitly verified Qoyod-posted orders may remain in the sample; absence of orders is not an anomaly.",
            "Do not infer failures from under-review, pending, processing, or otherwise unsent orders.",
        ],
    };
}

function sanitizeAnalysisContext(context) {
    if (!context || typeof context !== "object" || Array.isArray(context)) return context;
    const metrics = context.metrics && typeof context.metrics === "object"
        ? { ...context.metrics }
        : undefined;
    if (metrics) {
        delete metrics.qoyod_failed;
        delete metrics.dashboard_orders;
        delete metrics.total_sales;
    }

    return {
        ...context,
        ...(metrics ? { metrics } : {}),
        operational_context_v2: sanitizeOperationalContext(
            context.operational_context_v2,
        ),
        errors: Array.isArray(context.errors)
            ? context.errors.filter((item) => !containsQoyodReference(item))
            : context.errors,
        gates: Array.isArray(context.gates)
            ? context.gates.filter((item) => !containsQoyodReference(item))
            : context.gates,
        recommendations: Array.isArray(context.recommendations)
            ? context.recommendations.filter((item) => !containsQoyodReference(item))
            : context.recommendations,
    };
}

export function applyTemporaryAiScope(data) {
    if (!QOYOD_ANALYSIS_PAUSED) return data;
    const wasString = typeof data === "string";
    let payload = data;
    if (wasString) {
        try {
            payload = JSON.parse(data);
        } catch {
            return data;
        }
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) return data;

    const question = String(payload.question || "");
    if (containsQoyodReference(question)) {
        throw analysisError(
            "تحليل قيود موقوف مؤقتًا حتى يكتمل تكامل قيود. لم تُرسل أي بيانات للتحليل.",
            409,
            { code: "qoyod_ai_analysis_paused", retryable: false },
        );
    }

    const sanitized = {
        ...payload,
        context: sanitizeAnalysisContext(payload.context),
    };
    return wasString ? JSON.stringify(sanitized) : sanitized;
}

export function isAiAnalysisRequest(config) {
    return (
        String(config?.method || "").toLowerCase() === "post"
        && pathOnly(config?.url) === AI_ANALYSIS_PATH
        && config?._mezanAiAnalysisAsync !== true
    );
}

export function rewriteAiAnalysisRequest(config) {
    if (!isAiAnalysisRequest(config)) return config;
    return {
        ...config,
        url: replacePathPreservingQuery(config.url, AI_ANALYSIS_ASYNC_PATH),
        data: applyTemporaryAiScope(config.data),
        _mezanAiAnalysisRequest: true,
        _mezanAiAnalysisAsync: true,
    };
}

export function isAiAnalysisAsyncResponse(response) {
    return (
        response?.config?._mezanAiAnalysisAsync === true
        && Number(response?.status) === 202
        && Boolean(response?.data?.run_id)
    );
}

export async function pollAiAnalysisJob({
    accepted,
    loadJob,
    wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
    attempts = 75,
    intervalMs = 1000,
}) {
    const runId = accepted?.run_id;
    if (!runId) {
        throw analysisError("لم يرجع خادم ميزان رقم مهمة التحليل.", 502);
    }

    let current = accepted;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        const acceptedTerminal = terminalResult(current);
        if (acceptedTerminal) return acceptedTerminal;
        if (!ACTIVE_STATUSES.has(current?.status)) {
            throw analysisError(
                `عاد خادم ميزان بحالة تحليل غير معروفة: ${current?.status || "فارغة"}.`,
                502,
            );
        }

        current = await loadJob(runId);
        const loadedTerminal = terminalResult(current);
        if (loadedTerminal) return loadedTerminal;
        if (!ACTIVE_STATUSES.has(current?.status)) {
            throw analysisError(
                `عاد خادم ميزان بحالة تحليل غير معروفة: ${current?.status || "فارغة"}.`,
                502,
            );
        }
        if (attempt < attempts - 1) {
            await wait(intervalMs);
        }
    }

    throw analysisError(
        "استغرق التحليل وقتًا أطول من الحد الآمن. لم يتم تعديل أو إرسال أي بيانات.",
        504,
        { code: "ai_analysis_poll_timeout", retryable: true },
    );
}

const AI_ANALYSIS_PATH = "/ai/analyze";
const AI_ANALYSIS_ASYNC_PATH = "/ai/analyze-async";
const ACTIVE_STATUSES = new Set(["queued", "running"]);

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
        if (current?.status === "complete") {
            if (!current?.analysis) {
                throw analysisError("اكتملت مهمة التحليل دون نتيجة صالحة.", 502);
            }
            return current;
        }
        if (current?.status === "failed") {
            throw failureFromJob(current);
        }
        if (!ACTIVE_STATUSES.has(current?.status)) {
            throw analysisError(
                `عاد خادم ميزان بحالة تحليل غير معروفة: ${current?.status || "فارغة"}.`,
                502,
            );
        }

        current = await loadJob(runId);
        if (current?.status === "complete" || current?.status === "failed") {
            continue;
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

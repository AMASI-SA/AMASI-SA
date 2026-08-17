import axios from "axios";
import {
    dashboardAuthoritativeParams,
    dashboardExecutiveParams,
    hasDashboardExecutiveBreakdown,
    isDashboardAuthoritativeResponse,
    isDashboardV2Response,
    isSnapDailyCompatibilityResponse,
    mergeDashboardAuthoritativeSummary,
    mergeDashboardExecutiveBreakdown,
    rewriteDashboardMezanV2Request,
    toLegacySnapDailySpend,
} from "./dashboardMezanV2Adapter";
import {
    createLatestResponseBroker,
    dashboardRequestRangeKey,
} from "./dashboardLatestResponseBroker";
import {
    isAiAnalysisAsyncResponse,
    isAiAnalysisRequest,
    pollAiAnalysisJob,
    rewriteAiAnalysisRequest,
} from "./aiAnalysisAsync";
import {
    isMetaDashboardAsyncSyncResponse,
    pollMetaDashboardSyncJob,
    rewriteMetaDashboardRequest,
    toLegacyMetaSyncPayload,
} from "./metaDashboardV2Adapter";
import {
    isSnapchatAsyncSyncResponse,
    isSnapchatSyncRequest,
    pollSnapchatAsyncSyncJob,
    recoverSnapchatSyncAfterTransportFailure,
    rewriteSnapchatSyncRequest,
} from "./snapchatSyncRecovery";
import { AUTH_SESSION_REQUEST_TIMEOUT_MS } from "../context/authBootstrap";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND_URL}/api`;

const api = axios.create({
    baseURL: API_BASE,
    withCredentials: true,
});

let authRefreshPromise = null;

// Dashboard enrichment is optional. It must never hold the complete dashboard
// response hostage when a provider-specific read is slow or temporarily
// unavailable (for example while a browser session is being refreshed).
export const DASHBOARD_ENRICHMENT_TIMEOUT_MS = 2_500;

function withinDashboardEnrichmentDeadline(promise, timeoutMs = DASHBOARD_ENRICHMENT_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
        const timer = globalThis.setTimeout(() => {
            reject(new Error("dashboard_enrichment_timeout"));
        }, timeoutMs);
        Promise.resolve(promise).then(
            (value) => {
                globalThis.clearTimeout(timer);
                resolve(value);
            },
            (error) => {
                globalThis.clearTimeout(timer);
                reject(error);
            },
        );
    });
}

function shouldRefreshBrowserSession(error) {
    const status = error?.response?.status;
    const config = error?.config || {};
    const url = String(config.url || "");
    return status === 401
        && config._mezanAuthRetried !== true
        && !url.includes("/auth/login")
        && !url.includes("/auth/logout")
        && !url.includes("/auth/refresh")
        && !url.includes("/auth/mfa/")
        && !url.includes("/auth/email-otp/")
        && !url.includes("/auth/passkey/");
}

function browserSessionRefreshTimeout(config = {}) {
    const configuredTimeout = Number(config.timeout);
    const deadlineAt = Number(config._mezanAuthDeadlineAt);
    const remainingToDeadline = Number.isFinite(deadlineAt)
        ? deadlineAt - Date.now()
        : AUTH_SESSION_REQUEST_TIMEOUT_MS;
    const requestBudget = Number.isFinite(configuredTimeout) && configuredTimeout > 0
        ? configuredTimeout
        : AUTH_SESSION_REQUEST_TIMEOUT_MS;
    return Math.max(
        1,
        Math.min(
            AUTH_SESSION_REQUEST_TIMEOUT_MS,
            requestBudget,
            remainingToDeadline,
        ),
    );
}

async function refreshBrowserSessionOnce(timeoutMs) {
    if (!authRefreshPromise) {
        authRefreshPromise = axios.post(
            `${API_BASE}/auth/refresh`,
            {},
            {
                withCredentials: true,
                timeout: timeoutMs,
            },
        ).finally(() => {
            authRefreshPromise = null;
        });
    }
    return authRefreshPromise;
}

const dashboardV2ResponseBroker = createLatestResponseBroker();

function currentAccessToken() {
    try {
        return localStorage.getItem("access_token");
    } catch {
        return null;
    }
}

function directRequestHeaders() {
    const token = currentAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
}

async function loadSnapDailyCompatibility(config = {}) {
    const date = String(config?._mezanSnapDailyDate || "").trim();
    const summaryResponse = await axios.get(
        `${API_BASE}/integrations-v2/snapchat_ads/performance-summary`,
        {
            params: {
                ...(date ? { from_date: date, to_date: date } : {}),
            },
            withCredentials: true,
            headers: directRequestHeaders(),
        },
    );
    return toLegacySnapDailySpend(summaryResponse.data, date);
}

function emptyDashboardExecutiveBreakdown() {
    return {
        providers: {},
        total: {},
        coverage: { transport_fallback_unavailable: true },
        source_only: true,
        provider_write_reached: false,
        campaign_write_reached: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    };
}

api.interceptors.request.use((config) => {
    const token = currentAccessToken();
    if (token) {
        config.headers = config.headers || {};
        config.headers["Authorization"] = `Bearer ${token}`;
    }

    let nextConfig = rewriteDashboardMezanV2Request(config);
    if (nextConfig?._mezanDashboardV2 === true) {
        nextConfig._mezanDashboardRequestToken = dashboardV2ResponseBroker.begin({
            rangeKey: dashboardRequestRangeKey(nextConfig),
        });
    }
    if (isSnapchatSyncRequest(nextConfig)) {
        nextConfig._mezanSyncStartedAt = Date.now();
        nextConfig = rewriteSnapchatSyncRequest(nextConfig);
    }
    if (isAiAnalysisRequest(nextConfig)) {
        nextConfig = rewriteAiAnalysisRequest(nextConfig);
    }
    nextConfig = rewriteMetaDashboardRequest(nextConfig);
    return nextConfig;
});

api.interceptors.response.use(
    async (response) => {
        if (isMetaDashboardAsyncSyncResponse(response)) {
            const job = await pollMetaDashboardSyncJob({
                accepted: response.data,
                loadJob: async (runId) => {
                    const jobResponse = await axios.get(
                        `${API_BASE}/integrations-v2/meta_ads/sync-async/${encodeURIComponent(runId)}`,
                        {
                            withCredentials: true,
                            headers: directRequestHeaders(),
                        },
                    );
                    return jobResponse.data;
                },
            });
            if (job.status === "failed") {
                const failure = new Error(job.error?.message || "تعذر تحديث Meta V2.");
                failure.response = {
                    status: 502,
                    data: {
                        detail: job.error || {
                            code: "meta_dashboard_v2_sync_failed",
                            message: "تعذر تحديث Meta V2.",
                        },
                    },
                };
                throw failure;
            }
            return {
                ...response,
                data: toLegacyMetaSyncPayload(job),
                status: 200,
                statusText: "Meta V2 dashboard sync completed",
            };
        }

        if (isSnapchatAsyncSyncResponse(response)) {
            const payload = await pollSnapchatAsyncSyncJob({
                accepted: response.data,
                loadJob: async (runId) => {
                    const jobResponse = await axios.get(
                        `${API_BASE}/integrations-v2/snapchat_ads/sync-async/${encodeURIComponent(runId)}`,
                        {
                            withCredentials: true,
                            headers: directRequestHeaders(),
                        },
                    );
                    return jobResponse.data;
                },
            });

            if (isSnapDailyCompatibilityResponse(response)) {
                return {
                    ...response,
                    data: await loadSnapDailyCompatibility(response.config),
                    status: 200,
                    statusText: "Snapchat V2 daily compatibility completed",
                };
            }
            return {
                ...response,
                data: payload,
                status: 200,
                statusText: "Snapchat asynchronous sync completed",
            };
        }

        if (
            isDashboardV2Response(response)
            && !hasDashboardExecutiveBreakdown(response.data)
        ) {
            try {
                const executiveResponse = await withinDashboardEnrichmentDeadline(
                    axios.get(
                        `${API_BASE}/integrations-v2/dashboard/ads-executive-breakdown`,
                        {
                            params: dashboardExecutiveParams(response.config),
                            withCredentials: true,
                            headers: directRequestHeaders(),
                        },
                    ),
                );
                return {
                    ...response,
                    data: mergeDashboardExecutiveBreakdown(
                        response.data,
                        executiveResponse.data,
                    ),
                    statusText: "Dashboard V2 merged with inline ads executive breakdown",
                };
            } catch {
                // Mezan 2 must never reopen the legacy advertising-cost modal.
                // Preserve an inline table shell with dashes until the focused
                // read-only fallback endpoint becomes available again.
                return {
                    ...response,
                    data: mergeDashboardExecutiveBreakdown(
                        response.data,
                        emptyDashboardExecutiveBreakdown(),
                    ),
                    statusText: "Dashboard V2 inline ads breakdown unavailable",
                };
            }
        }

        if (isDashboardAuthoritativeResponse(response)) {
            try {
                const authoritativeResponse = await withinDashboardEnrichmentDeadline(
                    axios.get(
                        `${API_BASE}/integrations-v2/dashboard/authoritative-summary`,
                        {
                            params: dashboardAuthoritativeParams(response.config),
                            withCredentials: true,
                            headers: directRequestHeaders(),
                        },
                    ),
                );
                return {
                    ...response,
                    data: mergeDashboardAuthoritativeSummary(
                        response.data,
                        authoritativeResponse.data,
                    ),
                    statusText: "Dashboard merged with Mezan V2 sources",
                };
            } catch {
                // Preserve the legacy Dashboard response if the read-only
                // enrichment endpoint is temporarily unavailable.
                return response;
            }
        }

        if (isAiAnalysisAsyncResponse(response)) {
            const job = await pollAiAnalysisJob({
                accepted: response.data,
                loadJob: async (runId) => {
                    const jobResponse = await axios.get(
                        `${API_BASE}/ai/analyze-async/${encodeURIComponent(runId)}`,
                        {
                            withCredentials: true,
                            headers: directRequestHeaders(),
                        },
                    );
                    return jobResponse.data;
                },
            });

            return {
                ...response,
                data: {
                    ok: true,
                    mode: job.mode || "read_only_analysis",
                    writes_performed: false,
                    analysis: job.analysis,
                },
                status: 200,
                statusText: "Mezan AI asynchronous analysis completed",
            };
        }

        return response;
    },
    async (error) => {
        if (shouldRefreshBrowserSession(error)) {
            try {
                await refreshBrowserSessionOnce(
                    browserSessionRefreshTimeout(error.config),
                );
                return api.request({
                    ...error.config,
                    _mezanAuthRetried: true,
                });
            } catch (refreshError) {
                // Only a definitive refresh rejection proves the cookie
                // session is invalid. Network/429/5xx/timeout failures must
                // remain transient so AuthContext can fail closed with retry
                // instead of falsely navigating the browser to /login.
                if (refreshError?.response?.status === 401) {
                    return Promise.reject(error);
                }
                return Promise.reject(refreshError);
            }
        }

        const recovered = await recoverSnapchatSyncAfterTransportFailure({
            error,
            loadRuns: async () => {
                const response = await axios.get(
                    `${API_BASE}/integrations-v2/sync-runs`,
                    {
                        params: { provider: "snapchat_ads", limit: 10 },
                        withCredentials: true,
                        headers: directRequestHeaders(),
                    },
                );
                return Array.isArray(response.data?.items)
                    ? response.data.items
                    : [];
            },
        });
        if (!recovered) return Promise.reject(error);
        if (error?.config?._mezanSnapDailyCompatibility === true) {
            return {
                data: await loadSnapDailyCompatibility(error.config),
                status: 200,
                statusText: "Recovered Snapchat V2 daily compatibility",
                headers: {},
                config: error.config,
                request: error?.request,
            };
        }
        return {
            data: recovered.payload,
            status: 200,
            statusText: "Recovered from audited Snapchat sync run",
            headers: {},
            config: error?.config || {},
            request: error?.request,
        };
    },
);

// Dashboard date changes can create overlapping requests: an older "today"
// response may finish after the newer "yesterday" response. Resolve every
// stale caller with the newest completed Dashboard V2 payload so stale totals
// can never overwrite the currently selected period in ProfitSummaryCard.
api.interceptors.response.use(
    (response) => {
        const requestToken = response?.config?._mezanDashboardRequestToken;
        return requestToken
            ? dashboardV2ResponseBroker.resolve(requestToken, response)
            : response;
    },
    (error) => {
        const requestToken = error?.config?._mezanDashboardRequestToken
            || error?.response?.config?._mezanDashboardRequestToken;
        return requestToken
            ? dashboardV2ResponseBroker.reject(requestToken, error)
            : Promise.reject(error);
    },
);

export function formatApiErrorDetail(detail) {
    if (detail == null) return "حدث خطأ ما، يرجى المحاولة لاحقاً.";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
        return detail
            .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
            .filter(Boolean)
            .join(" ");
    if (detail && typeof detail.msg === "string") return detail.msg;
    return String(detail);
}

export default api;

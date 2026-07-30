import axios from "axios";
import {
    isSnapchatAsyncSyncResponse,
    isSnapchatSyncRequest,
    pollSnapchatAsyncSyncJob,
    recoverSnapchatSyncAfterTransportFailure,
    rewriteSnapchatSyncRequest,
} from "./snapchatSyncRecovery";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API_BASE = `${BACKEND_URL}/api`;

const api = axios.create({
    baseURL: API_BASE,
    withCredentials: true,
});

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

api.interceptors.request.use((config) => {
    const token = currentAccessToken();
    if (token) {
        config.headers = config.headers || {};
        config.headers["Authorization"] = `Bearer ${token}`;
    }
    if (isSnapchatSyncRequest(config)) {
        config._mezanSyncStartedAt = Date.now();
        return rewriteSnapchatSyncRequest(config);
    }
    return config;
});

api.interceptors.response.use(
    async (response) => {
        if (!isSnapchatAsyncSyncResponse(response)) return response;

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

        return {
            ...response,
            data: payload,
            status: 200,
            statusText: "Snapchat asynchronous sync completed",
        };
    },
    async (error) => {
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

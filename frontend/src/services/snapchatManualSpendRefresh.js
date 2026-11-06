import api from "../lib/api";
import { clearDashboardPlatformSpendCache } from "../lib/dashboardPlatformSpendClient";

const ROUTE = "/integrations-v2/ads-auto-sync/snapchat/refresh";
const MAX_WAIT_MS = 25 * 60_000;

function aborted() {
    const error = new Error("manual_spend_refresh_aborted");
    error.name = "AbortError";
    return error;
}

function wait(ms, signal) {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) { reject(aborted()); return; }
        const onAbort = () => {
            clearTimeout(timer);
            signal?.removeEventListener("abort", onAbort);
            reject(aborted());
        };
        const timer = setTimeout(() => {
            signal?.removeEventListener("abort", onAbort);
            resolve();
        }, ms);
        signal?.addEventListener("abort", onAbort, { once: true });
    });
}

export async function refreshSnapchatSpend({ dateFrom, dateTo, signal } = {}) {
    if (signal?.aborted) throw aborted();
    const { data: accepted } = await api.post(ROUTE,
        { date_from: dateFrom, date_to: dateTo || dateFrom }, { signal });
    if (!accepted?.run_id) throw new Error("تعذر تأكيد بدء جلب صرف سناب.");
    let job = accepted;
    const startedAt = Date.now();
    while (Date.now() - startedAt <= MAX_WAIT_MS) {
        if (signal?.aborted) throw aborted();
        if (job.status === "complete" || job.status === "partial") {
            // Old reads may still finish after this invalidation. The client
            // generation fence prevents them restoring a pre-refresh cache.
            clearDashboardPlatformSpendCache();
            return job;
        }
        if (["failed", "skipped", "unknown"].includes(job.status)) {
            throw new Error("تعذر إكمال جلب صرف سناب. تم الاحتفاظ بآخر بيانات محفوظة.");
        }
        await wait(Date.now() - startedAt < 30_000 ? 2000 : 10_000, signal);
        if ((typeof document !== "undefined" && document.hidden)
            || (typeof navigator !== "undefined" && navigator.onLine === false)) continue;
        const response = await api.get(`${ROUTE}/${encodeURIComponent(accepted.run_id)}`, { signal });
        job = response.data || {};
    }
    throw new Error("لم يتأكد اكتمال تحديث سناب بعد. يمكنك المحاولة لاحقًا.");
}

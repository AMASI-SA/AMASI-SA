import api from "../lib/api";
import { clearDashboardPlatformSpendCache } from "../lib/dashboardPlatformSpendClient";

const ROUTE = "/integrations-v2/ads-auto-sync/snapchat/refresh";
const MAX_WAIT_MS = 6 * 60_000;
const SCOPE = "snapchat_spend_only";

function validJob(job, { dateFrom, dateTo, runId, accountIds } = {}) {
    const ids = job?.requested_account_ids;
    return Boolean(job && typeof job === "object" && !Array.isArray(job)
        && typeof job.run_id === "string" && job.run_id
        && (!runId || job.run_id === runId)
        && job.scope === SCOPE && job.source_mode === "manual_account_hour"
        && job.date_from === dateFrom && job.date_to === dateTo
        && Array.isArray(ids) && ids.length > 0
        && ids.every((id) => typeof id === "string" && id.trim())
        && new Set(ids).size === ids.length
        && (!accountIds || ids.length === accountIds.length && ids.every((id) => accountIds.includes(id))));
}

function successfulAccounts(job) {
    if (!["complete", "partial"].includes(job?.status)) return null;
    const rows = job?.account_results;
    const ids = job?.requested_account_ids;
    if (!Array.isArray(rows) || !Array.isArray(ids) || rows.length !== ids.length
        || new Set(rows.map((row) => row?.account_id)).size !== ids.length
        || rows.some((row) => !ids.includes(row?.account_id) || !["complete", "failed"].includes(row?.status))) return null;
    const completed = rows.filter((row) => row.status === "complete");
    if (!completed.length || completed.length !== job.refreshed_count
        || rows.length - completed.length !== job.failed_count
        || (job.status === "complete") !== (completed.length === ids.length)
        || completed.some((row) => row.snapshot_job_id !== job.run_id
            || typeof row.observed_at !== "string" || !Number.isFinite(Date.parse(row.observed_at)))) return null;
    return completed;
}

export function snapchatSpendReadbackMatches(job, data) {
    const completed = successfulAccounts(job);
    const accounts = data?.providers?.snapchat?.accounts;
    if (!completed || !Array.isArray(accounts) || data.date_from !== job.date_from || data.date_to !== job.date_to) return false;
    return completed.every((result) => {
        const matches = accounts.filter((account) => account.account_id === result.account_id);
        if (matches.length !== 1) return false;
        const account = matches[0];
        // An unchanged amount is valid: confirmation is a newer observation,
        // not an artificial numeric difference. A newer full sync may supersede
        // the manual observation between the job response and this saved read.
        return account.quality?.amount_available === true
            && typeof account.total_sar === "number" && Number.isFinite(account.total_sar) && account.total_sar >= 0
            && typeof account.quality.observed_at === "string"
            && Date.parse(account.quality.observed_at) >= Date.parse(result.observed_at);
    });
}

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
    const expected = { dateFrom, dateTo: dateTo || dateFrom };
    if (!validJob(accepted, expected)) throw new Error("تعذر تأكيد بدء جلب صرف سناب فقط للفترة المحددة.");
    expected.runId = accepted.run_id;
    expected.accountIds = accepted.requested_account_ids;
    let job = accepted;
    const startedAt = Date.now();
    while (Date.now() - startedAt <= MAX_WAIT_MS) {
        if (signal?.aborted) throw aborted();
        if (!validJob(job, expected)) throw new Error("تعذر التحقق من نتيجة تحديث صرف سناب للفترة المحددة.");
        if (job.status === "complete" || job.status === "partial") {
            if (!successfulAccounts(job)) throw new Error("لم يتأكد جلب صرف الحسابات. تم الاحتفاظ بآخر بيانات محفوظة.");
            // Old reads may still finish after this invalidation. The client
            // generation fence prevents them restoring a pre-refresh cache.
            clearDashboardPlatformSpendCache();
            return job;
        }
        if (!["queued", "running"].includes(job.status)) {
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

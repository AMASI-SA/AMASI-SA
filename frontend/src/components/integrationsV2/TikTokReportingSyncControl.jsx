import { useState } from "react";
import { ArrowClockwise, CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import { syncTikTokReporting } from "../../services/tiktokIntegrationsV2";

export default function TikTokReportingSyncControl({ integration }) {
    const [syncing, setSyncing] = useState(false);
    const [result, setResult] = useState(null);
    const action = integration?.actions?.sync_data || {};
    const enabled = Boolean(action.enabled) && !syncing;

    async function runSync() {
        if (!enabled) return;
        setSyncing(true);
        setResult(null);
        try {
            const completed = await syncTikTokReporting({ days: 30 });
            setResult(completed);
            if (completed.status === "complete") {
                toast.success(
                    `اكتملت مزامنة TikTok: ${completed.accounts_complete} حساب، ${completed.rows_saved} صف يومي`,
                );
            } else {
                toast.warning(
                    `اكتملت مزامنة TikTok جزئيًا: ${completed.accounts_complete}/${completed.accounts_attempted} حساب، ${completed.errors_count} ملاحظة`,
                    { duration: 8000 },
                );
            }
        } catch (error) {
            const code = error?.code || error?.response?.data?.detail?.code;
            const known = {
                tiktok_oauth_not_configured: "إعدادات TikTok Marketing API غير مكتملة.",
                tiktok_reporting_disabled: "مزامنة TikTok المباشرة متوقفة بحارس الأمان.",
                tiktok_oauth_credential_missing: "اربط حساب TikTok المصرح به أولًا.",
                tiktok_reporting_accounts_missing: "لم يُكتشف حساب إعلاني مصرح به بعد.",
                tiktok_needs_reauth: "يجب إعادة ربط TikTok قبل المزامنة.",
                tiktok_rate_limited: "TikTok أوقف الطلبات مؤقتًا؛ أعد المحاولة لاحقًا.",
                tiktok_reporting_poll_timeout: "المزامنة ما زالت تستغرق وقتًا أطول من نافذة المتابعة.",
            };
            toast.error(known[code] || error?.message || "تعذر مزامنة TikTok.");
        } finally {
            setSyncing(false);
        }
    }

    return (
        <section
            className="rounded-xl border border-slate-200 bg-white p-3"
            data-testid="tiktok-reporting-sync-control"
        >
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-xs font-extrabold text-slate-800">
                        تقارير TikTok المباشرة
                    </div>
                    <div className="mt-1 text-[11px] leading-5 text-slate-500">
                        قراءة المصروف والمشاهدات والنقرات والتحويلات من Marketing API فقط؛
                        دون تعديل الحملات أو المحاسبة أو قيود.
                    </div>
                </div>
                <button
                    type="button"
                    onClick={runSync}
                    disabled={!enabled}
                    title={!enabled ? (action.reason || "الربط أو راية الأمان غير جاهزين") : undefined}
                    className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-3 text-xs font-extrabold transition ${
                        enabled
                            ? "border-slate-900 bg-slate-900 text-white hover:bg-slate-800"
                            : "cursor-not-allowed border-slate-100 bg-slate-50 text-slate-400"
                    }`}
                    data-testid="integration-tiktok_ads-sync"
                >
                    <ArrowClockwise
                        size={16}
                        weight="bold"
                        className={syncing ? "animate-spin" : ""}
                    />
                    {syncing ? "جاري المزامنة…" : "مزامنة 30 يوم"}
                </button>
            </div>

            {!action.enabled && action.reason && (
                <div className="mt-2 flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50 p-2 text-[11px] leading-5 text-amber-800">
                    <WarningCircle size={15} className="mt-0.5 shrink-0" weight="fill" />
                    {action.reason}
                </div>
            )}

            {result && (
                <div className="mt-2 flex items-center gap-2 rounded-lg border border-emerald-100 bg-emerald-50 p-2 text-[11px] font-bold text-emerald-800">
                    <CheckCircle size={16} weight="fill" />
                    {result.accounts_complete} حساب مكتمل · {result.rows_saved} صف · {result.errors_count} ملاحظة
                </div>
            )}
        </section>
    );
}

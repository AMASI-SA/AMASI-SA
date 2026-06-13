import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Ghost, ArrowsClockwise, CurrencyCircleDollar, Clock, Buildings } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

/**
 * SnapchatAccounts — per-account spend detail page (iteration 15).
 *
 * Lists every Snapchat ad account the merchant has enabled in Settings,
 * showing for each one:
 *   - Today's spend (Asia/Riyadh 00:00 → 23:59)
 *   - This month's spend
 *   - Last 30 days' spend
 *   - Native currency + FX rate + SAR-converted amount
 *   - Last sync timestamp
 *
 * The "تحديث الكل" button calls POST /api/snapchat/sync-all-accounts which
 * syncs every enabled account in parallel, writes per-(account,date) rows
 * into `snapchat_account_daily`, and aggregates totals back into
 * `daily_costs.snapchat_ads` so the Dashboard card auto-updates.
 */

const fmtSAR = (v) =>
    Number.isFinite(Number(v)) ? Number(v).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    }) : "0.00";

const fmtNative = (v, cur) => {
    const n = Number(v) || 0;
    return `${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur || ""}`.trim();
};

const fmtDateAr = (iso) => {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("en-US", {
            dateStyle: "medium", timeStyle: "short",
        });
    } catch { return iso; }
};

function AccountCard({ acc }) {
    const isNonSar = acc.currency_native && acc.currency_native !== "SAR";
    return (
        <div
            className="rounded-xl border border-border bg-white p-4 sm:p-6 overflow-hidden"
            data-testid={`snap-account-card-${acc.ad_account_id}`}
        >
            <div className="flex items-start justify-between gap-3 mb-4 flex-wrap">
                <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <Ghost size={20} weight="fill" className="text-yellow-500 flex-shrink-0" />
                        <h3 className="text-lg sm:text-xl font-bold truncate" style={{ fontFamily: "Tajawal" }}>
                            {acc.name}
                        </h3>
                        <span
                            className="text-[10px] px-1.5 py-0.5 bg-emerald-100 text-emerald-800 rounded-full font-bold"
                            data-testid={`snap-account-status-${acc.ad_account_id}`}
                        >
                            ✓ مُفعَّل
                        </span>
                    </div>
                    <div className="text-xs text-muted-foreground" dir="ltr">{acc.ad_account_id}</div>
                </div>
                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                    <span className="text-[11px] px-2 py-0.5 bg-blue-100 text-blue-800 rounded-full font-bold">
                        🕒 Asia/Riyadh
                    </span>
                    {isNonSar && (
                        <span className="text-[11px] px-2 py-0.5 bg-amber-100 text-amber-800 rounded-full font-bold">
                            {acc.currency_native} → SAR (×{Number(acc.fx_rate || 1).toFixed(3)})
                        </span>
                    )}
                </div>
            </div>

            {/* 3-col KPI grid: Today / Month / 30d */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
                <div
                    className="rounded-lg border border-border bg-yellow-50/40 p-3"
                    data-testid={`snap-account-today-${acc.ad_account_id}`}
                >
                    <div className="text-[11px] text-muted-foreground font-semibold mb-1">صرف اليوم (الرياض)</div>
                    <div className="text-2xl font-extrabold text-foreground tabular-nums">
                        {fmtSAR(acc.today?.spend_sar)} <span className="text-sm font-bold text-muted-foreground">ر.س</span>
                    </div>
                    {isNonSar && (
                        <div className="text-[11px] text-amber-700 mt-0.5 tabular-nums">
                            ≈ {fmtNative(acc.today?.spend_native, acc.currency_native)}
                        </div>
                    )}
                </div>
                <div
                    className="rounded-lg border border-border bg-white p-3"
                    data-testid={`snap-account-month-${acc.ad_account_id}`}
                >
                    <div className="text-[11px] text-muted-foreground font-semibold mb-1">الصرف الشهري</div>
                    <div className="text-2xl font-extrabold text-foreground tabular-nums">
                        {fmtSAR(acc.month?.spend_sar)} <span className="text-sm font-bold text-muted-foreground">ر.س</span>
                    </div>
                </div>
                <div
                    className="rounded-lg border border-border bg-white p-3"
                    data-testid={`snap-account-30d-${acc.ad_account_id}`}
                >
                    <div className="text-[11px] text-muted-foreground font-semibold mb-1">آخر 30 يوم</div>
                    <div className="text-2xl font-extrabold text-foreground tabular-nums">
                        {fmtSAR(acc.last_30d?.spend_sar)} <span className="text-sm font-bold text-muted-foreground">ر.س</span>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                <div className="flex items-center gap-2 text-muted-foreground">
                    <CurrencyCircleDollar size={14} weight="bold" className="flex-shrink-0" />
                    <span>العملة الأصلية: <strong className="text-foreground" dir="ltr">{acc.currency_native || "SAR"}</strong></span>
                    {Number(acc.fx_rate || 1) !== 1 && (
                        <span>· سعر الصرف: <strong className="text-foreground tabular-nums" dir="ltr">×{Number(acc.fx_rate).toFixed(3)}</strong></span>
                    )}
                </div>
                <div className="flex items-center gap-2 text-muted-foreground">
                    <Clock size={14} weight="bold" className="flex-shrink-0" />
                    <span>آخر مزامنة: <strong className="text-foreground">{fmtDateAr(acc.last_sync_at)}</strong></span>
                </div>
                {acc.timezone && acc.timezone !== "Asia/Riyadh" && (
                    <div className="flex items-center gap-2 text-muted-foreground sm:col-span-2">
                        <Buildings size={14} weight="bold" className="flex-shrink-0" />
                        <span>TZ حساب Snap: <strong dir="ltr">{acc.timezone}</strong> — لكننا نجمع الصرف بتوقيت الرياض.</span>
                    </div>
                )}
            </div>
        </div>
    );
}

export default function SnapchatAccounts() {
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/snapchat/accounts-summary");
            setSummary(data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر جلب البيانات");
        } finally { setLoading(false); }
    };

    const syncAll = async () => {
        if (!summary || summary.count === 0) {
            toast.error("لا توجد حسابات مفعَّلة. اذهب للإعدادات أولاً واختر حسابات.");
            return;
        }
        setSyncing(true);
        toast.loading("جاري مزامنة كل الحسابات…", { id: "snap-sync-all" });
        try {
            const { data } = await api.post("/snapchat/sync-all-accounts", { days: 30 });
            const errsCount = (data.errors || []).length;
            if (errsCount > 0) {
                toast.warning(
                    `تمت المزامنة الجزئية — ${data.accounts_synced} حساب، ${errsCount} خطأ`,
                    { id: "snap-sync-all", duration: 8000 },
                );
            } else {
                toast.success(
                    `✓ تمت مزامنة ${data.accounts_synced} حساب بنجاح (بتوقيت الرياض → SAR)`,
                    { id: "snap-sync-all", duration: 6000 },
                );
            }
            await load();
        } catch (err) {
            toast.error(
                formatApiErrorDetail(err.response?.data?.detail) || "تعذرت المزامنة",
                { id: "snap-sync-all", duration: 8000 },
            );
        } finally { setSyncing(false); }
    };

    useEffect(() => { load(); }, []);

    return (
        <div className="space-y-6" data-testid="snapchat-accounts-page">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight flex items-center gap-3 flex-wrap" style={{ fontFamily: "Tajawal" }}>
                        <Ghost size={36} weight="fill" className="text-yellow-500" />
                        حسابات Snapchat Ads
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        تفاصيل صرف كل حساب إعلاني — بتوقيت <strong>Asia/Riyadh</strong> ومحوّل إلى <strong>SAR</strong>.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={syncAll}
                    disabled={syncing || loading || !summary || summary.count === 0}
                    className="px-4 py-2.5 bg-yellow-500 hover:bg-yellow-600 text-white rounded-lg font-bold text-sm inline-flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed w-full md:w-auto transition-colors"
                    data-testid="snap-sync-all-btn"
                >
                    <ArrowsClockwise size={16} weight="bold" className={syncing ? "animate-spin" : ""} />
                    {syncing ? "جاري المزامنة…" : "مزامنة كل الحسابات (30 يوم)"}
                </button>
            </div>

            {/* Cross-account totals card */}
            {summary && summary.count > 0 && (
                <div className="rounded-xl border-2 border-yellow-300 bg-gradient-to-br from-yellow-50 to-amber-50 p-4 sm:p-6" data-testid="snap-totals-card">
                    <div className="text-xs font-bold text-amber-800 mb-2 flex items-center gap-2 flex-wrap">
                        <span>إجمالي جميع الحسابات ({summary.count} حساب)</span>
                        <span className="text-[10px] px-1.5 py-0.5 bg-white/80 text-amber-800 rounded-full">SAR · Asia/Riyadh</span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        <div className="bg-white/70 rounded-lg p-3" data-testid="snap-totals-today">
                            <div className="text-[11px] text-muted-foreground font-semibold mb-1">صرف اليوم</div>
                            <div className="text-3xl font-extrabold text-amber-900 tabular-nums">
                                {fmtSAR(summary.today?.spend_sar)} <span className="text-base">ر.س</span>
                            </div>
                        </div>
                        <div className="bg-white/70 rounded-lg p-3" data-testid="snap-totals-month">
                            <div className="text-[11px] text-muted-foreground font-semibold mb-1">الصرف الشهري</div>
                            <div className="text-3xl font-extrabold text-amber-900 tabular-nums">
                                {fmtSAR(summary.month?.spend_sar)} <span className="text-base">ر.س</span>
                            </div>
                        </div>
                        <div className="bg-white/70 rounded-lg p-3" data-testid="snap-totals-30d">
                            <div className="text-[11px] text-muted-foreground font-semibold mb-1">آخر 30 يوم</div>
                            <div className="text-3xl font-extrabold text-amber-900 tabular-nums">
                                {fmtSAR(summary.last_30d?.spend_sar)} <span className="text-base">ر.س</span>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Per-account cards */}
            {loading ? (
                <div className="text-center py-12 text-muted-foreground" data-testid="snap-accounts-loading">
                    جاري التحميل…
                </div>
            ) : summary && summary.count === 0 ? (
                <div
                    className="rounded-xl border-2 border-dashed border-border bg-white p-8 text-center"
                    data-testid="snap-accounts-empty-state"
                >
                    <Ghost size={48} weight="duotone" className="text-yellow-500 mx-auto mb-3" />
                    <h3 className="text-lg font-bold mb-2">لا توجد حسابات مفعَّلة بعد</h3>
                    <p className="text-sm text-muted-foreground mb-4 leading-relaxed">
                        اذهب لصفحة الإعدادات، اربط Snapchat، ثم اختر حساباً واحداً أو أكثر لتفعيله.
                    </p>
                    <Link
                        to="/settings"
                        className="inline-flex items-center gap-2 px-4 py-2 bg-brand text-white rounded-lg font-bold text-sm hover:opacity-90"
                        data-testid="snap-go-settings-link"
                    >
                        فتح الإعدادات →
                    </Link>
                </div>
            ) : (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4" data-testid="snap-accounts-grid">
                    {(summary?.accounts || []).map((acc) => (
                        <AccountCard key={acc.ad_account_id} acc={acc} />
                    ))}
                </div>
            )}
        </div>
    );
}

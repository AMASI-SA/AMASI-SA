/**
 * Snapchat account cards plus the Mezan V2 hybrid operational summary.
 *
 * The separated V2 view renders one aggregate card first:
 * - spend, impressions and clicks from Snapchat Ads API;
 * - actual orders and revenue from Salla's reported order source;
 * - Snapchat-attributed conversions as a separate comparison.
 *
 * Per-account cards remain provider-only because Salla orders cannot be split
 * safely between ad accounts without campaign/click evidence.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowsClockwise, Ghost, ShoppingBag, CurrencyDollar, Coins,
} from "@phosphor-icons/react";
import api from "../lib/api";
import SnapchatHybridSummaryCard from "./SnapchatHybridSummaryCard";


function fmt(n) {
    return Number(n || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}


function fmtOrders(value) {
    return Number(value || 0).toLocaleString("en-US", {
        maximumFractionDigits: 0,
    });
}


function SnapchatPeriodMetric({ label, value, hint, tone, testid }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50/70 text-violet-900",
        sky: "border-sky-200 bg-sky-50/70 text-sky-900",
        emerald: "border-emerald-200 bg-emerald-50/70 text-emerald-900",
        amber: "border-amber-200 bg-amber-50/70 text-amber-900",
    };
    return (
        <div className={`rounded-xl border p-3 sm:p-4 ${tones[tone] || tones.amber}`} data-testid={testid}>
            <div className="text-xs font-bold opacity-70">{label}</div>
            <div className="num mt-2 text-xl font-extrabold sm:text-2xl">{value}</div>
            {hint && <div className="mt-1 text-[10px] font-semibold opacity-60">{hint}</div>}
        </div>
    );
}


function SnapchatAccountPeriod({ accountId, label, period, periodKey }) {
    const spend = Number(period?.spend || 0);
    const orders = Number(period?.orders || 0);
    const revenue = Number(period?.revenue || 0);
    const roas = spend > 0 ? Number(period?.roas || 0) : null;
    const costPerOrder = period?.cost_per_order;
    return (
        <section>
            <div className="mb-2 text-xs font-extrabold text-slate-600">{label}</div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-5 lg:gap-3">
                <SnapchatPeriodMetric
                    label="الصرف"
                    value={`${fmt(spend)} ر.س`}
                    tone="violet"
                    testid={`snap-v2-${accountId}-${periodKey}-spend`}
                />
                <SnapchatPeriodMetric
                    label="التحويلات المنسوبة"
                    value={fmtOrders(orders)}
                    hint="من Snapchat API لهذا الحساب"
                    tone="sky"
                    testid={`snap-v2-${accountId}-${periodKey}-orders`}
                />
                <SnapchatPeriodMetric
                    label="المبيعات المنسوبة"
                    value={`${fmt(revenue)} ر.س`}
                    hint="من Snapchat API لهذا الحساب"
                    tone="emerald"
                    testid={`snap-v2-${accountId}-${periodKey}-revenue`}
                />
                <SnapchatPeriodMetric
                    label="ROAS المنسوب"
                    value={roas == null ? "—" : `${roas.toFixed(2)}×`}
                    tone="amber"
                    testid={`snap-v2-${accountId}-${periodKey}-roas`}
                />
                <SnapchatPeriodMetric
                    label="CPA المنسوب"
                    value={costPerOrder == null ? "—" : `${fmt(costPerOrder)} ر.س`}
                    tone="amber"
                    testid={`snap-v2-${accountId}-${periodKey}-cpo`}
                />
            </div>
        </section>
    );
}


function SnapchatSeparatedAccountCard({ account, monthStart, today }) {
    const accountId = account.id || account.external_account_id;
    return (
        <article
            className="rounded-2xl border-2 border-yellow-300 bg-gradient-to-br from-yellow-50 via-white to-yellow-50/40 p-4 shadow-sm sm:p-6"
            data-testid={`snap-v2-account-card-${accountId}`}
        >
            <header className="flex flex-col gap-3 border-b border-yellow-200 pb-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-yellow-400 text-black">
                        <Ghost size={24} weight="fill" />
                    </span>
                    <div className="min-w-0">
                        <h3 className="truncate text-lg font-extrabold text-slate-900 sm:text-xl" title={account.name}>
                            {account.name || accountId}
                        </h3>
                        <div className="truncate font-mono text-[10px] text-slate-400" dir="ltr">{accountId}</div>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2 text-[10px] font-extrabold">
                    <span className="rounded-full border border-yellow-200 bg-white px-2.5 py-1 text-yellow-900">{account.currency || "SAR"}</span>
                    <span className="rounded-full border border-sky-200 bg-white px-2.5 py-1 text-sky-700">{account.timezone || "Asia/Riyadh"}</span>
                </div>
            </header>

            <div className="mt-4 space-y-5">
                <SnapchatAccountPeriod
                    accountId={accountId}
                    label={`اليوم (${account.today?.date || today || "—"})`}
                    period={account.today}
                    periodKey="today"
                />
                <SnapchatAccountPeriod
                    accountId={accountId}
                    label={`هذا الشهر (منذ ${account.month?.start || monthStart || "—"})`}
                    period={account.month}
                    periodKey="month"
                />
            </div>

            <footer className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-yellow-200 pt-3 text-[10px]">
                <span className="text-slate-500">المصدر: Snapchat Marketing API — هذا الحساب فقط</span>
                <Link
                    to={`/ads-manager?provider=snapchat&account=${encodeURIComponent(accountId)}`}
                    className="font-extrabold text-yellow-800 hover:text-yellow-950 hover:underline"
                    data-testid={`snap-v2-account-details-${accountId}`}
                >
                    تفاصيل الحملات ←
                </Link>
            </footer>
        </article>
    );
}


export function SnapchatSeparatedAccountsView({ data, onRefresh, refreshing, lastFetched }) {
    const accounts = Array.isArray(data?.accounts) ? data.accounts : [];
    if (!accounts.length) return null;
    return (
        <section className="space-y-4" data-testid="snapchat-v2-separated-accounts">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <Ghost size={22} weight="fill" className="text-yellow-500" />
                        <h2 className="text-xl font-extrabold text-slate-900 sm:text-2xl">حسابات Snapchat المنفصلة</h2>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">هذه البطاقات تعرض أرقام Snapchat API لكل حساب؛ الطلبات الفعلية من سلة تظهر في البطاقة الإجمالية أعلاه.</p>
                    {lastFetched && (
                        <p className="mt-1 text-[10px] font-bold text-emerald-700">
                            آخر قراءة: {new Date(lastFetched).toLocaleTimeString("ar-SA", { hour: "2-digit", minute: "2-digit" })}
                        </p>
                    )}
                </div>
                <button
                    type="button"
                    onClick={onRefresh}
                    disabled={refreshing}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-yellow-400 px-4 py-2.5 text-sm font-extrabold text-black hover:bg-yellow-500 disabled:opacity-60"
                    data-testid="snapchat-v2-accounts-refresh"
                >
                    <ArrowsClockwise size={16} weight="bold" className={refreshing ? "animate-spin" : ""} />
                    {refreshing ? "جارٍ التحديث…" : "تحديث بيانات سناب الآن"}
                </button>
            </div>
            <div className="space-y-4" data-testid="snapchat-v2-accounts-list">
                {accounts.map((account) => (
                    <SnapchatSeparatedAccountCard
                        key={account.id || account.external_account_id}
                        account={account}
                        monthStart={data.month_start}
                        today={data.today}
                    />
                ))}
            </div>
        </section>
    );
}


export default function SnapchatAccountsCards({
    refreshSignal = 0,
    endpoint = "/dashboard/snapchat-accounts-summary",
    variant = "compact",
}) {
    const [data, setData] = useState(null);
    const [hybridData, setHybridData] = useState(null);
    const [busy, setBusy] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [lastFetched, setLastFetched] = useState(null);

    const fetchData = useCallback(async ({ manual = false } = {}) => {
        if (manual) setRefreshing(true);
        try {
            const requests = [api.get(endpoint)];
            if (variant === "separated") {
                requests.push(api.get("/dashboard-v2/snapchat-summary"));
            }
            const results = await Promise.allSettled(requests);
            let updated = false;
            if (results[0]?.status === "fulfilled") {
                setData(results[0].value.data);
                updated = true;
            }
            if (variant === "separated" && results[1]?.status === "fulfilled") {
                setHybridData(results[1].value.data);
                updated = true;
            }
            if (updated) setLastFetched(Date.now());
        } finally {
            setBusy(false);
            if (manual) setRefreshing(false);
        }
    }, [endpoint, variant]);

    useEffect(() => {
        let mounted = true;
        (async () => {
            await fetchData();
            if (!mounted) return;
        })();
        return () => { mounted = false; };
    }, [refreshSignal, fetchData]);

    useEffect(() => {
        const id = setInterval(() => {
            if (document.visibilityState === "visible") fetchData();
        }, 30 * 60 * 1000);
        return () => clearInterval(id);
    }, [fetchData]);

    if (busy && !data && !hybridData) return null;
    if (variant === "separated") {
        return (
            <div className="space-y-6" data-testid="snapchat-v2-hybrid-and-accounts">
                <SnapchatHybridSummaryCard data={hybridData} />
                <SnapchatSeparatedAccountsView
                    data={data}
                    onRefresh={() => fetchData({ manual: true })}
                    refreshing={refreshing}
                    lastFetched={lastFetched}
                />
            </div>
        );
    }
    if (!data?.accounts || data.accounts.length < 2) return null;

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-4 sm:p-5 shadow-sm" data-testid="snapchat-accounts-cards">
            <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                    <Ghost size={20} weight="fill" className="text-yellow-500" />
                    <h3 className="text-sm sm:text-base font-extrabold text-slate-900">حسابات سناب شات — تفصيلي</h3>
                </div>
                <div className="text-[10px] text-slate-500" data-testid="snap-accounts-meta">
                    الصرف خلال {data.month_start?.slice(0, 7)} • {data.accounts.length} حساب
                    {lastFetched && (
                        <span className="ml-2 text-emerald-700">· آخر تحديث {new Date(lastFetched).toLocaleTimeString("ar-SA", { hour: "2-digit", minute: "2-digit" })}</span>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" data-testid="snapchat-accounts-grid">
                {data.accounts.map((a) => {
                    const hasLimit = a.credit_limit != null && Number(a.credit_limit) > 0;
                    const usage = hasLimit ? (Number(a.open_debt || 0) / Number(a.credit_limit)) * 100 : 0;
                    const threshold = Number(a.alert_threshold_pct ?? 80);
                    const isOverThreshold = hasLimit && usage >= threshold;
                    const isOverLimit = hasLimit && usage >= 100;
                    return (
                        <div
                            key={a.id}
                            className="border border-slate-200 rounded-xl p-3 hover:border-yellow-300 transition-colors"
                            data-testid={`snap-acc-card-${a.id}`}
                        >
                            <div className="flex items-start justify-between mb-2">
                                <div className="min-w-0">
                                    <div className="font-bold text-sm text-slate-900 truncate" title={a.name} data-testid={`snap-acc-name-${a.id}`}>
                                        {a.name}
                                    </div>
                                    {a.external_account_id && (
                                        <div className="text-[10px] text-slate-400 font-mono truncate" dir="ltr">{a.external_account_id}</div>
                                    )}
                                </div>
                                <span className="text-[10px] bg-yellow-50 text-yellow-800 border border-yellow-200 px-1.5 py-0.5 rounded font-bold flex-shrink-0">
                                    {a.spend_share_pct?.toFixed?.(1) ?? a.spend_share_pct}%
                                </span>
                            </div>

                            <div className="grid grid-cols-2 gap-2">
                                <div className="bg-violet-50 border border-violet-200 rounded p-2">
                                    <div className="text-[10px] font-bold text-violet-700 inline-flex items-center gap-1">
                                        <Coins size={10} weight="bold" /> الصرف
                                    </div>
                                    <div className="num font-extrabold text-violet-900 mt-0.5" data-testid={`snap-acc-spend-${a.id}`}>{fmt(a.spend)}</div>
                                    <div className="text-[9px] text-violet-600">ر.س</div>
                                </div>
                                <div className="bg-sky-50 border border-sky-200 rounded p-2">
                                    <div className="text-[10px] font-bold text-sky-700 inline-flex items-center gap-1">
                                        <ShoppingBag size={10} weight="bold" /> التحويلات
                                    </div>
                                    <div className="num font-extrabold text-sky-900 mt-0.5" data-testid={`snap-acc-orders-${a.id}`}>{a.orders}</div>
                                    <div className="text-[9px] text-sky-600">منسوبة بواسطة سناب</div>
                                </div>
                                <div className="bg-amber-50 border border-amber-200 rounded p-2">
                                    <div className="text-[10px] font-bold text-amber-700">CPA المنسوب</div>
                                    <div className="num font-extrabold text-amber-900 mt-0.5" data-testid={`snap-acc-cpo-${a.id}`}>
                                        {a.cost_per_order != null ? fmt(a.cost_per_order) : "—"}
                                    </div>
                                    <div className="text-[9px] text-amber-600">ر.س / تحويل</div>
                                </div>
                                <div className="bg-emerald-50 border border-emerald-200 rounded p-2">
                                    <div className="text-[10px] font-bold text-emerald-700 inline-flex items-center gap-1">
                                        <CurrencyDollar size={10} weight="bold" /> المبيعات المنسوبة
                                    </div>
                                    <div className="num font-extrabold text-emerald-900 mt-0.5" data-testid={`snap-acc-revenue-${a.id}`}>{fmt(a.revenue)}</div>
                                    <div className="text-[9px] text-emerald-600">ROAS {a.roas?.toFixed?.(2) ?? a.roas}×</div>
                                </div>
                            </div>

                            {hasLimit && (
                                <div className="mt-2.5 pt-2.5 border-t border-slate-100 space-y-1" data-testid={`snap-acc-limit-${a.id}`}>
                                    <div className="flex items-center justify-between text-[10px]">
                                        <span className="text-slate-500">المديونية / الحد</span>
                                        <span className={`font-extrabold ${isOverLimit ? "text-rose-700" : isOverThreshold ? "text-amber-700" : "text-emerald-700"}`}>
                                            {fmt(a.open_debt)} / {fmt(a.credit_limit)} ({usage.toFixed(0)}%)
                                        </span>
                                    </div>
                                    <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                                        <div
                                            className={`h-full transition-all ${isOverLimit ? "bg-rose-500" : isOverThreshold ? "bg-amber-500" : "bg-emerald-500"}`}
                                            style={{ width: `${Math.min(usage, 100)}%` }}
                                        ></div>
                                    </div>
                                </div>
                            )}

                            <Link
                                to="/ad-accounts"
                                className="block mt-2.5 text-center text-[10px] text-indigo-600 hover:text-indigo-800 hover:underline font-bold"
                                data-testid={`snap-acc-open-${a.id}`}
                            >
                                إدارة الحساب →
                            </Link>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowsClockwise,
    Coins,
    CurrencyDollar,
    Ghost,
    ShoppingBag,
    TrendUp,
} from "@phosphor-icons/react";
import api from "../lib/api";

const ENDPOINT = "/integrations-v2/snapchat_ads/accounts-dashboard-summary";
const POLL_MS = 5 * 60 * 1000;

function money(value) {
    return Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function integer(value) {
    return Number(value || 0).toLocaleString("en-US", {
        maximumFractionDigits: 0,
    });
}

function Metric({ label, value, hint, tone = "slate", Icon, testid }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50 text-violet-900",
        sky: "border-sky-200 bg-sky-50 text-sky-900",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        slate: "border-slate-200 bg-slate-50 text-slate-900",
    };
    return (
        <div className={`rounded-xl border p-3 ${tones[tone] || tones.slate}`} data-testid={testid}>
            <div className="mb-1 flex items-center gap-1.5 text-[11px] font-bold opacity-75">
                {Icon ? <Icon size={13} weight="bold" /> : null}
                <span>{label}</span>
            </div>
            <div className="num text-xl font-extrabold" style={{ fontFamily: "Tajawal" }}>{value}</div>
            {hint ? <div className="mt-0.5 text-[10px] opacity-70">{hint}</div> : null}
        </div>
    );
}

function PeriodMetrics({ account, periodKey, label }) {
    const metrics = account?.[periodKey] || {};
    const prefix = `snap-standalone-${account.id}-${periodKey}`;
    return (
        <div data-testid={`${prefix}-section`}>
            <div className="mb-2 text-xs font-extrabold text-slate-700">{label}</div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
                <Metric
                    label="الصرف"
                    value={`${money(metrics.spend)} ر.س`}
                    tone="violet"
                    Icon={Coins}
                    testid={`${prefix}-spend`}
                />
                <Metric
                    label="الطلبات"
                    value={integer(metrics.orders)}
                    hint="تحويلات Snapchat"
                    tone="sky"
                    Icon={ShoppingBag}
                    testid={`${prefix}-orders`}
                />
                <Metric
                    label="المبيعات"
                    value={`${money(metrics.revenue)} ر.س`}
                    tone="emerald"
                    Icon={CurrencyDollar}
                    testid={`${prefix}-revenue`}
                />
                <Metric
                    label="ROAS"
                    value={Number(metrics.spend || 0) > 0 ? `${Number(metrics.roas || 0).toFixed(2)}×` : "—"}
                    tone={Number(metrics.roas || 0) >= 2 ? "emerald" : "amber"}
                    Icon={TrendUp}
                    testid={`${prefix}-roas`}
                />
                <Metric
                    label="متوسط تكلفة الطلب"
                    value={metrics.cost_per_order != null ? `${money(metrics.cost_per_order)} ر.س` : "—"}
                    tone="amber"
                    testid={`${prefix}-cpo`}
                />
            </div>
        </div>
    );
}

export function SnapchatStandaloneAccountCardsContent({ data, busy = false, onRefresh }) {
    const accounts = Array.isArray(data?.accounts) ? data.accounts : [];
    if (!accounts.length) return null;

    return (
        <div className="space-y-5" data-testid="snapchat-standalone-accounts">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h2 className="flex items-center gap-2 text-xl font-extrabold text-slate-900" style={{ fontFamily: "Tajawal" }}>
                        <Ghost size={23} weight="fill" className="text-yellow-500" />
                        حسابات Snapchat المنفصلة
                    </h2>
                    <p className="mt-1 text-xs text-slate-500">
                        كل بطاقة تقرأ حسابًا إعلانيًا واحدًا فقط؛ لا يوجد دمج بين الحسابات.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={onRefresh}
                    disabled={busy}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-yellow-400 px-4 py-2 text-sm font-extrabold text-black transition hover:bg-yellow-500 disabled:opacity-50"
                    data-testid="snapchat-standalone-refresh"
                >
                    <ArrowsClockwise size={17} weight="bold" className={busy ? "animate-spin" : ""} />
                    {busy ? "جاري التحديث…" : "تحديث الحسابات الآن"}
                </button>
            </div>

            {accounts.map((account) => (
                <section
                    key={account.id}
                    className="rounded-2xl border-2 border-yellow-300 bg-gradient-to-br from-yellow-50 via-white to-amber-50 p-4 shadow-sm sm:p-6"
                    data-testid={`snapchat-standalone-account-${account.id}`}
                >
                    <div className="mb-5 flex flex-col gap-2 border-b border-yellow-200 pb-4 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                            <div className="flex items-center gap-2">
                                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-yellow-400 text-xl">👻</div>
                                <div className="min-w-0">
                                    <h3 className="truncate text-lg font-extrabold text-slate-900" title={account.name}>
                                        {account.name}
                                    </h3>
                                    <div className="truncate font-mono text-[10px] text-slate-400" dir="ltr">
                                        {account.external_account_id || account.id}
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div className="flex flex-wrap gap-1.5 text-[10px] font-bold">
                            <span className="rounded-full border border-yellow-200 bg-white px-2 py-1 text-yellow-800">
                                {account.currency_native || "SAR"}
                            </span>
                            <span className="rounded-full border border-blue-200 bg-white px-2 py-1 text-blue-700">
                                {account.report_timezone || "Asia/Riyadh"}
                            </span>
                        </div>
                    </div>

                    <div className="space-y-5">
                        <PeriodMetrics account={account} periodKey="today" label={`اليوم (${data.today_date || ""})`} />
                        <PeriodMetrics account={account} periodKey="month" label={`هذا الشهر (منذ ${data.month_start || ""})`} />
                    </div>

                    <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-yellow-200 pt-3 text-[10px] text-slate-500">
                        <span>المصدر: Snapchat Marketing API — الحساب نفسه فقط</span>
                        <Link to="/ads-manager" className="font-extrabold text-yellow-800 hover:underline">
                            تفاصيل الحملات ←
                        </Link>
                    </div>
                </section>
            ))}
        </div>
    );
}

export default function SnapchatStandaloneAccountCards({ onReadyChange }) {
    const [data, setData] = useState(null);
    const [busy, setBusy] = useState(true);

    const load = useCallback(async ({ force = false } = {}) => {
        setBusy(true);
        try {
            const response = await api.get(ENDPOINT, {
                params: force ? { force: true } : undefined,
            });
            const next = response?.data || null;
            setData(next);
            onReadyChange?.(Boolean(next?.accounts?.length), next);
        } catch (_) {
            onReadyChange?.(false, null);
        } finally {
            setBusy(false);
        }
    }, [onReadyChange]);

    useEffect(() => {
        load();
    }, [load]);

    useEffect(() => {
        const timer = window.setInterval(() => {
            if (document.visibilityState === "visible") load();
        }, POLL_MS);
        return () => window.clearInterval(timer);
    }, [load]);

    if (!data?.accounts?.length) return null;
    return (
        <SnapchatStandaloneAccountCardsContent
            data={data}
            busy={busy}
            onRefresh={() => load({ force: true })}
        />
    );
}

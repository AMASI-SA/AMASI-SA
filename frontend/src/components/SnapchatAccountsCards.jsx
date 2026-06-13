/**
 * Iter-159j — Per-Snapchat-Ad-Account Cards
 * ----------------------------------------
 * Renders one card per Snapchat ad account showing:
 *   • Spend (this month)
 *   • Orders (prorated by spend share)
 *   • Avg cost per order
 *   • Sales/revenue
 *   • Credit-limit progress bar (links to /ad-accounts)
 *
 * Sits ALONGSIDE the existing aggregated SnapchatOfficialCard — does NOT
 * replace it.  Renders nothing when the user has < 2 Snapchat accounts
 * (the aggregated card is sufficient in that case).
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Ghost, ShoppingBag, CurrencyDollar, Coins } from "@phosphor-icons/react";
import api from "../lib/api";


function fmt(n) {
    return Number(n || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}


export default function SnapchatAccountsCards() {
    const [data, setData] = useState(null);
    const [busy, setBusy] = useState(true);

    useEffect(() => {
        let mounted = true;
        (async () => {
            try {
                const { data } = await api.get("/dashboard/snapchat-accounts-summary");
                if (mounted) setData(data);
            } catch (_) { /* silent */ }
            finally { if (mounted) setBusy(false); }
        })();
        return () => { mounted = false; };
    }, []);

    if (busy || !data) return null;
    if (!data.accounts || data.accounts.length < 2) return null;  // no value to split when only one account

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-4 sm:p-5 shadow-sm" data-testid="snapchat-accounts-cards">
            <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                    <Ghost size={20} weight="fill" className="text-yellow-500" />
                    <h3 className="text-sm sm:text-base font-extrabold text-slate-900">حسابات سناب شات — تفصيلي</h3>
                </div>
                <div className="text-[10px] text-slate-500">
                    الصرف خلال {data.month_start?.slice(0, 7)} • {data.accounts.length} حساب
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
                                        <ShoppingBag size={10} weight="bold" /> الطلبات
                                    </div>
                                    <div className="num font-extrabold text-sky-900 mt-0.5" data-testid={`snap-acc-orders-${a.id}`}>{a.orders}</div>
                                    <div className="text-[9px] text-sky-600">طلب</div>
                                </div>
                                <div className="bg-amber-50 border border-amber-200 rounded p-2">
                                    <div className="text-[10px] font-bold text-amber-700">متوسط تكلفة الطلب</div>
                                    <div className="num font-extrabold text-amber-900 mt-0.5" data-testid={`snap-acc-cpo-${a.id}`}>
                                        {a.cost_per_order != null ? fmt(a.cost_per_order) : "—"}
                                    </div>
                                    <div className="text-[9px] text-amber-600">ر.س / طلب</div>
                                </div>
                                <div className="bg-emerald-50 border border-emerald-200 rounded p-2">
                                    <div className="text-[10px] font-bold text-emerald-700 inline-flex items-center gap-1">
                                        <CurrencyDollar size={10} weight="bold" /> المبيعات
                                    </div>
                                    <div className="num font-extrabold text-emerald-900 mt-0.5" data-testid={`snap-acc-revenue-${a.id}`}>{fmt(a.revenue)}</div>
                                    <div className="text-[9px] text-emerald-600">ROAS {a.roas?.toFixed?.(2) ?? a.roas}×</div>
                                </div>
                            </div>

                            {/* Credit-limit usage strip */}
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

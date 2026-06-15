// Iter-206 — Advertising Expenses Report (SSOT-backed)
//
// Reads from /api/accounting/reports/advertising-expenses which itself
// derives every number from `general_ledger.entity_type=expense
// .entity_id=advertising` rows (posted by Iter-205). No legacy
// `ad_account_ledger`, no `daily_costs`. Pure SSOT.
//
// Layout: date range, total card, by-platform grid, by-account table,
// monthly bar chart, daily trend list.

import React, { useEffect, useState, useMemo } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (n) => Number(n || 0).toLocaleString(
    "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const PROVIDER_LABEL = {
    snapchat: "Snapchat",
    meta: "Meta / Facebook",
    tiktok: "TikTok",
    google: "Google Ads",
    twitter: "X (Twitter)",
    unknown: "غير محدد",
};

const PROVIDER_COLOR = {
    snapchat: "yellow",
    meta: "sky",
    tiktok: "rose",
    google: "violet",
    twitter: "slate",
    unknown: "slate",
};

function defaultRange() {
    const today = new Date();
    const first = new Date(today.getFullYear(), today.getMonth(), 1);
    const iso = (d) => d.toISOString().slice(0, 10);
    return { from: iso(first), to: iso(today) };
}

export default function AdvertisingExpensesReport() {
    const [{ from, to }, setRange] = useState(defaultRange());
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get(
                "/accounting/reports/advertising-expenses",
                { params: { from_date: from, to_date: to } },
            );
            setData(data);
        } catch (e) {
            toast.error("فشل تحميل التقرير");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

    const maxMonthly = useMemo(
        () => Math.max(0, ...(data?.by_month || []).map((m) => m.total)),
        [data],
    );

    return (
        <div className="p-6 max-w-6xl mx-auto" data-testid="advertising-expenses-report">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex items-start justify-between mb-4 flex-wrap gap-3">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            📊 تقرير المصروفات الإعلانية
                        </h1>
                        <p className="text-xs text-slate-500 mt-1">
                            مبني على القيد الموحد (general_ledger) — `expense.advertising` فقط.
                            كل ريال يظهر هنا له قيد محاسبي مزدوج.
                        </p>
                    </div>
                    <div className="flex items-end gap-2">
                        <Field label="من تاريخ"
                            value={from}
                            onChange={(v) => setRange((r) => ({ ...r, from: v }))}
                            testid="adv-from" />
                        <Field label="إلى تاريخ"
                            value={to}
                            onChange={(v) => setRange((r) => ({ ...r, to: v }))}
                            testid="adv-to" />
                        <button onClick={load}
                            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-lg"
                            data-testid="adv-load-btn">
                            {loading ? "جاري التحميل..." : "تحديث"}
                        </button>
                    </div>
                </div>

                {/* Total card */}
                <div className="bg-gradient-to-br from-indigo-50 to-violet-50 border border-indigo-200 rounded-xl p-5 mb-5">
                    <div className="text-xs font-bold text-indigo-700 mb-1">إجمالي المصروفات الإعلانية للفترة</div>
                    <div className="num text-3xl font-extrabold text-indigo-900" data-testid="adv-total">
                        {fmt(data?.total)} ر.س
                    </div>
                    <div className="text-[11px] text-indigo-600 mt-1">
                        {from} → {to} · {data?.daily?.length || 0} يوم نشط
                    </div>
                </div>

                {/* By platform */}
                <Section title="حسب المنصة" testid="adv-by-platform">
                    {(data?.by_platform || []).length === 0
                        ? <Empty />
                        : (
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                {data.by_platform.map((p) => (
                                    <PlatformCard key={p.key} platform={p}
                                        total={data.total} />
                                ))}
                            </div>
                        )}
                </Section>

                {/* By ad account */}
                <Section title="حسب الحساب الإعلاني" testid="adv-by-account">
                    {(data?.by_ad_account || []).length === 0
                        ? <Empty />
                        : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                                        <tr>
                                            <th className="text-right py-2 px-3 font-bold">الحساب</th>
                                            <th className="text-right py-2 px-3 font-bold">المنصة</th>
                                            <th className="text-left py-2 px-3 font-bold">عدد العمليات</th>
                                            <th className="text-left py-2 px-3 font-bold">الإجمالي (ر.س)</th>
                                            <th className="text-left py-2 px-3 font-bold">النسبة</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.by_ad_account.map((a) => {
                                            const pct = data.total > 0 ? (a.total / data.total) * 100 : 0;
                                            return (
                                                <tr key={a.ad_account_id}
                                                    className="border-b border-slate-100 hover:bg-slate-50"
                                                    data-testid={`adv-row-${a.ad_account_id}`}>
                                                    <td className="py-2 px-3 font-bold text-slate-900">{a.name}</td>
                                                    <td className="py-2 px-3 text-slate-600 text-xs">
                                                        {PROVIDER_LABEL[a.ad_provider] || a.ad_provider}
                                                    </td>
                                                    <td className="text-left py-2 px-3 num">{a.count}</td>
                                                    <td className="text-left py-2 px-3 num font-extrabold text-indigo-700">
                                                        {fmt(a.total)}
                                                    </td>
                                                    <td className="text-left py-2 px-3 num text-[11px] text-slate-500">
                                                        {pct.toFixed(1)}%
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        )}
                </Section>

                {/* By month */}
                <Section title="حسب الشهر" testid="adv-by-month">
                    {(data?.by_month || []).length === 0
                        ? <Empty />
                        : (
                            <div className="space-y-2">
                                {data.by_month.map((m) => {
                                    const w = maxMonthly > 0 ? (m.total / maxMonthly) * 100 : 0;
                                    return (
                                        <div key={m.month} className="flex items-center gap-3"
                                            data-testid={`adv-month-${m.month}`}>
                                            <div className="w-20 text-xs text-slate-600 font-bold">{m.month}</div>
                                            <div className="flex-1 h-6 bg-slate-100 rounded-lg overflow-hidden relative">
                                                <div className="h-full bg-gradient-to-r from-indigo-400 to-violet-500"
                                                    style={{ width: `${w}%` }}></div>
                                                <span className="absolute inset-0 flex items-center px-3 text-[11px] font-bold text-slate-800">
                                                    {fmt(m.total)} ر.س · {m.count} قيد
                                                </span>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                </Section>
            </div>
        </div>
    );
}

function Field({ label, value, onChange, testid }) {
    return (
        <div>
            <label className="block text-[10px] font-bold text-slate-600 mb-1">{label}</label>
            <input type="date" value={value} onChange={(e) => onChange(e.target.value)}
                className="px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                data-testid={testid} />
        </div>
    );
}

function Section({ title, children, testid }) {
    return (
        <div className="mb-5" data-testid={testid}>
            <h2 className="text-sm font-extrabold text-slate-800 mb-2 border-r-4 border-indigo-500 pr-2">
                {title}
            </h2>
            {children}
        </div>
    );
}

function PlatformCard({ platform, total }) {
    const color = PROVIDER_COLOR[platform.key] || "slate";
    const pct = total > 0 ? (platform.total / total) * 100 : 0;
    return (
        <div className={`bg-${color}-50 border border-${color}-200 rounded-lg p-3`}
            data-testid={`adv-platform-${platform.key}`}>
            <div className={`text-[10px] font-bold text-${color}-700 mb-1`}>
                {PROVIDER_LABEL[platform.key] || platform.key}
            </div>
            <div className={`num text-lg font-extrabold text-${color}-900`}>
                {fmt(platform.total)}
            </div>
            <div className={`text-[10px] text-${color}-600 mt-0.5`}>
                ر.س · {pct.toFixed(1)}% · {platform.count} قيد
            </div>
        </div>
    );
}

function Empty() {
    return (
        <div className="text-center py-6 text-slate-400 text-xs">
            لا توجد بيانات في الفترة المختارة
        </div>
    );
}

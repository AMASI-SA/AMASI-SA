// Iter-161 Phase 4 — Reconciliation Report Page
// Final gate before disabling legacy endpoints.
// Side-by-side comparison: legacy_balance vs ledger_balance per entity.

import React, { useState, useEffect } from "react";
import api from "../lib/api";
import { toast } from "sonner";

const fmt = (n) => Number(n || 0).toLocaleString(
    "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function ReconciliationReport() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const { data: d } = await api.get("/accounting/migration/reconciliation");
            setData(d);
        } catch (e) { toast.error("فشل تحميل التقرير"); }
        finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    if (loading || !data) {
        return <div className="p-8 text-center text-slate-400">جاري التحميل...</div>;
    }

    const s = data.summary;
    const safe = s.safe_to_disable_legacy;

    return (
        <div className="p-6 max-w-6xl mx-auto" data-testid="reconciliation-page">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <div className="flex items-center justify-between mb-4">
                    <div>
                        <h1 className="text-2xl font-extrabold text-slate-900">
                            🔍 تقرير المطابقة (Reconciliation)
                        </h1>
                        <p className="text-xs text-slate-500 mt-1">
                            مقارنة دقيقة بين النظام القديم (`liabilities` + `account_transactions`) والـ Ledger الجديد لكل كيان.
                        </p>
                    </div>
                    <button onClick={load}
                        className="px-3 py-1.5 bg-slate-200 hover:bg-slate-300 text-sm font-bold rounded">
                        🔄 تحديث
                    </button>
                </div>

                {/* Summary cards */}
                <div className="grid grid-cols-4 gap-3 mb-6">
                    <SumCard label="إجمالي الكيانات" value={s.total_entities} color="slate" suffix="" />
                    <SumCard label="مطابق ✓" value={s.matched} color="emerald" suffix="" />
                    <SumCard label="غير مطابق ✗" value={s.mismatched} color="rose" suffix="" />
                    <SumCard label="نسبة التطابق" value={s.match_percentage} color={safe ? "emerald" : "amber"} suffix="%" />
                </div>

                {s.total_absolute_delta > 0 && (
                    <div className="bg-amber-50 border border-amber-300 rounded-lg p-3 mb-4">
                        <div className="text-sm font-bold text-amber-900">
                            ⚠ إجمالي الفروق المالية: <span className="num">{fmt(s.total_absolute_delta)} ر.س</span>
                        </div>
                        <div className="text-xs text-amber-700 mt-1">
                            راجع الجداول أدناه لمعرفة مصدر كل فرق. لا تقم بتعطيل النظام القديم قبل التحقق.
                        </div>
                    </div>
                )}

                <div className={`rounded-lg p-4 mb-6 border-2 ${safe ? "bg-emerald-50 border-emerald-300" : "bg-rose-50 border-rose-300"}`}>
                    <div className={`text-base font-extrabold ${safe ? "text-emerald-900" : "text-rose-900"}`}>
                        {safe
                            ? "✅ آمن: نسبة التطابق 100%. يمكنك المضي قدماً لتعطيل endpoints الـ legacy."
                            : "❌ غير آمن: هناك فروقات يجب تفسيرها قبل تعطيل النظام القديم."}
                    </div>
                </div>

                {/* Sections */}
                <Section title="👥 الموظفون" rows={data.employees} columns={[
                    { key: "salary_payable", label: "راتب مستحق" },
                    { key: "advance", label: "سلفة" },
                    { key: "custody", label: "عهدة" },
                ]} />
                <Section title="🏭 الموردون" rows={data.suppliers} columns={[
                    { key: "payable", label: "المستحق" },
                ]} />
                <Section title="🤝 الأشخاص الخارجيون" rows={data.externals} columns={[
                    { key: "receivable", label: "المستحق لنا" },
                ]} />
                <Section title="📦 شركات الشحن" rows={data.couriers} columns={[
                    { key: "payable", label: "Payable" },
                    { key: "cod_receivable", label: "COD" },
                ]} />
                <Section title="🏦 الحسابات البنكية" rows={data.banks} columns={[
                    { key: "balance", label: "الرصيد" },
                ]} />
            </div>
        </div>
    );
}

function SumCard({ label, value, color, suffix }) {
    return (
        <div className={`bg-${color}-50 border border-${color}-200 rounded-lg p-3 text-center`}>
            <div className="text-[10px] text-slate-600 font-bold mb-1">{label}</div>
            <div className={`text-2xl font-extrabold text-${color}-700 num`}>{value}{suffix}</div>
        </div>
    );
}

function Section({ title, rows, columns }) {
    if (!rows || rows.length === 0) {
        return (
            <div className="mb-6">
                <h2 className="text-lg font-extrabold text-slate-800 mb-2">{title}</h2>
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs text-slate-400 text-center">
                    لا توجد سجلات
                </div>
            </div>
        );
    }
    return (
        <div className="mb-6">
            <h2 className="text-lg font-extrabold text-slate-800 mb-2">{title} ({rows.length})</h2>
            <div className="overflow-x-auto">
                <table className="w-full text-xs">
                    <thead className="bg-slate-50 border-b border-slate-200">
                        <tr className="text-slate-600">
                            <th className="text-right py-2 px-2 font-bold">الاسم</th>
                            {columns.map(col => (
                                <React.Fragment key={col.key}>
                                    <th className="text-left py-2 px-1 font-bold">{col.label} (قديم)</th>
                                    <th className="text-left py-2 px-1 font-bold">{col.label} (Ledger)</th>
                                    <th className="text-center py-2 px-1 font-bold">Δ</th>
                                </React.Fragment>
                            ))}
                            <th className="text-center py-2 px-2 font-bold">الحالة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(r => (
                            <tr key={r.id}
                                className={`border-b border-slate-100 ${r.all_match ? "" : "bg-rose-50/40"}`}>
                                <td className="py-1.5 px-2 font-bold">{r.name}</td>
                                {columns.map(col => {
                                    const f = r[col.key] || { legacy: 0, ledger: 0, delta: 0, match: true };
                                    return (
                                        <React.Fragment key={col.key}>
                                            <td className="text-left py-1.5 px-1 num">{fmt(f.legacy)}</td>
                                            <td className="text-left py-1.5 px-1 num">{fmt(f.ledger)}</td>
                                            <td className={`text-center py-1.5 px-1 num font-bold ${f.match ? "text-slate-400" : "text-rose-700"}`}>
                                                {f.match ? "—" : fmt(f.delta)}
                                            </td>
                                        </React.Fragment>
                                    );
                                })}
                                <td className="text-center py-1.5 px-2">
                                    {r.all_match
                                        ? <span className="text-emerald-700 font-extrabold">✓</span>
                                        : <span className="text-rose-700 font-extrabold">✗</span>}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

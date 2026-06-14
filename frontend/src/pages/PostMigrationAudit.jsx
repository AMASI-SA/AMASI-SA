/**
 * Post-Migration Audit — Iter-181
 *
 * Read-only sanity check after Phase 4 Closeout. Surfaces any
 * discrepancy between the legacy data and the Universal Ledger
 * BEFORE the merchant decides to disable legacy endpoints.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.error || e?.response?.data?.detail || e?.message || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
const intf = (n) => (Number(n) || 0).toLocaleString("en-US");

const VERDICT_META = {
    pass:     { label: "✅ سليم",      bg: "bg-emerald-50",  border: "border-emerald-300", txt: "text-emerald-900" },
    warnings: { label: "⚠️ تحذيرات",  bg: "bg-amber-50",   border: "border-amber-300",  txt: "text-amber-900" },
    fail:     { label: "🚨 مشاكل عالية", bg: "bg-rose-50",   border: "border-rose-300",   txt: "text-rose-900" },
};

const SEVERITY_PILL = {
    high:   "bg-rose-100 text-rose-800",
    medium: "bg-amber-100 text-amber-800",
    info:   "bg-sky-100 text-sky-800",
};

export default function PostMigrationAudit() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        let alive = true;
        (async () => {
            try {
                const res = await api.get("/audit/post-migration");
                if (alive) setData(res.data);
            } catch (e) {
                if (alive) {
                    setError(errMsg(e, "تعذّر تحميل الفحص"));
                    toast.error(errMsg(e, "تعذّر تحميل الفحص"));
                }
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => { alive = false; };
    }, []);

    if (loading) return <div className="p-8 text-center text-slate-500" data-testid="audit-loading">جاري الفحص…</div>;
    if (error || !data) return (
        <div className="p-8 max-w-3xl mx-auto" data-testid="audit-error">
            <div className="rounded-2xl border-2 border-rose-200 bg-rose-50 p-6 text-center">
                <div className="text-rose-800 font-bold">تعذّر تحميل الفحص</div>
                <div className="text-sm text-rose-700 mt-1">{error}</div>
            </div>
        </div>
    );

    const v = VERDICT_META[data.verdict] || VERDICT_META.warnings;
    const sums = data.ledger_sums_by_entity || {};
    const bank = data.bank_reconciliation || {};
    const codExc = data.cod_exclusion || {};
    const negs = data.negative_balances || {};

    return (
        <div className="p-6 max-w-7xl mx-auto" data-testid="audit-page">
            <div className="bg-white rounded-2xl shadow-lg p-6 mb-6">
                <h1 className="text-2xl font-extrabold text-slate-900 mb-1">🔬 الفحص النهائي بعد الترحيل</h1>
                <p className="text-sm text-slate-500">فحص شامل (قراءة فقط) للتأكد من سلامة الترحيل قبل تعطيل أي نظام قديم.</p>
            </div>

            <div className={`${v.bg} ${v.border} ${v.txt} border-2 rounded-2xl p-5 mb-6`} data-testid="audit-verdict">
                <div className="text-3xl font-extrabold mb-2">{v.label}</div>
                {data.issues.length === 0 ? (
                    <div className="text-sm">لا توجد أي مشكلات. كل الفحوصات اجتازت.</div>
                ) : (
                    <ul className="space-y-1.5 text-sm">
                        {data.issues.map((i, idx) => (
                            <li key={idx} className="flex items-start gap-2" data-testid={`audit-issue-${i.code}`}>
                                <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${SEVERITY_PILL[i.severity] || "bg-slate-200"}`}>
                                    {i.severity}
                                </span>
                                <span><strong>{i.code}</strong>: {i.message}</span>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                <div className="bg-white rounded-xl border-2 border-slate-200 p-4" data-testid="audit-cutoff">
                    <div className="text-xs font-bold text-slate-500 mb-1">تاريخ القطع</div>
                    <div className="text-base font-extrabold text-slate-900">
                        {data.cutoff?.cutoff_date || "— لم يُسجَّل بعد —"}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-0.5">
                        الحالة: {data.cutoff?.status || "—"} · قيود مُطبَّقة: {intf(data.cutoff?.applied_count)}
                    </div>
                </div>
                <div className={`rounded-xl border-2 p-4 ${codExc.confirmed_excluded ? "bg-emerald-50 border-emerald-300" : "bg-rose-50 border-rose-300"}`} data-testid="audit-cod">
                    <div className="text-xs font-bold text-slate-500 mb-1">استبعاد COD</div>
                    <div className={`text-base font-extrabold ${codExc.confirmed_excluded ? "text-emerald-900" : "text-rose-900"}`}>
                        {codExc.confirmed_excluded ? "✅ مُستبعد بنجاح" : "⚠️ تسرّب للترحيل"}
                    </div>
                    <div className="text-[11px] mt-0.5">قيود COD في Ledger: {intf(codExc.in_ledger)}</div>
                </div>
                <div className={`rounded-xl border-2 p-4 ${bank.match ? "bg-emerald-50 border-emerald-300" : "bg-amber-50 border-amber-300"}`} data-testid="audit-bank-recon">
                    <div className="text-xs font-bold text-slate-500 mb-1">مطابقة البنوك</div>
                    <div className={`text-base font-extrabold ${bank.match ? "text-emerald-900" : "text-amber-900"}`}>
                        {bank.match ? "✅ متطابقة" : `⚠️ فرق ${fmt(bank.diff)} ر.س`}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-0.5">
                        Legacy {fmt(bank.legacy_total)} · Ledger {fmt(bank.ledger_net)}
                    </div>
                </div>
            </div>

            <div className="bg-white rounded-2xl shadow p-5 mb-6 border border-slate-200">
                <h3 className="font-extrabold text-slate-900 mb-3">📚 مجاميع Ledger حسب نوع الكيان</h3>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm" data-testid="audit-ledger-sums">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="px-3 py-2 text-right font-bold">النوع</th>
                                <th className="px-3 py-2 text-left font-bold">مدين (Debit)</th>
                                <th className="px-3 py-2 text-left font-bold">دائن (Credit)</th>
                                <th className="px-3 py-2 text-left font-bold">صافي</th>
                            </tr>
                        </thead>
                        <tbody>
                            {Object.entries(sums).map(([et, v2]) => (
                                <tr key={et} className="border-t border-slate-100">
                                    <td className="px-3 py-2 text-right font-bold">{et}</td>
                                    <td className="px-3 py-2 num text-left">
                                        {fmt(v2.debit)} <span className="text-[10px] text-slate-500">({intf(v2.debit_count || 0)})</span>
                                    </td>
                                    <td className="px-3 py-2 num text-left">
                                        {fmt(v2.credit)} <span className="text-[10px] text-slate-500">({intf(v2.credit_count || 0)})</span>
                                    </td>
                                    <td className="px-3 py-2 num text-left font-extrabold">{fmt(v2.net)}</td>
                                </tr>
                            ))}
                            {Object.keys(sums).length === 0 && (
                                <tr><td colSpan={4} className="px-3 py-4 text-center text-slate-500">— لا توجد قيود افتتاحية بعد —</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
                <div className="bg-white rounded-2xl shadow p-5 border border-slate-200" data-testid="audit-duplicates">
                    <h3 className="font-extrabold text-slate-900 mb-2">🔁 القيود الافتتاحية المكررة</h3>
                    <div className={`text-3xl font-extrabold ${data.duplicates.count ? "text-rose-700" : "text-emerald-700"}`}>
                        {intf(data.duplicates.count)}
                    </div>
                    <div className="text-xs text-slate-500 mt-1">يجب أن يكون = 0</div>
                </div>
                <div className="bg-white rounded-2xl shadow p-5 border border-slate-200" data-testid="audit-orphans">
                    <h3 className="font-extrabold text-slate-900 mb-2">👤 قيود تشير لكيانات محذوفة</h3>
                    <div className={`text-3xl font-extrabold ${data.orphans.count ? "text-rose-700" : "text-emerald-700"}`}>
                        {intf(data.orphans.count)}
                    </div>
                    <div className="text-xs text-slate-500 mt-1">
                        صافي الأثر المحاسبي: {fmt(data.orphans.net_impact || 0)} ر.س
                    </div>
                </div>
            </div>

            {/* Iter-181b — Detailed orphan analysis */}
            {data.orphans.count > 0 && (
                <div className="bg-white rounded-2xl shadow p-5 mb-6 border-2 border-rose-200" data-testid="audit-orphan-details">
                    <h3 className="font-extrabold text-slate-900 mb-3">🔍 تحليل تفصيلي للقيود اليتيمة ({intf(data.orphans.count)})</h3>

                    <div className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-lg p-3 mb-4 leading-relaxed">
                        <strong>التفسير: </strong>{data.orphans.interpretation}
                    </div>

                    <h4 className="font-bold text-slate-800 mb-2 text-sm">حسب نوع الكيان:</h4>
                    <div className="overflow-x-auto mb-4">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50">
                                <tr>
                                    <th className="px-3 py-2 text-right">النوع</th>
                                    <th className="px-3 py-2 text-left">العدد</th>
                                    <th className="px-3 py-2 text-left">مدين</th>
                                    <th className="px-3 py-2 text-left">دائن</th>
                                    <th className="px-3 py-2 text-left">صافي</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(data.orphans.by_type || []).map((t) => (
                                    <tr key={t.entity_type} className="border-t border-slate-100">
                                        <td className="px-3 py-2 font-bold text-right">{t.entity_type}</td>
                                        <td className="px-3 py-2 num text-left">{intf(t.count)}</td>
                                        <td className="px-3 py-2 num text-left">{fmt(t.debit_total)}</td>
                                        <td className="px-3 py-2 num text-left">{fmt(t.credit_total)}</td>
                                        <td className="px-3 py-2 num text-left font-bold">{fmt(t.net)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <h4 className="font-bold text-slate-800 mb-2 text-sm">حسب التصنيف:</h4>
                    <div className="flex flex-wrap gap-2 mb-4">
                        {Object.entries(data.orphans.by_classification || {}).map(([cls, cnt]) => (
                            <span key={cls} className="inline-block px-3 py-1 rounded-full bg-amber-100 text-amber-900 text-xs font-bold">
                                {cls}: {intf(cnt)}
                            </span>
                        ))}
                    </div>

                    <details>
                        <summary className="cursor-pointer font-bold text-slate-900 text-sm select-none">
                            📋 جدول كل القيود اليتيمة ({intf(data.orphans.count)}) — اضغط للعرض
                        </summary>
                        <div className="overflow-x-auto mt-3">
                            <table className="w-full text-xs" data-testid="audit-orphan-list">
                                <thead className="bg-slate-50 text-slate-700">
                                    <tr>
                                        <th className="px-2 py-2 text-right">Ledger ID</th>
                                        <th className="px-2 py-2 text-right">النوع</th>
                                        <th className="px-2 py-2 text-right">Entity ID</th>
                                        <th className="px-2 py-2 text-right">الاسم</th>
                                        <th className="px-2 py-2 text-right">Sub Account</th>
                                        <th className="px-2 py-2 text-left">مدين</th>
                                        <th className="px-2 py-2 text-left">دائن</th>
                                        <th className="px-2 py-2 text-right">التصنيف</th>
                                        <th className="px-2 py-2 text-right">التاريخ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {(data.orphans.all || []).map((o, idx) => (
                                        <tr key={o.ledger_id || idx} className="border-t border-slate-100">
                                            <td className="px-2 py-2 font-mono text-[10px] text-right">{o.ledger_id?.slice(0, 12) || "—"}</td>
                                            <td className="px-2 py-2 text-right">{o.entity_type}</td>
                                            <td className="px-2 py-2 font-mono text-[10px] text-right">{(o.entity_id || "—").toString().slice(0, 16)}</td>
                                            <td className="px-2 py-2 text-right font-bold">{o.entity_name || "—"}</td>
                                            <td className="px-2 py-2 text-right">{o.sub_account || "—"}</td>
                                            <td className="px-2 py-2 num text-left">{fmt(o.debit)}</td>
                                            <td className="px-2 py-2 num text-left">{fmt(o.credit)}</td>
                                            <td className="px-2 py-2 text-right">
                                                <span className="inline-block px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 text-[10px] font-bold">
                                                    {o.classification}
                                                </span>
                                            </td>
                                            <td className="px-2 py-2 text-right text-[10px]">
                                                {o.created_at ? new Date(o.created_at).toLocaleString("en-GB", { timeZone: "Asia/Riyadh", hour12: false }) : "—"}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </details>
                </div>
            )}

            {negs.count > 0 && (
                <details className="bg-white rounded-2xl shadow p-5 mb-6 border border-slate-200" data-testid="audit-negative-balances">
                    <summary className="cursor-pointer font-extrabold text-slate-900 select-none">
                        💰 حسابات بأرصدة سالبة ({intf(negs.count)})
                        {negs.unexplained_count > 0 && (
                            <span className="ms-2 inline-block px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[10px] font-bold">
                                {intf(negs.unexplained_count)} غير مبرَّر
                            </span>
                        )}
                    </summary>
                    <table className="w-full text-sm mt-3">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="px-3 py-2 text-right font-bold">الحساب</th>
                                <th className="px-3 py-2 text-right font-bold">النوع</th>
                                <th className="px-3 py-2 text-left font-bold">الرصيد</th>
                                <th className="px-3 py-2 text-center font-bold">سالب متوقع؟</th>
                            </tr>
                        </thead>
                        <tbody>
                            {negs.accounts.map((a) => (
                                <tr key={a.id} className="border-t border-slate-100">
                                    <td className="px-3 py-2 text-right font-bold">{a.name}</td>
                                    <td className="px-3 py-2 text-right">{a.type}</td>
                                    <td className="px-3 py-2 num text-left font-bold text-rose-700">{fmt(a.balance)}</td>
                                    <td className="px-3 py-2 text-center">
                                        {a.expected_negative ? "✅" : "⚠️"}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </details>
            )}
        </div>
    );
}

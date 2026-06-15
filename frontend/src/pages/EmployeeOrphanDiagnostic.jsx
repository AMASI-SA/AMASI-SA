/**
 * Iter-222 — صفحة تشخيص قيود الموظفين اليتيمة (Read-Only).
 *
 * تقرير قراءة-فقط للقيود اليتيمة على entity_type=employee. لا تنفّذ
 * أي إصلاح من هذه الصفحة — مجرد عرض السبب الحقيقي لكل قيد قبل
 * اتخاذ قرار المعالجة (في تكرار لاحق ستُضاف أزرار Preview Fix و
 * Apply Fix).
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
    e?.response?.data?.detail
    || e?.response?.data?.error
    || e?.message
    || fb;

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const CLASS_LABELS = {
    deleted_entity:        { ar: "موظف محذوف",          color: "rose"   },
    employee_id_mismatch:  { ar: "خطأ ربط (مستخدم آخر)", color: "amber" },
    missing_counter_entry: { ar: "قيد غير متوازن",       color: "amber" },
    orphan_opening:        { ar: "افتتاحي بلا موظف",     color: "rose"  },
    orphan_reversal:       { ar: "عكس بدون أصل",         color: "amber" },
    other:                 { ar: "غير مصنّف",            color: "slate" },
};

const TONE = {
    rose:    "bg-rose-50 text-rose-700 border-rose-200",
    amber:   "bg-amber-50 text-amber-700 border-amber-200",
    emerald: "bg-emerald-50 text-emerald-700 border-emerald-200",
    slate:   "bg-slate-50 text-slate-700 border-slate-200",
    violet:  "bg-violet-50 text-violet-700 border-violet-200",
};


function ClassPill({ k }) {
    const meta = CLASS_LABELS[k] || CLASS_LABELS.other;
    return (
        <span
            className={`inline-flex items-center px-2 py-0.5 rounded-md
                        text-[10px] font-bold border ${TONE[meta.color]}`}
            data-testid={`class-pill-${k}`}
        >
            {meta.ar}
        </span>
    );
}


function StatTile({ label, value, tone = "slate", testid }) {
    return (
        <div
            className={`rounded-xl border p-4 ${TONE[tone]}`}
            data-testid={testid}
        >
            <div className="text-[10px] font-bold opacity-80 mb-1">
                {label}
            </div>
            <div className="num text-xl font-extrabold">{value}</div>
        </div>
    );
}


export default function EmployeeOrphanDiagnostic() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState("");
    const [classFilter, setClassFilter] = useState("");

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get(
                "/audit/employee-orphan-openings",
            );
            setData(data);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل التقرير"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const summary = data?.summary || {};
    const perEmp = data?.per_employee || [];
    const entries = data?.entries || [];

    const filtered = useMemo(() => {
        return entries.filter((e) => {
            if (classFilter && e.classification !== classFilter) return false;
            if (filter) {
                const f = filter.toLowerCase();
                return (
                    (e.entity_id || "").toLowerCase().includes(f)
                    || (e.metadata_name || "").toLowerCase().includes(f)
                    || (e.txn_group_id || "").toLowerCase().includes(f)
                    || (e.ledger_id || "").toLowerCase().includes(f)
                );
            }
            return true;
        });
    }, [entries, filter, classFilter]);

    return (
        <div className="space-y-6" dir="rtl"
             data-testid="employee-orphan-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold
                                   text-slate-900">
                        تشخيص قيود الموظفين اليتيمة
                    </h1>
                    <p className="text-xs text-slate-500 mt-1">
                        تقرير قراءة فقط — لا يُجري أي تعديل على الدفتر.
                        الهدف معرفة السبب الحقيقي لكل قيد قبل اتخاذ
                        قرار المعالجة.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={load}
                    disabled={loading}
                    className="px-3 py-2 rounded-lg border border-slate-300
                               text-xs font-bold text-slate-700
                               hover:bg-slate-50 disabled:opacity-60"
                    data-testid="refresh-btn"
                >
                    {loading ? "جارٍ التحديث…" : "تحديث"}
                </button>
            </div>

            {/* Phase-2 placeholder buttons — disabled until we
                decide the resolution per classification. */}
            <div
                className="rounded-xl border border-dashed border-slate-300
                           bg-slate-50 p-4 flex items-center justify-between
                           gap-3 flex-wrap"
                data-testid="phase2-placeholder"
            >
                <div className="text-xs text-slate-600">
                    <span className="font-bold">المرحلة الثانية:</span>{" "}
                    معاينة الإصلاح / تطبيق الإصلاح — معطّلة الآن. بعد
                    مراجعة هذا التقرير سنحدّد طريقة المعالجة المناسبة
                    لكل فئة بشكل منفصل، ثم نفعّل الأزرار.
                </div>
                <div className="flex gap-2">
                    <button
                        type="button"
                        disabled
                        title="سيُفعّل في تكرار لاحق"
                        className="px-3 py-1.5 rounded-lg bg-slate-200
                                   text-slate-400 text-xs font-bold
                                   cursor-not-allowed"
                        data-testid="btn-preview-fix-disabled"
                    >
                        معاينة الإصلاح (غير مفعّل)
                    </button>
                    <button
                        type="button"
                        disabled
                        title="سيُفعّل في تكرار لاحق"
                        className="px-3 py-1.5 rounded-lg bg-slate-200
                                   text-slate-400 text-xs font-bold
                                   cursor-not-allowed"
                        data-testid="btn-apply-fix-disabled"
                    >
                        تطبيق الإصلاح (غير مفعّل)
                    </button>
                </div>
            </div>

            {/* Summary tiles */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <StatTile
                    label="إجمالي القيود اليتيمة"
                    value={summary.total_orphans ?? "—"}
                    tone={summary.total_orphans ? "rose" : "emerald"}
                    testid="stat-total-orphans"
                />
                <StatTile
                    label="إجمالي المدين"
                    value={`${fmt(summary.total_debit)} ر.س`}
                    tone="amber"
                    testid="stat-total-debit"
                />
                <StatTile
                    label="إجمالي الدائن"
                    value={`${fmt(summary.total_credit)} ر.س`}
                    tone="violet"
                    testid="stat-total-credit"
                />
                <StatTile
                    label="صافي الأثر"
                    value={`${(summary.net_impact ?? 0) >= 0 ? "+" : "−"}${fmt(Math.abs(summary.net_impact))} ر.س`}
                    tone={Math.abs(summary.net_impact ?? 0) > 0.01 ? "rose" : "emerald"}
                    testid="stat-net-impact"
                />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <StatTile
                    label="أثر صافي على salary_payable"
                    value={`${fmt(summary.salary_payable_impact)} ر.س`}
                    tone="slate"
                    testid="stat-salary-impact"
                />
                <StatTile
                    label="أثر على السُّلف (advance)"
                    value={`${fmt(summary.advance_impact)} ر.س`}
                    tone="slate"
                    testid="stat-advance-impact"
                />
                <StatTile
                    label="أثر على العُهد (custody)"
                    value={`${fmt(summary.custody_impact)} ر.س`}
                    tone="slate"
                    testid="stat-custody-impact"
                />
            </div>

            {/* Classification breakdown */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <h2 className="text-base font-extrabold text-slate-900 mb-3">
                    التصنيف
                </h2>
                {(summary.by_classification || []).length === 0 ? (
                    <div className="text-center text-emerald-600 py-4
                                    text-sm font-bold">
                        ✅ لا توجد قيود يتيمة على الموظفين.
                    </div>
                ) : (
                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            onClick={() => setClassFilter("")}
                            className={`px-3 py-2 rounded-lg border text-xs
                                       font-bold ${classFilter === ""
                                ? "bg-slate-900 text-white border-slate-900"
                                : "bg-white text-slate-700 border-slate-200"}`}
                            data-testid="class-filter-all"
                        >
                            الكل ({entries.length})
                        </button>
                        {summary.by_classification.map((c) => {
                            const meta = CLASS_LABELS[c.classification]
                                || CLASS_LABELS.other;
                            const active = classFilter === c.classification;
                            return (
                                <button
                                    type="button"
                                    key={c.classification}
                                    onClick={() => setClassFilter(c.classification)}
                                    className={`px-3 py-2 rounded-lg border
                                               text-xs font-bold ${active
                                        ? "bg-slate-900 text-white border-slate-900"
                                        : TONE[meta.color]}`}
                                    data-testid={`class-filter-${c.classification}`}
                                >
                                    {meta.ar}: {c.count}
                                    <span className="opacity-70 text-[10px]
                                                     mr-2 num font-mono">
                                        صافي {fmt(c.net)}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* Per-employee breakdown */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <h2 className="text-base font-extrabold text-slate-900 mb-3">
                    حسب الموظف ({perEmp.length})
                </h2>
                {perEmp.length === 0 ? (
                    <div className="text-center text-slate-400 py-4 text-sm">
                        لا يوجد موظفين متأثرين.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="text-slate-500 font-bold
                                               border-b border-slate-200">
                                    <th className="text-right py-2 px-2">
                                        الاسم / المعرّف</th>
                                    <th className="text-right py-2 px-2">
                                        التصنيفات</th>
                                    <th className="text-center py-2 px-2">
                                        # القيود</th>
                                    <th className="text-left py-2 px-2 num">
                                        salary_payable الحالي
                                    </th>
                                    <th className="text-left py-2 px-2 num">
                                        salary_payable المتوقع بعد الإصلاح
                                    </th>
                                    <th className="text-left py-2 px-2 num">
                                        الفرق
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {perEmp.map((e) => {
                                    const cur = e.current_balance?.salary_payable || 0;
                                    const exp = e.expected_after_fix?.salary_payable || 0;
                                    const diff = e.difference?.salary_payable || 0;
                                    return (
                                        <tr
                                            key={e.entity_id}
                                            className="border-b border-slate-100
                                                       hover:bg-slate-50"
                                            data-testid={`emp-row-${e.entity_id}`}
                                        >
                                            <td className="py-2 px-2">
                                                <div className="font-bold
                                                                text-slate-900">
                                                    {e.name || "—"}
                                                </div>
                                                <div className="text-[10px]
                                                                font-mono
                                                                text-slate-400">
                                                    {e.entity_id}
                                                </div>
                                            </td>
                                            <td className="py-2 px-2">
                                                <div className="flex flex-wrap gap-1">
                                                    {e.classifications.map(c => (
                                                        <ClassPill key={c} k={c} />
                                                    ))}
                                                </div>
                                            </td>
                                            <td className="py-2 px-2 text-center
                                                           font-bold num">
                                                {e.affected_count}
                                            </td>
                                            <td className="py-2 px-2 num
                                                           text-left text-slate-700">
                                                {fmt(cur)}
                                            </td>
                                            <td className="py-2 px-2 num
                                                           text-left text-slate-700">
                                                {fmt(exp)}
                                            </td>
                                            <td className={`py-2 px-2 num text-left
                                                            font-bold ${Math.abs(diff) > 0.01
                                                ? "text-rose-700"
                                                : "text-emerald-700"}`}>
                                                {diff >= 0 ? "+" : "−"}{fmt(Math.abs(diff))}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Raw entries with filtering */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <div className="flex items-center justify-between mb-3
                                flex-wrap gap-2">
                    <h2 className="text-base font-extrabold text-slate-900">
                        القيود اليتيمة — تفاصيل ({filtered.length} /{" "}
                        {entries.length})
                    </h2>
                    <input
                        type="text"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder="بحث (entity_id / اسم / txn_group / ledger_id)…"
                        className="w-64 px-3 py-2 rounded-lg border
                                   border-slate-300 text-xs"
                        data-testid="entries-search"
                    />
                </div>
                {filtered.length === 0 ? (
                    <div className="text-center text-slate-400 py-6 text-sm">
                        لا توجد قيود تطابق الفلتر.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="text-slate-500 font-bold
                                               border-b border-slate-200">
                                    <th className="text-right py-2 px-2">
                                        التاريخ</th>
                                    <th className="text-right py-2 px-2">
                                        النوع</th>
                                    <th className="text-right py-2 px-2">
                                        الفرعي</th>
                                    <th className="text-right py-2 px-2">
                                        الاسم</th>
                                    <th className="text-right py-2 px-2">
                                        entity_id
                                    </th>
                                    <th className="text-left py-2 px-2 num">
                                        مدين</th>
                                    <th className="text-left py-2 px-2 num">
                                        دائن</th>
                                    <th className="text-right py-2 px-2">
                                        التصنيف</th>
                                    <th className="text-right py-2 px-2">
                                        السبب</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((e, i) => (
                                    <tr
                                        key={e.ledger_id || i}
                                        className="border-b border-slate-100
                                                   hover:bg-slate-50"
                                        data-testid={`entry-row-${i}`}
                                    >
                                        <td className="py-2 px-2 num
                                                       text-slate-500
                                                       whitespace-nowrap">
                                            {(e.posted_at || "").slice(0, 10)}
                                        </td>
                                        <td className="py-2 px-2 text-slate-700
                                                       font-mono">
                                            {e.entry_type}
                                        </td>
                                        <td className="py-2 px-2 text-slate-600">
                                            {e.sub_account}
                                        </td>
                                        <td className="py-2 px-2 text-slate-900
                                                       font-semibold">
                                            {e.metadata_name || "—"}
                                        </td>
                                        <td className="py-2 px-2 font-mono
                                                       text-[10px] text-slate-400">
                                            {e.entity_id}
                                        </td>
                                        <td className="py-2 px-2 num text-left
                                                       text-emerald-700">
                                            {e.debit ? fmt(e.debit) : ""}
                                        </td>
                                        <td className="py-2 px-2 num text-left
                                                       text-rose-700">
                                            {e.credit ? fmt(e.credit) : ""}
                                        </td>
                                        <td className="py-2 px-2">
                                            <ClassPill k={e.classification} />
                                        </td>
                                        <td className="py-2 px-2 text-slate-600
                                                       text-[11px] max-w-md">
                                            {e.reason}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

// Iter-164 — Reconciliation Report Page (clarity edition)
//
// Before this iteration, users were confused by the report:
// "Why does Ledger show 0 for everyone? Why is match=21%?"
// The answer was: the migration was NEVER executed; Dry Run only PLANS
// opening balances, it doesn't post them. The report previously
// compared legacy vs current-empty-ledger which always looked terrible
// pre-migration.
//
// This redesign makes the migration state explicit and presents:
//   • Migration status banner (executed / not executed)
//   • Three columns: قديم / Ledger الحالي / المتوقّع بعد الترحيل
//   • Per-employee dynamic-accrual breakdown
//   • Orphan supplier liabilities surfaced separately
//   • An «تنفيذ الترحيل النهائي» button right inside the report

import React, { useState, useEffect } from "react";
import api from "../lib/api";
import { toast } from "sonner";

const fmt = (n) => Number(n || 0).toLocaleString(
    "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const todayIso = () => new Date().toISOString().slice(0, 10);

export default function ReconciliationReport() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [migrating, setMigrating] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const { data: d } = await api.get("/accounting/migration/reconciliation");
            setData(d);
        } catch { toast.error("فشل تحميل التقرير"); }
        finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    const runMigration = async () => {
        if (!data) return;
        const cutoff = todayIso();
        const ok = window.confirm(
            "تأكيد تنفيذ الترحيل النهائي:\n\n" +
            `• تاريخ القطع: ${cutoff}\n` +
            `• إجمالي ما سيتم ترحيله: ${fmt(data.summary.will_post_after_migration)} ر.س\n` +
            `• عدد الكيانات: ${data.summary.total_entities}\n\n` +
            "بعد التنفيذ، ستظهر هذه الأرصدة كقيود افتتاحية في دفتر الأستاذ الموحّد، " +
            "وسيُسجَّل تاريخ القطع. لا يمكن التراجع بنفسك.\n\nالمتابعة؟"
        );
        if (!ok) return;
        setMigrating(true);
        try {
            const { data: r } = await api.post(
                "/accounting/migration/run",
                { cutoff_date: cutoff, dry_run: false });
            toast.success(
                `تم الترحيل بنجاح · ${r.applied_count} قيد افتتاحي`,
                { duration: 8000 });
            await load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الترحيل");
        } finally { setMigrating(false); }
    };

    if (loading || !data) {
        return <div className="p-8 text-center text-slate-400">جاري التحميل...</div>;
    }

    const s = data.summary;
    const ms = data.migration_status || { completed: false };
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
                            مقارنة دقيقة بين النظام القديم ودفتر الأستاذ الموحّد، مع توضيح ما سيحدث بعد تنفيذ الترحيل.
                        </p>
                    </div>
                    <button onClick={load}
                        className="px-3 py-1.5 bg-slate-200 hover:bg-slate-300 text-sm font-bold rounded"
                        data-testid="recon-refresh-btn">
                        🔄 تحديث
                    </button>
                </div>

                {/* Iter-164 — Migration state banner */}
                <MigrationStateBanner
                    ms={ms} s={s} migrating={migrating}
                    onRun={runMigration} />

                {/* Summary cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
                    <SumCard label="إجمالي الكيانات" value={s.total_entities} color="slate" suffix="" />
                    <SumCard
                        label={ms.completed ? "مطابق ✓" : "مطابق حالياً ✓"}
                        value={s.matched} color="emerald" suffix="" />
                    <SumCard
                        label={ms.completed ? "غير مطابق ✗" : "بانتظار الترحيل"}
                        value={s.mismatched}
                        color={ms.completed ? "rose" : "amber"} suffix="" />
                    <SumCard
                        label={ms.completed ? "نسبة التطابق" : "نسبة التطابق المتوقّعة"}
                        value={ms.completed ? s.match_percentage : s.projected_match_percentage}
                        color={
                            (ms.completed ? s.match_percentage : s.projected_match_percentage) >= 100
                                ? "emerald" : "amber"}
                        suffix="%" />
                </div>

                {/* Will-post-after-migration card (pre-migration only) */}
                {!ms.completed && (
                    <div className="bg-indigo-50 border border-indigo-300 rounded-lg p-3 mb-4">
                        <div className="text-sm font-bold text-indigo-900">
                            💰 سيتم ترحيله كقيود افتتاحية: <span className="num">{fmt(s.will_post_after_migration)} ر.س</span>
                        </div>
                        <div className="text-xs text-indigo-700 mt-1">
                            هذا هو إجمالي الأرصدة من النظام القديم (الرواتب المستحقة، السلف، الموردين، البنوك...) التي ستُسجَّل تلقائياً في دفتر الأستاذ عند تنفيذ الترحيل.
                        </div>
                    </div>
                )}

                {/* Orphan supplier alert */}
                {s.orphan_supplier_count > 0 && (
                    <div className="bg-amber-50 border border-amber-300 rounded-lg p-3 mb-4">
                        <div className="text-sm font-bold text-amber-900">
                            ⚠ مديونيات موردين بدون ربط: {s.orphan_supplier_count} ({fmt(s.orphan_supplier_total)} ر.س)
                        </div>
                        <div className="text-xs text-amber-800 mt-1">
                            هذه الصفوف موجودة في النظام القديم لكنها غير مرتبطة بأي مورد مُسجَّل. لن تُرحَّل تلقائياً.
                            إما أنشئ موردًا بنفس الاسم أولاً، أو سدّد/احذف الفاتورة يدوياً، أو تجاهلها إذا كانت قديمة.
                        </div>
                        <details className="mt-2">
                            <summary className="cursor-pointer text-xs font-bold text-amber-900">عرض القائمة</summary>
                            <ul className="text-[11px] mt-2 space-y-1 max-h-40 overflow-y-auto">
                                {data.orphan_suppliers.map((x) => (
                                    <li key={x.id}
                                        className="flex justify-between bg-white p-1.5 rounded border border-amber-100">
                                        <span>{x.supplier_name || "(بدون اسم)"} — {x.description || "—"}</span>
                                        <span className="num font-bold text-amber-900">{fmt(x.remaining)} ر.س</span>
                                    </li>
                                ))}
                            </ul>
                        </details>
                    </div>
                )}

                {s.total_absolute_delta > 0 && ms.completed && (
                    <div className="bg-rose-50 border border-rose-300 rounded-lg p-3 mb-4">
                        <div className="text-sm font-bold text-rose-900">
                            ❗ إجمالي الفروق المالية بعد الترحيل: <span className="num">{fmt(s.total_absolute_delta)} ر.س</span>
                        </div>
                        <div className="text-xs text-rose-700 mt-1">
                            هذه الفروق ظهرت بعد تنفيذ الترحيل. راجع الجداول أدناه — قد تكون نتيجة قيود يدوية أُضيفت بعد القطع.
                        </div>
                    </div>
                )}

                <div className={`rounded-lg p-4 mb-6 border-2 ${safe
                    ? "bg-emerald-50 border-emerald-300"
                    : ms.completed ? "bg-rose-50 border-rose-300" : "bg-amber-50 border-amber-300"}`}>
                    <div className={`text-base font-extrabold ${safe
                        ? "text-emerald-900"
                        : ms.completed ? "text-rose-900" : "text-amber-900"}`}>
                        {safe
                            ? "✅ آمن: نسبة التطابق 100% وتم تنفيذ الترحيل. يمكنك المضي قدماً لإغلاق المرحلة القديمة."
                            : ms.completed
                                ? "❌ غير آمن: الترحيل نُفّذ لكن توجد فروقات يجب تفسيرها."
                                : "⏳ بانتظار الترحيل: نسبة التطابق ستصبح 100% فور تنفيذ الترحيل (إذا كانت بيانات النظام القديم سليمة في عمود 'قديم')."}
                    </div>
                </div>

                {/* Sections */}
                <Section title="👥 الموظفون" rows={data.employees} columns={[
                    { key: "salary_payable", label: "راتب مستحق" },
                    { key: "advance", label: "سلفة" },
                    { key: "custody", label: "عهدة" },
                ]} showBreakdown={true} migrationCompleted={ms.completed} />
                <Section title="🏭 الموردون" rows={data.suppliers} columns={[
                    { key: "payable", label: "المستحق" },
                ]} migrationCompleted={ms.completed} />
                <Section title="🤝 الأشخاص الخارجيون" rows={data.externals} columns={[
                    { key: "receivable", label: "المستحق لنا" },
                ]} migrationCompleted={ms.completed} />
                <Section title="📦 شركات الشحن" rows={data.couriers} columns={[
                    { key: "payable", label: "Payable" },
                    { key: "cod_receivable", label: "COD" },
                ]} migrationCompleted={ms.completed} />
                <Section title="🏦 الحسابات البنكية" rows={data.banks} columns={[
                    { key: "balance", label: "الرصيد" },
                ]} migrationCompleted={ms.completed} />
            </div>
        </div>
    );
}

function MigrationStateBanner({ ms, s, migrating, onRun }) {
    if (ms.completed) {
        return (
            <div className="bg-emerald-50 border-2 border-emerald-300 rounded-lg p-4 mb-4"
                data-testid="migration-completed-banner">
                <div className="flex items-center justify-between">
                    <div>
                        <div className="text-sm font-extrabold text-emerald-900 mb-1">
                            ✅ تم تنفيذ الترحيل
                        </div>
                        <div className="text-xs text-emerald-700">
                            تاريخ القطع: <span className="num">{ms.cutoff_date}</span> ·
                            تاريخ التنفيذ: <span className="num">{(ms.applied_at || "").slice(0, 19).replace("T", " ")}</span> ·
                            عدد القيود الافتتاحية: <span className="num">{ms.applied_count}</span>
                        </div>
                    </div>
                </div>
            </div>
        );
    }
    return (
        <div className="bg-amber-50 border-2 border-amber-300 rounded-lg p-4 mb-4"
            data-testid="migration-pending-banner">
            <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                    <div className="text-sm font-extrabold text-amber-900 mb-1">
                        ⏳ الترحيل لم يُنفَّذ بعد
                    </div>
                    <div className="text-xs text-amber-800 leading-relaxed">
                        <p className="mb-1">
                            <strong>لهذا السبب يظهر عمود «Ledger الحالي» بقيمة 0</strong> لمعظم الكيانات.
                            هذه ليست مشكلة في المنطق — ببساطة الأرصدة الافتتاحية لم تُسجَّل بعد في دفتر الأستاذ.
                        </p>
                        <p className="mb-1">
                            انظر عمود <strong>«المتوقَّع بعد الترحيل»</strong> — يُظهر القيم التي ستظهر في دفتر
                            الأستاذ <strong>فور تنفيذ الترحيل</strong>. إذا كانت تطابق العمود القديم،
                            فالمنطق سليم 100%.
                        </p>
                        <p>
                            عندما تكون أرقام عمود «قديم» صحيحة في نظرك، اضغط الزر التالي لتنفيذ الترحيل النهائي.
                        </p>
                    </div>
                </div>
                <button
                    onClick={onRun}
                    disabled={migrating || s.total_entities === 0}
                    className="px-4 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-sm font-extrabold whitespace-nowrap"
                    data-testid="run-migration-btn"
                    title="ينفّذ الترحيل النهائي ويسجّل جميع الأرصدة الافتتاحية في دفتر الأستاذ">
                    {migrating ? "جاري التنفيذ..." : "▶ تنفيذ الترحيل النهائي"}
                </button>
            </div>
        </div>
    );
}

function SumCard({ label, value, color, suffix }) {
    const palette = {
        slate:   "bg-slate-50 border-slate-200 text-slate-700",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-700",
        rose:    "bg-rose-50 border-rose-200 text-rose-700",
        amber:   "bg-amber-50 border-amber-200 text-amber-700",
        indigo:  "bg-indigo-50 border-indigo-200 text-indigo-700",
    }[color] || "bg-slate-50 border-slate-200 text-slate-700";
    return (
        <div className={`${palette} border rounded-lg p-3 text-center`}>
            <div className="text-[10px] text-slate-600 font-bold mb-1">{label}</div>
            <div className="text-2xl font-extrabold num">{value}{suffix}</div>
        </div>
    );
}

function Section({ title, rows, columns, showBreakdown, migrationCompleted }) {
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
                            {columns.map((col) => (
                                <React.Fragment key={col.key}>
                                    <th className="text-left py-2 px-1 font-bold">{col.label} (قديم)</th>
                                    <th className="text-left py-2 px-1 font-bold">{col.label} (Ledger الحالي)</th>
                                    {!migrationCompleted && (
                                        <th className="text-left py-2 px-1 font-bold text-indigo-700">{col.label} (المتوقَّع)</th>
                                    )}
                                    <th className="text-center py-2 px-1 font-bold">Δ</th>
                                </React.Fragment>
                            ))}
                            <th className="text-center py-2 px-2 font-bold">الحالة</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <React.Fragment key={r.id}>
                                <tr className={`border-b border-slate-100 ${r.all_match ? "" : "bg-amber-50/30"}`}>
                                    <td className="py-1.5 px-2 font-bold">{r.name}</td>
                                    {columns.map((col) => {
                                        const f = r[col.key] || {};
                                        const legacy = Number(f.legacy || 0);
                                        const ledger = Number(f.ledger || 0);
                                        const projected = Number(f.projected || 0);
                                        return (
                                            <React.Fragment key={col.key}>
                                                <td className="text-left py-1.5 px-1 num">{fmt(legacy)}</td>
                                                <td className="text-left py-1.5 px-1 num">{fmt(ledger)}</td>
                                                {!migrationCompleted && (
                                                    <td className="text-left py-1.5 px-1 num text-indigo-700 font-bold">{fmt(projected)}</td>
                                                )}
                                                <td className={`text-center py-1.5 px-1 num font-bold ${f.match ? "text-slate-400" : "text-amber-700"}`}>
                                                    {f.match ? "—" : fmt(f.delta)}
                                                </td>
                                            </React.Fragment>
                                        );
                                    })}
                                    <td className="text-center py-1.5 px-2">
                                        {migrationCompleted
                                            ? (r.all_match
                                                ? <span className="text-emerald-700 font-extrabold">✓</span>
                                                : <span className="text-rose-700 font-extrabold">✗</span>)
                                            : <span className="text-amber-700 font-extrabold" title="بانتظار تنفيذ الترحيل">⏳</span>}
                                    </td>
                                </tr>
                                {showBreakdown && r.breakdown && (
                                    <tr className="bg-slate-50/60 border-b border-slate-100"
                                        data-testid={`emp-breakdown-${r.id}`}>
                                        <td colSpan={columns.length * (migrationCompleted ? 3 : 4) + 2}
                                            className="py-1.5 px-2 text-[11px] text-slate-600">
                                            <span className="font-bold text-slate-700">تفصيل الراتب المستحق:</span>
                                            <span className="mx-2">راتب شهري: <span className="num font-bold">{fmt(r.breakdown.monthly_amount)}</span></span>
                                            ·
                                            <span className="mx-2">من <span className="num">{r.breakdown.accrual_start || "—"}</span> إلى <span className="num">{r.breakdown.accrual_end || "—"}</span></span>
                                            ·
                                            <span className="mx-2">أيام عمل: <span className="num font-bold">{r.breakdown.days_worked}</span></span>
                                            ·
                                            <span className="mx-2">مستحق: <span className="num font-bold">{fmt(r.breakdown.accrued)}</span></span>
                                            ·
                                            <span className="mx-2">نقد مدفوع: <span className="num font-bold">{fmt(r.breakdown.cash_paid)}</span></span>
                                            ·
                                            <span className="mx-2 text-slate-800">صافي = <span className="num font-extrabold">{fmt(Math.max(0, (r.breakdown.accrued || 0) - (r.breakdown.cash_paid || 0)))}</span></span>
                                        </td>
                                    </tr>
                                )}
                            </React.Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

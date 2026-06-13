// Iter-161 Phase 2 — Migration Wizard
//
// Dry-run + apply the legacy → universal ledger migration.
// Shows BEFORE/AFTER balance comparison for employees, suppliers,
// external persons and bank accounts.

import React, { useState, useEffect } from "react";
import api from "../lib/api";
import { toast } from "sonner";

export default function MigrationWizard() {
    const [status, setStatus] = useState(null);
    const [cutoffDate, setCutoffDate] = useState(
        () => new Date().toISOString().slice(0, 10));
    const [dryRun, setDryRun] = useState(null);
    const [busy, setBusy] = useState(false);
    const [confirmText, setConfirmText] = useState("");

    useEffect(() => { (async () => {
        try {
            const { data } = await api.get("/accounting/migration/status");
            setStatus(data);
        } catch (e) { /* ignore */ }
    })(); }, []);

    const runDryRun = async () => {
        setBusy(true);
        try {
            const { data } = await api.post(
                "/accounting/migration/run",
                { cutoff_date: cutoffDate, dry_run: true });
            setDryRun(data);
            toast.success("تم تشغيل المعاينة بنجاح");
        } catch (e) {
            toast.error(e.response?.data?.detail || "فشل تشغيل المعاينة");
        } finally { setBusy(false); }
    };

    const applyMigration = async () => {
        if (confirmText !== "أوافق على الترحيل") {
            toast.error("أكتب «أوافق على الترحيل» للتأكيد");
            return;
        }
        if (!window.confirm(
            "⚠️ سيتم تطبيق الترحيل الفعلي.\n" +
            "الأرصدة الحالية ستصبح Opening Balance في Ledger الجديد.\n" +
            "البيانات القديمة لن تُحذف.\n" +
            "العملية لا يمكن تكرارها.\n\nهل أنت متأكد؟"
        )) return;
        setBusy(true);
        try {
            const { data } = await api.post(
                "/accounting/migration/run",
                { cutoff_date: cutoffDate, dry_run: false });
            setDryRun(data);
            toast.success(`تم الترحيل بنجاح. عدد القيود: ${data.applied_count}`);
            // refresh status
            const { data: st } = await api.get("/accounting/migration/status");
            setStatus(st);
        } catch (e) {
            toast.error(e.response?.data?.detail || "فشل الترحيل");
        } finally { setBusy(false); }
    };

    const fmt = (n) => Number(n || 0).toLocaleString(
        "ar-SA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const renderDiffTable = (title, rows, fieldLabel) => {
        if (!rows?.length) return null;
        return (
            <div className="bg-white border border-slate-200 rounded-lg p-4 mb-4">
                <h3 className="text-sm font-extrabold text-slate-800 mb-2">{title} ({rows.length})</h3>
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="text-slate-500 border-b border-slate-200">
                                <th className="text-right py-1">الاسم</th>
                                {Object.keys(rows[0]?.fields || {}).map(f => (
                                    <React.Fragment key={f}>
                                        <th className="text-left py-1">{fieldLabel(f)} (قبل)</th>
                                        <th className="text-left py-1">{fieldLabel(f)} (بعد)</th>
                                        <th className="text-center py-1">الفرق</th>
                                    </React.Fragment>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => (
                                <tr key={i} className="border-b border-slate-100">
                                    <td className="py-1 font-bold">{r.name}</td>
                                    {Object.entries(r.fields).map(([fkey, fval]) => (
                                        <React.Fragment key={fkey}>
                                            <td className="text-left py-1 num">{fmt(fval.before)}</td>
                                            <td className="text-left py-1 num">{fmt(fval.after)}</td>
                                            <td className={`text-center py-1 num font-bold ${fval.match ? "text-emerald-700" : "text-rose-700"}`}>
                                                {fval.match ? "✓" : `Δ ${fmt(fval.delta)}`}
                                            </td>
                                        </React.Fragment>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        );
    };

    return (
        <div className="p-6 max-w-5xl mx-auto" data-testid="migration-wizard">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <h1 className="text-2xl font-extrabold text-slate-900 mb-1">
                    🚀 ترحيل الأرصدة إلى النظام المحاسبي الموحد
                </h1>
                <p className="text-sm text-slate-500 mb-4">
                    يقوم النظام بنقل الأرصدة الحالية إلى الـ Ledger الجديد كرصيد افتتاحي.
                    البيانات القديمة محفوظة للقراءة فقط.
                </p>

                {status?.completed ? (
                    <div className="bg-emerald-50 border-2 border-emerald-200 rounded-lg p-4 mb-4">
                        <div className="text-sm font-bold text-emerald-900">
                            ✅ تم الترحيل بتاريخ {status.cutoff?.cutoff_date}
                        </div>
                        <div className="text-xs text-emerald-700 mt-1">
                            عدد القيود الافتتاحية: {status.cutoff?.applied_count} ·
                            وقت التطبيق: {status.cutoff?.applied_at?.slice(0, 19).replace("T", " ")}
                        </div>
                        <button onClick={async () => {
                            try {
                                const { data } = await api.get("/accounting/migration/verify");
                                const lines = [
                                    `الموظفون: ${data.counts.employees_with_balance}`,
                                    `الموردون: ${data.counts.suppliers_with_balance}`,
                                    `الأشخاص الخارجيون: ${data.counts.externals_with_balance}`,
                                    `الحسابات البنكية: ${data.counts.banks_with_balance}`,
                                    `إجمالي القيود الافتتاحية: ${data.counts.opening_entries_total}`,
                                    "",
                                    "مقارنة الأرصدة:",
                                    `راتب مستحق: قديم=${data.legacy_totals.salary_payable} ↔ جديد=${data.opening_totals.salary_payable} ${data.match.salary_payable ? "✓" : "✗"}`,
                                    `سلف موظفين: قديم=${data.legacy_totals.advance} ↔ جديد=${data.opening_totals.advance} ${data.match.advance ? "✓" : "✗"}`,
                                    `موردون: قديم=${data.legacy_totals.supplier_payable} ↔ جديد=${data.opening_totals.supplier_payable} ${data.match.supplier_payable ? "✓" : "✗"}`,
                                    `مستحقات خارجية: قديم=${data.legacy_totals.external_receivable} ↔ جديد=${data.opening_totals.external_receivable} ${data.match.external_receivable ? "✓" : "✗"}`,
                                    `أرصدة بنوك: قديم=${data.legacy_totals.bank_balance} ↔ جديد=${data.opening_totals.bank_balance} ${data.match.bank_balance ? "✓" : "✗"}`,
                                    "",
                                    data.all_match ? "✅ كل الأرصدة متطابقة 100%" : "⚠ يوجد اختلاف في بعض الأرصدة",
                                ];
                                alert(lines.join("\n"));
                            } catch (e) { toast.error("فشل تحميل التقرير"); }
                        }}
                            className="mt-2 text-xs text-emerald-800 underline hover:text-emerald-950"
                            data-testid="mig-verify-btn">
                            📊 عرض تقرير التحقق
                        </button>
                    </div>
                ) : (
                    <div className="bg-amber-50 border-2 border-amber-200 rounded-lg p-4 mb-4">
                        <div className="text-sm font-bold text-amber-900">⚠️ الترحيل لم يُنفَّذ بعد</div>
                        <div className="text-xs text-amber-700 mt-1">
                            يُنصح بتشغيل Dry Run أولاً والتحقق من المقارنة قبل الاعتماد النهائي.
                        </div>
                    </div>
                )}

                <div className="grid grid-cols-2 gap-3 mb-4">
                    <div>
                        <label className="block text-sm font-bold text-slate-700 mb-1">تاريخ القطع (Cutoff):</label>
                        <input type="date" value={cutoffDate}
                            onChange={e => setCutoffDate(e.target.value)}
                            disabled={status?.completed}
                            className="w-full px-3 py-2 border border-slate-300 rounded text-sm disabled:opacity-60"
                            data-testid="mig-cutoff-date" />
                    </div>
                    <div className="flex items-end gap-2">
                        <button onClick={runDryRun} disabled={busy}
                            className="flex-1 px-4 py-2 bg-sky-600 hover:bg-sky-700 text-white text-sm font-bold rounded disabled:opacity-50"
                            data-testid="mig-dry-run-btn">
                            {busy ? "جاري..." : "🔍 معاينة (Dry Run)"}
                        </button>
                    </div>
                </div>

                {dryRun && (
                    <div className="mt-4">
                        <div className="bg-slate-100 border border-slate-300 rounded-lg p-3 mb-3">
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="text-sm font-bold text-slate-900">
                                        {dryRun.dry_run ? "📋 نتائج المعاينة" : "✅ تم التطبيق"}
                                    </div>
                                    <div className="text-xs text-slate-600 mt-0.5">
                                        تاريخ القطع: {dryRun.cutoff_date} ·
                                        عدد القيود المخططة: {dryRun.planned_operations} ·
                                        المطبق: {dryRun.applied_count} ·
                                        حالة: <span className={dryRun.mismatch_count === 0 ? "text-emerald-700 font-bold" : "text-rose-700 font-bold"}>
                                            {dryRun.mismatch_count === 0 ? "متطابق" : `${dryRun.mismatch_count} عدم تطابق`}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {renderDiffTable("الموظفون", dryRun.diff.employees,
                            f => f === "salary_payable" ? "راتب مستحق"
                                : f === "advance" ? "سلف" : "عهدة")}
                        {renderDiffTable("الموردون", dryRun.diff.suppliers,
                            () => "مستحق للمورد")}
                        {renderDiffTable("الأشخاص الخارجيون", dryRun.diff.externals,
                            () => "مستحق لنا")}
                        {renderDiffTable("الحسابات البنكية", dryRun.diff.banks,
                            () => "رصيد")}

                        {dryRun.dry_run && !status?.completed && (
                            <div className="bg-rose-50 border-2 border-rose-200 rounded-lg p-4 mt-4">
                                <div className="text-sm font-bold text-rose-900 mb-2">
                                    ⚠️ التطبيق النهائي (لا يمكن التراجع)
                                </div>
                                <div className="text-xs text-rose-700 mb-3">
                                    سيتم إنشاء {dryRun.planned_operations} قيداً افتتاحياً.
                                    لتأكيد التطبيق اكتب: <strong>أوافق على الترحيل</strong>
                                </div>
                                <input type="text" value={confirmText}
                                    onChange={e => setConfirmText(e.target.value)}
                                    placeholder="أوافق على الترحيل"
                                    className="w-full px-3 py-2 border-2 border-rose-300 rounded text-sm mb-2"
                                    data-testid="mig-confirm-text" />
                                <button onClick={applyMigration}
                                    disabled={busy || confirmText !== "أوافق على الترحيل"}
                                    className="w-full px-4 py-2.5 bg-rose-600 hover:bg-rose-700 text-white text-sm font-bold rounded disabled:opacity-50"
                                    data-testid="mig-apply-btn">
                                    🚀 تطبيق الترحيل النهائي
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

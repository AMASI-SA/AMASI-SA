/**
 * Iter-230 — صفحة تشخيص فرق المديونيات الإعلانية (Read-Only).
 *
 * تقرير قراءة-فقط يقارن بين:
 *   - walk_balance (من ad_account_ledger، ما تستخدمه صفحة /ad-accounts)
 *   - ssot_balance (من general_ledger، ما يستخدمه المركز المالي)
 *
 * لا أرشفة، لا تعديل، لا حذف. فقط شفافية كاملة عن مصدر الفرق.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (n) =>
    (Number(n) || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });

const errMsg = (e, fb) =>
    e?.response?.data?.detail || e?.response?.data?.error || e?.message || fb;


export default function AdDebtDiagnostic() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState(null);

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/audit/ad-debt-diagnostic");
            setData(data);
        } catch (e) {
            toast.error(errMsg(e, "فشل تحميل التقرير"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const downloadJSON = () => {
        if (!data) return;
        const blob = new Blob([JSON.stringify(data, null, 2)],
            { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `ad_debt_diagnostic_${new Date().toISOString().slice(0,19).replace(/[:T]/g,"-")}.json`;
        document.body.appendChild(a); a.click();
        document.body.removeChild(a); URL.revokeObjectURL(url);
    };

    const summary = data?.summary || {};
    const accounts = data?.accounts || [];
    const mismatched = accounts.filter(a => !a.match);

    return (
        <div className="space-y-6" dir="rtl" data-testid="ad-debt-diagnostic">
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">
                        تشخيص فرق المديونيات الإعلانية
                    </h1>
                    <p className="text-xs text-slate-500 mt-1">
                        تقرير قراءة فقط — يقارن walk_balance (من
                        ad_account_ledger) مع ssot_balance (من general_ledger).
                        لا يُجري أي تعديل على البيانات.
                    </p>
                </div>
                <div className="flex gap-2">
                    <button onClick={downloadJSON} disabled={!data}
                        className="px-3 py-2 rounded-lg border border-violet-300
                                   bg-violet-50 text-violet-800 text-xs font-bold
                                   disabled:opacity-60"
                        data-testid="btn-export-json">
                        📤 تصدير JSON
                    </button>
                    <button onClick={load} disabled={loading}
                        className="px-3 py-2 rounded-lg border border-slate-300
                                   text-xs font-bold text-slate-700 hover:bg-slate-50">
                        {loading ? "..." : "تحديث"}
                    </button>
                </div>
            </div>

            {/* Summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl border p-4 bg-emerald-50 border-emerald-200">
                    <div className="text-[10px] font-bold text-emerald-700">
                        walk_balance (Ad Accounts page)
                    </div>
                    <div className="num text-xl font-extrabold text-emerald-900"
                         data-testid="stat-walk">
                        {fmt(summary.total_walk_balance)} ر.س
                    </div>
                </div>
                <div className="rounded-xl border p-4 bg-violet-50 border-violet-200">
                    <div className="text-[10px] font-bold text-violet-700">
                        ssot_balance (Financial Position)
                    </div>
                    <div className="num text-xl font-extrabold text-violet-900"
                         data-testid="stat-ssot">
                        {fmt(summary.total_ssot_balance)} ر.س
                    </div>
                </div>
                <div className={`rounded-xl border p-4 ${Math.abs(summary.total_difference) < 0.01
                    ? "bg-emerald-50 border-emerald-200"
                    : "bg-rose-50 border-rose-200"}`}>
                    <div className="text-[10px] font-bold opacity-80">الفرق الإجمالي</div>
                    <div className="num text-xl font-extrabold"
                         data-testid="stat-diff">
                        {(summary.total_difference ?? 0) >= 0 ? "+" : "−"}
                        {fmt(Math.abs(summary.total_difference))} ر.س
                    </div>
                </div>
                <div className="rounded-xl border p-4 bg-amber-50 border-amber-200">
                    <div className="text-[10px] font-bold text-amber-700">
                        حسابات بفرق
                    </div>
                    <div className="num text-xl font-extrabold text-amber-900">
                        {summary.accounts_mismatch_count} / {summary.accounts_count}
                    </div>
                </div>
            </div>

            {/* Global attribution by entry_type */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <h2 className="text-base font-extrabold text-slate-900 mb-3">
                    تفصيل SSOT حسب نوع القيد (entry_type)
                </h2>
                <div className="flex flex-wrap gap-2">
                    {(summary.global_attribution_by_entry_type || []).map(r => (
                        <span key={r.entry_type}
                              className="px-3 py-1.5 rounded-lg border
                                         border-slate-200 bg-slate-50 text-xs">
                            <span className="font-mono font-bold">{r.entry_type}</span>:{" "}
                            <span className="num font-bold">
                                {r.net_contribution >= 0 ? "+" : "−"}
                                {fmt(Math.abs(r.net_contribution))}
                            </span>
                        </span>
                    ))}
                </div>
            </div>

            {/* Mismatched accounts */}
            <div className="bg-white border border-slate-200 rounded-2xl p-5">
                <h2 className="text-base font-extrabold text-slate-900 mb-3">
                    الحسابات الإعلانية ({mismatched.length} بفرق · {accounts.length} الإجمالي)
                </h2>
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="text-slate-500 font-bold border-b border-slate-200">
                                <th className="text-right py-2 px-2">الحساب</th>
                                <th className="text-right py-2 px-2">المنصة</th>
                                <th className="text-left py-2 px-2 num">walk</th>
                                <th className="text-left py-2 px-2 num">ssot</th>
                                <th className="text-left py-2 px-2 num">الفرق</th>
                                <th className="text-left py-2 px-2 num">credit</th>
                                <th className="text-left py-2 px-2 num">debit</th>
                                <th className="text-center py-2 px-2">قيود مؤرشفة</th>
                                <th className="text-center py-2 px-2"></th>
                            </tr>
                        </thead>
                        <tbody>
                            {accounts.map(a => (
                                <>
                                <tr key={a.account_id}
                                    className={`border-b border-slate-100 hover:bg-slate-50
                                                ${!a.match ? "bg-rose-50/30" : ""}`}
                                    data-testid={`acc-row-${a.account_id}`}>
                                    <td className="py-2 px-2 font-bold">{a.account_name}</td>
                                    <td className="py-2 px-2 font-mono text-[10px]">{a.platform}</td>
                                    <td className="py-2 px-2 num text-left text-emerald-700">{fmt(a.walk_balance)}</td>
                                    <td className="py-2 px-2 num text-left text-violet-700">{fmt(a.ssot_balance)}</td>
                                    <td className={`py-2 px-2 num text-left font-bold
                                                    ${a.match ? "text-emerald-700" : "text-rose-700"}`}>
                                        {a.difference >= 0 ? "+" : "−"}{fmt(Math.abs(a.difference))}
                                    </td>
                                    <td className="py-2 px-2 num text-left">{fmt(a.ssot_total_credit)}</td>
                                    <td className="py-2 px-2 num text-left">{fmt(a.ssot_total_debit)}</td>
                                    <td className="py-2 px-2 text-center text-[10px]">
                                        {a.ssot_archived_count} ({fmt(a.ssot_archived_net)})
                                    </td>
                                    <td className="py-2 px-2 text-center">
                                        <button
                                            onClick={() => setExpanded(
                                                expanded === a.account_id ? null : a.account_id)}
                                            className="text-violet-700 text-[11px] font-bold hover:underline"
                                            data-testid={`expand-${a.account_id}`}>
                                            {expanded === a.account_id ? "إخفاء" : "عرض القيود"}
                                        </button>
                                    </td>
                                </tr>
                                {expanded === a.account_id && (
                                <tr><td colSpan={9} className="p-3 bg-slate-50">
                                    <div className="mb-2 text-[11px] font-bold">
                                        تفصيل {a.entries.length} قيد ·{" "}
                                        ssot_by_entry_type:{" "}
                                        {Object.entries(a.ssot_by_entry_type || {}).map(([k,v]) =>
                                            <span key={k} className="mx-1 font-mono">
                                                {k}: +{fmt(v.credit)}/−{fmt(v.debit)} ({v.count})
                                            </span>
                                        )}
                                    </div>
                                    <table className="w-full text-[10px] bg-white">
                                        <thead>
                                            <tr className="text-slate-500 border-b">
                                                <th className="text-right py-1 px-2">التاريخ</th>
                                                <th className="text-right py-1 px-2">entry_type</th>
                                                <th className="text-right py-1 px-2">side</th>
                                                <th className="text-left py-1 px-2 num">amount</th>
                                                <th className="text-right py-1 px-2">ledger_id</th>
                                                <th className="text-right py-1 px-2">txn_group_id</th>
                                                <th className="text-right py-1 px-2">المصدر</th>
                                                <th className="text-center py-1 px-2">ضمن SSOT؟</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {a.entries.map((e,i) => (
                                                <tr key={e.ledger_id || i}
                                                    className={`border-b border-slate-100
                                                                ${!e.contributes_to_ssot ? "opacity-50" : ""}`}>
                                                    <td className="py-1 px-2 num">{(e.posted_at||"").slice(0,10)}</td>
                                                    <td className="py-1 px-2 font-mono">{e.entry_type}</td>
                                                    <td className="py-1 px-2">{e.side}</td>
                                                    <td className={`py-1 px-2 num text-left font-bold
                                                                    ${e.side === "credit" ? "text-rose-600" : "text-emerald-600"}`}>
                                                        {fmt(e.amount)}
                                                    </td>
                                                    <td className="py-1 px-2 font-mono text-[9px]">{(e.ledger_id||"").slice(0,12)}</td>
                                                    <td className="py-1 px-2 font-mono text-[9px]">{(e.txn_group_id||"").slice(0,12)}</td>
                                                    <td className="py-1 px-2">{e.metadata_source||"—"}</td>
                                                    <td className="py-1 px-2 text-center">
                                                        {e.contributes_to_ssot ? "✓" :
                                                            e.is_reversal ? "↩ reversal" :
                                                            e.is_archived ? "🗄 archived" : "—"}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </td></tr>
                                )}
                                </>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}

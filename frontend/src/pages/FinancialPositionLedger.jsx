// Iter-161 Phase 4 — Financial Position (Ledger-only)
// Replaces legacy financial-position page that aggregated from
// liabilities + account_transactions. NOW reads strictly from
// /api/accounting/financial-position (which derives from general_ledger).

import React, { useState, useEffect } from "react";
import api from "../lib/api";
import { toast } from "sonner";

const fmt = (n) => Number(n || 0).toLocaleString(
    "en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const ASSET_LABELS = {
    bank: "النقدية والبنوك",
    employee_advance: "سلف موظفين (مستحقة منهم)",
    employee_custody: "عهد موظفين",
    external_receivable: "مستحقات من أشخاص خارجيين",
    courier_cod_receivable: "COD لم يُحوَّل من شركات الشحن",
    ad_account_prepaid: "أرصدة مدفوعة مقدماً للحسابات الإعلانية",
    input_vat: "ضريبة مدخلات قابلة للاسترداد",
};

const LIABILITY_LABELS = {
    employee_salary_payable: "رواتب مستحقة للموظفين",
    supplier_payable: "مستحقات للموردين",
    courier_payable: "مستحقات لشركات الشحن",
    external_payable: "مستحقات لأشخاص خارجيين",
    ad_account_debt: "مديونيات الحسابات الإعلانية",
};

export default function FinancialPositionLedger() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const { data: d } = await api.get("/accounting/financial-position");
            setData(d);
        } catch (e) {
            toast.error("فشل تحميل المركز المالي");
        } finally { setLoading(false); }
    };
    useEffect(() => { load(); }, []);

    if (loading || !data) {
        return <div className="p-8 text-center text-slate-400">جاري التحميل...</div>;
    }

    return (
        <div className="p-6 max-w-5xl mx-auto" data-testid="financial-position-ledger">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <h1 className="text-2xl font-extrabold text-slate-900 mb-1">
                    💰 المركز المالي (Ledger)
                </h1>
                <p className="text-xs text-slate-500 mb-6">
                    كل الأرصدة محسوبة حصراً من <code className="bg-slate-100 px-1 rounded">general_ledger</code> — Phase 4.
                    المصدر: <span className="font-bold">{data.source}</span>
                </p>

                <div className="grid grid-cols-3 gap-4 mb-6">
                    <div className="bg-emerald-50 border-2 border-emerald-300 rounded-xl p-4">
                        <div className="text-xs text-slate-600 font-bold mb-1">إجمالي الأصول</div>
                        <div className="text-2xl font-extrabold text-emerald-700 num">
                            {fmt(data.totals.total_assets)} <span className="text-sm">ر.س</span>
                        </div>
                    </div>
                    <div className="bg-rose-50 border-2 border-rose-300 rounded-xl p-4">
                        <div className="text-xs text-slate-600 font-bold mb-1">إجمالي الالتزامات</div>
                        <div className="text-2xl font-extrabold text-rose-700 num">
                            {fmt(data.totals.total_liabilities)} <span className="text-sm">ر.س</span>
                        </div>
                    </div>
                    <div className={`${data.totals.net_position >= 0 ? "bg-sky-50 border-sky-300" : "bg-amber-50 border-amber-300"} border-2 rounded-xl p-4`}>
                        <div className="text-xs text-slate-600 font-bold mb-1">صافي المركز المالي</div>
                        <div className={`text-2xl font-extrabold num ${data.totals.net_position >= 0 ? "text-sky-700" : "text-amber-700"}`}>
                            {fmt(data.totals.net_position)} <span className="text-sm">ر.س</span>
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-6">
                    {/* Assets */}
                    <div>
                        <h2 className="text-lg font-extrabold text-emerald-800 mb-2 border-b border-emerald-200 pb-1">
                            🟢 الأصول
                        </h2>
                        <table className="w-full text-sm">
                            <tbody>
                                {Object.entries(data.assets).map(([key, val]) => (
                                    <tr key={key} className="border-b border-slate-100">
                                        <td className="py-2 text-slate-700">{ASSET_LABELS[key] || key}</td>
                                        <td className={`text-left py-2 num font-bold ${val >= 0 ? "text-emerald-700" : "text-rose-700"}`}>
                                            {fmt(val)}
                                        </td>
                                    </tr>
                                ))}
                                <tr className="font-extrabold border-t-2 border-emerald-300">
                                    <td className="py-2">الإجمالي</td>
                                    <td className="text-left py-2 num text-emerald-700">{fmt(data.totals.total_assets)}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>

                    {/* Liabilities */}
                    <div>
                        <h2 className="text-lg font-extrabold text-rose-800 mb-2 border-b border-rose-200 pb-1">
                            🔴 الالتزامات
                        </h2>
                        <table className="w-full text-sm">
                            <tbody>
                                {Object.entries(data.liabilities).map(([key, val]) => (
                                    <tr key={key} className="border-b border-slate-100">
                                        <td className="py-2 text-slate-700">{LIABILITY_LABELS[key] || key}</td>
                                        <td className="text-left py-2 num font-bold text-rose-700">
                                            {fmt(val)}
                                        </td>
                                    </tr>
                                ))}
                                <tr className="font-extrabold border-t-2 border-rose-300">
                                    <td className="py-2">الإجمالي</td>
                                    <td className="text-left py-2 num text-rose-700">{fmt(data.totals.total_liabilities)}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <div className="mt-6 text-[10px] text-slate-400 text-center">
                    معادلة المركز المالي: الأصول − الالتزامات = صافي المركز
                </div>
            </div>
        </div>
    );
}

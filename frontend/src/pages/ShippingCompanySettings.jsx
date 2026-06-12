/**
 * Iter-144 — Shipping company settings (deferred/immediate + COD fees).
 *
 * Lives alongside (NOT replacing) the existing Settings → Shipping
 * Companies tab.  This page focuses on the fields that drive the new
 * unified ledger.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";


const fmt = (v) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });


export default function ShippingCompanySettings() {
    const [settings, setSettings] = useState(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        api.get("/settings").then(({ data }) => setSettings(data)).catch(() => { });
    }, []);

    if (!settings) return <div className="p-8 text-center text-slate-500">جاري التحميل…</div>;
    const companies = settings.shipping_companies || [];

    const updateCompany = (idx, patch) => {
        const next = companies.map((c, i) => i === idx ? { ...c, ...patch } : c);
        setSettings({ ...settings, shipping_companies: next });
    };

    const save = async () => {
        setBusy(true);
        try {
            await api.put("/settings", settings);
            toast.success("تم حفظ الإعدادات");
        } catch (e) {
            toast.error("فشل الحفظ — راجع الكونسول");
        } finally { setBusy(false); }
    };

    return (
        <div className="p-6 max-w-6xl mx-auto space-y-5" dir="rtl" data-testid="shipping-settings-page">
            <header className="flex items-center justify-between gap-2 flex-wrap">
                <h1 className="text-2xl font-extrabold text-slate-900">⚙️ إعدادات شركات الشحن</h1>
                <Link to="/shipping/ledger" className="px-3 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 text-sm font-bold rounded-lg" data-testid="settings-back">
                    ← الأرصدة
                </Link>
            </header>

            <div className="rounded-xl bg-blue-50 border-2 border-blue-200 text-blue-900 text-[12px] p-3" data-testid="settings-help">
                <strong>نوع الشركة:</strong>
                <span className="me-3"><span className="px-1.5 py-0.5 bg-emerald-200 rounded text-emerald-900 text-[10px] font-bold mx-1">آجلة</span> تدخل أرصدة شركات الشحن (دائن/مدين/COD/رسوم).</span>
                <span><span className="px-1.5 py-0.5 bg-slate-200 rounded text-slate-700 text-[10px] font-bold mx-1">فورية</span> تكلفة الشحن مصروف مباشر — لا يدخل الـ ledger.</span>
            </div>

            <div className="bg-white border-2 border-slate-200 rounded-xl overflow-hidden">
                <table className="w-full text-xs" data-testid="settings-table">
                    <thead className="bg-slate-50 text-slate-700">
                        <tr>
                            <th className="p-2 text-right">الاسم</th>
                            <th className="p-2 text-right">النوع</th>
                            <th className="p-2 num text-right">تكلفة/طلب</th>
                            <th className="p-2 num text-right">VAT %</th>
                            <th className="p-2 num text-right">رسوم COD %</th>
                            <th className="p-2 num text-right">رسوم COD ثابتة/طلب</th>
                        </tr>
                    </thead>
                    <tbody>
                        {companies.map((c, idx) => (
                            <tr key={c.name + idx} className="border-t border-slate-100" data-testid={`settings-row-${c.name}`}>
                                <td className="p-2 font-bold">{c.name}</td>
                                <td className="p-2">
                                    <div className="inline-flex bg-slate-100 rounded-full p-0.5">
                                        <button type="button" onClick={() => updateCompany(idx, { is_deferred: true })} className={`px-3 py-1 rounded-full text-[10px] font-extrabold ${c.is_deferred ? "bg-emerald-600 text-white" : "text-slate-600"}`} data-testid={`type-deferred-${c.name}`}>آجلة</button>
                                        <button type="button" onClick={() => updateCompany(idx, { is_deferred: false })} className={`px-3 py-1 rounded-full text-[10px] font-extrabold ${!c.is_deferred ? "bg-slate-500 text-white" : "text-slate-600"}`} data-testid={`type-immediate-${c.name}`}>فورية</button>
                                    </div>
                                </td>
                                <td className="p-2"><input type="number" step="0.01" value={c.cost_per_order ?? c.cost ?? 0} onChange={(e) => updateCompany(idx, { cost_per_order: Number(e.target.value), cost: Number(e.target.value) })} className="w-24 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`cost-${c.name}`} /></td>
                                <td className="p-2"><input type="number" step="0.01" value={(c.vat_percent ?? (c.vat_rate || 0) * 100) || 0} onChange={(e) => updateCompany(idx, { vat_percent: Number(e.target.value), vat_rate: Number(e.target.value) / 100 })} className="w-16 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`vat-${c.name}`} /></td>
                                <td className="p-2"><input type="number" step="0.001" min="0" max="1" value={c.cod_fee_percent ?? 0} onChange={(e) => updateCompany(idx, { cod_fee_percent: Number(e.target.value) })} className="w-20 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`cod-pct-${c.name}`} placeholder="0.01" disabled={!c.is_deferred} /></td>
                                <td className="p-2"><input type="number" step="0.01" min="0" value={c.cod_fee_fixed_per_order ?? 0} onChange={(e) => updateCompany(idx, { cod_fee_fixed_per_order: Number(e.target.value) })} className="w-20 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`cod-fixed-${c.name}`} placeholder="5.00" disabled={!c.is_deferred} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div className="text-left">
                <button onClick={save} disabled={busy} className="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg disabled:opacity-50" data-testid="settings-save">
                    {busy ? "جاري الحفظ…" : "💾 حفظ الإعدادات"}
                </button>
            </div>
        </div>
    );
}

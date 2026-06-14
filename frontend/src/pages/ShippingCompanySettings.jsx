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

    // Storage is 0-1 decimal (e.g. 0.05 = 5%); UI shows percent (e.g. 5).
    // These helpers do the conversion at the edge so users type "5" and
    // not "0.05" — matching the convention used by payment_methods on
    // the main Settings page.
    const pctToDecimal = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return 0;
        return Math.max(0, Math.min(100, n)) / 100;
    };
    const decimalToPct = (v) => {
        const n = Number(v || 0);
        if (!Number.isFinite(n)) return 0;
        return Math.round(n * 10000) / 100; // 2-decimal percent
    };

    const updateCompany = (idx, patch) => {
        const next = companies.map((c, i) => i === idx ? { ...c, ...patch } : c);
        setSettings({ ...settings, shipping_companies: next });
    };

    // Iter-155 — add a brand-new shipping company row inline.
    const addCompany = () => {
        const name = (window.prompt("اسم شركة الشحن الجديدة:") || "").trim();
        if (!name) return;
        if (companies.some((c) => (c.name || "").trim() === name)) {
            toast.error("شركة بهذا الاسم موجودة بالفعل");
            return;
        }
        const next = [...companies, {
            name,
            cost_per_order: 0.0,
            vat_percent: 15.0,
            is_deferred: true,
            cod_fee_percent: 0.0,
            cod_fee_fixed_per_order: 0.0,
        }];
        setSettings({ ...settings, shipping_companies: next });
    };

    const removeCompany = (idx) => {
        const name = companies[idx]?.name || "";
        if (!window.confirm(`حذف «${name}» من قائمة شركات الشحن؟ هذا لن يحذف الطلبات المرتبطة، فقط يزيلها من الإعدادات.`)) return;
        const next = companies.filter((_, i) => i !== idx);
        setSettings({ ...settings, shipping_companies: next });
    };

    const save = async () => {
        setBusy(true);
        try {
            // Iter-178 — clamp every cod_fee_percent to its valid 0-1
            // decimal range BEFORE sending, in case any legacy row
            // arrived with a stale percent-shaped value (e.g. 5 instead
            // of 0.05). Without this guard the whole save fails when a
            // user adds a NEW company because Pydantic rejects ANY row
            // outside [0, 1].
            const payload = {
                ...settings,
                shipping_companies: (settings.shipping_companies || []).map((c) => ({
                    ...c,
                    cod_fee_percent: Math.max(
                        0, Math.min(1, Number(c.cod_fee_percent) || 0)),
                    cod_fee_fixed_per_order: Math.max(
                        0, Number(c.cod_fee_fixed_per_order) || 0),
                })),
            };
            await api.put("/settings", payload);
            toast.success("تم حفظ الإعدادات");
        } catch (e) {
            const detail = e?.response?.data?.detail;
            const msg = typeof detail === "string"
                ? detail
                : Array.isArray(detail)
                    ? detail.map((d) => d?.msg || JSON.stringify(d)).join(" · ")
                    : "فشل الحفظ — راجع الكونسول";
            toast.error(msg);
            console.error("Settings save failed:", detail || e);
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
                <div className="mt-2 pt-2 border-t border-blue-200">
                    <strong>رسوم COD:</strong> اكتب النسبة كرقم عادي مثل <code className="bg-white px-1 rounded">5</code> لتعني 5%. الحقل الثاني للرسوم الثابتة بالريال لكل طلب موصَّل.
                </div>
            </div>

            <div className="bg-white border-2 border-slate-200 rounded-xl overflow-hidden">
                <table className="w-full text-xs" data-testid="settings-table">
                    <thead className="bg-slate-50 text-slate-700">
                        <tr>
                            <th className="p-2 text-right">الاسم</th>
                            <th className="p-2 text-right">طريقة السداد</th>
                            <th className="p-2 num text-right">تكلفة/طلب</th>
                            <th className="p-2 num text-right">VAT %</th>
                            <th className="p-2 num text-right">رسوم COD %</th>
                            <th className="p-2 num text-right">رسوم COD ثابتة/طلب</th>
                            <th className="p-2 text-right w-12">إجراءات</th>
                        </tr>
                    </thead>
                    <tbody>
                        {companies.map((c, idx) => (
                            <tr key={c.name + idx} className="border-t border-slate-100" data-testid={`settings-row-${c.name}`}>
                                <td className="p-2 font-bold">{c.name}</td>
                                <td className="p-2">
                                    {/* Iter-189 — Prominent payment-mode picker.
                                        Replaces the old "آجلة / فورية" pill with
                                        clear two-card toggle showing icon + label. */}
                                    <div className="inline-flex gap-1 bg-slate-50 rounded-lg p-1 border border-slate-200">
                                        <button type="button"
                                            onClick={() => updateCompany(idx, { is_deferred: false, payment_mode: "prepaid" })}
                                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-extrabold transition-colors ${
                                                !c.is_deferred
                                                    ? "bg-emerald-600 text-white shadow-sm"
                                                    : "text-slate-600 hover:bg-slate-100"
                                            }`}
                                            data-testid={`payment-mode-prepaid-${c.name}`}>
                                            <span>🟢</span>
                                            <span>دفع مقدم</span>
                                        </button>
                                        <button type="button"
                                            onClick={() => updateCompany(idx, { is_deferred: true, payment_mode: "deferred" })}
                                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-extrabold transition-colors ${
                                                c.is_deferred
                                                    ? "bg-amber-600 text-white shadow-sm"
                                                    : "text-slate-600 hover:bg-slate-100"
                                            }`}
                                            data-testid={`payment-mode-deferred-${c.name}`}>
                                            <span>🟠</span>
                                            <span>دفع آجل</span>
                                        </button>
                                    </div>
                                    <div className="text-[10px] text-slate-500 mt-1 leading-tight max-w-[180px]">
                                        {c.is_deferred
                                            ? "تكلفة الشحن مستحقة على الشركة (ذمة)."
                                            : "مخصومة مسبقاً من مستحقات سلة."}
                                    </div>
                                </td>
                                <td className="p-2"><input type="number" step="0.01" value={c.cost_per_order ?? c.cost ?? 0} onChange={(e) => updateCompany(idx, { cost_per_order: Number(e.target.value), cost: Number(e.target.value) })} className="w-24 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`cost-${c.name}`} /></td>
                                <td className="p-2"><input type="number" step="0.01" value={(c.vat_percent ?? (c.vat_rate || 0) * 100) || 0} onChange={(e) => updateCompany(idx, { vat_percent: Number(e.target.value), vat_rate: Number(e.target.value) / 100 })} className="w-16 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`vat-${c.name}`} /></td>
                                <td className="p-2">
                                    <div className="flex items-center border border-slate-300 rounded overflow-hidden bg-white">
                                        <input
                                            type="number"
                                            step="0.01"
                                            min="0"
                                            max="100"
                                            value={decimalToPct(c.cod_fee_percent)}
                                            onChange={(e) => updateCompany(idx, { cod_fee_percent: pctToDecimal(e.target.value) })}
                                            className="w-16 px-1 py-1 text-xs num text-right bg-transparent focus:outline-none"
                                            data-testid={`cod-pct-${c.name}`}
                                            placeholder="5"
                                            disabled={!c.is_deferred}
                                            title="نسبة مئوية مثل 5 = 5%"
                                        />
                                        <span className="px-1.5 text-[10px] text-slate-500 border-s border-slate-300 bg-slate-50">%</span>
                                    </div>
                                </td>
                                <td className="p-2"><input type="number" step="0.01" min="0" value={c.cod_fee_fixed_per_order ?? 0} onChange={(e) => updateCompany(idx, { cod_fee_fixed_per_order: Number(e.target.value) })} className="w-20 border border-slate-300 rounded px-1 py-1 text-xs num text-right" data-testid={`cod-fixed-${c.name}`} placeholder="5.00" disabled={!c.is_deferred} /></td>
                                <td className="p-2">
                                    <button type="button" onClick={() => removeCompany(idx)} className="text-rose-500 hover:text-rose-700 text-sm font-extrabold" title="حذف من الإعدادات" data-testid={`remove-${c.name}`}>×</button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div className="flex items-center justify-between gap-3">
                <button onClick={addCompany} type="button" className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold rounded-lg" data-testid="add-shipping-company">
                    ➕ إضافة شركة جديدة
                </button>
                <button onClick={save} disabled={busy} className="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg disabled:opacity-50" data-testid="settings-save">
                    {busy ? "جاري الحفظ…" : "💾 حفظ الإعدادات"}
                </button>
            </div>
        </div>
    );
}

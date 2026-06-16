/**
 * Iter-236 — Ads currency & bank-commission settings page.
 * Route: /settings/ads-currencies
 *
 * - Global settings (USD→SAR rate + bank commission %)
 * - Per-ad-account currency + apply_bank_commission toggle
 * - Live calculation preview with merchant's example values
 */
import { useState, useEffect, useMemo } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const fmt = (n) => Number(n ?? 0).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
});

export default function AdsCurrencySettings() {
    const [settings, setSettings] = useState({
        usd_to_sar_rate: 3.7544,
        bank_commission_pct: 2.30,
        bank_fees_expense_account_id: null,
    });
    const [adAccounts, setAdAccounts] = useState([]);
    const [summary, setSummary] = useState({
        count: 0, total_ads_spend: 0, total_bank_fees: 0, total_due: 0,
    });
    const [previewUsd, setPreviewUsd] = useState(1000);
    const [previewSar, setPreviewSar] = useState(5000);
    const [saving, setSaving] = useState(false);
    const [loading, setLoading] = useState(true);

    const load = async () => {
        setLoading(true);
        try {
            const [s, cps, sm] = await Promise.all([
                api.get("/ads-currency-settings"),
                api.get("/counterparties?kind=ad_account"),
                api.get("/ads-currency-settings/summary"),
            ]);
            setSettings({
                usd_to_sar_rate: s.data?.usd_to_sar_rate ?? 3.7544,
                bank_commission_pct: s.data?.bank_commission_pct ?? 2.30,
                bank_fees_expense_account_id: s.data?.bank_fees_expense_account_id ?? null,
            });
            setAdAccounts(cps.data?.items || []);
            setSummary(sm.data || {});
        } catch (e) {
            toast.error("تعذّر تحميل الإعدادات");
        } finally {
            setLoading(false);
        }
    };
    useEffect(() => { load(); }, []);

    const saveSettings = async () => {
        setSaving(true);
        try {
            await api.put("/ads-currency-settings", {
                usd_to_sar_rate: Number(settings.usd_to_sar_rate),
                bank_commission_pct: Number(settings.bank_commission_pct),
            });
            toast.success("تم حفظ الإعدادات");
            await load();
        } catch (e) {
            toast.error("فشل الحفظ");
        } finally {
            setSaving(false);
        }
    };

    const saveAccountConfig = async (cpId, currency, applyFee) => {
        try {
            await api.put(`/ads-currency-settings/account/${cpId}`, {
                currency, apply_bank_commission: applyFee,
            });
            toast.success("تم الحفظ");
            setAdAccounts((prev) => prev.map((a) => (
                a.id === cpId
                    ? { ...a, currency, apply_bank_commission: applyFee }
                    : a
            )));
        } catch (e) {
            toast.error("فشل حفظ الحساب");
        }
    };

    // Live preview using local math (mirrors backend `compute_ads_amounts`)
    const calcUsd = useMemo(() => {
        const amt = Number(previewUsd) || 0;
        const rate = Number(settings.usd_to_sar_rate) || 0;
        const sar = amt * rate;
        const fee = sar * (Number(settings.bank_commission_pct) || 0) / 100;
        return { sar, fee, total: sar + fee };
    }, [previewUsd, settings]);

    const calcSar = useMemo(() => {
        const amt = Number(previewSar) || 0;
        const fee = amt * (Number(settings.bank_commission_pct) || 0) / 100;
        return { fee, total: amt + fee };
    }, [previewSar, settings]);

    return (
        <div className="p-2 sm:p-4 lg:p-6 max-w-[1300px] mx-auto"
             data-testid="ads-currency-settings-page">
            <div className="bg-white rounded-2xl shadow-lg p-4 sm:p-5 lg:p-6">
                <div className="mb-5">
                    <h1 className="text-xl sm:text-2xl font-extrabold text-slate-900">
                        💱 إعدادات عملات الحسابات الإعلانية
                    </h1>
                    <p className="text-xs sm:text-sm text-slate-500 mt-1">
                        أتمتة احتساب رسوم البنك وسعر الصرف عند تسجيل فواتير الإعلانات.
                        التغيير لا يؤثر على الفواتير القديمة.
                    </p>
                </div>

                {loading ? (
                    <div className="text-center p-8 text-slate-400">جاري التحميل…</div>
                ) : (
                    <>
                        {/* Summary cards (across ALL ad-account liabilities) */}
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 mb-5"
                             data-testid="ads-summary-cards">
                            <div className="rounded-xl border-2 border-slate-200 bg-slate-50 p-3 min-w-0">
                                <div className="text-[10px] sm:text-[11px] font-bold text-slate-600 leading-tight truncate">عدد الفواتير</div>
                                <div className="text-base sm:text-lg font-extrabold text-slate-800 num mt-0.5">
                                    {Number(summary.count || 0).toLocaleString("en-US")}
                                </div>
                            </div>
                            <div className="rounded-xl border-2 border-emerald-200 bg-emerald-50 p-3 min-w-0">
                                <div className="text-[10px] sm:text-[11px] font-bold text-slate-600 leading-tight truncate">إجمالي الإنفاق الإعلاني</div>
                                <div className="flex items-baseline gap-1 mt-0.5 text-emerald-800">
                                    <span className="text-sm sm:text-base lg:text-lg font-extrabold num truncate min-w-0"
                                          title={fmt(summary.total_ads_spend)}>{fmt(summary.total_ads_spend)}</span>
                                    <span className="text-[9px] sm:text-[10px] font-bold shrink-0">ر.س</span>
                                </div>
                            </div>
                            <div className="rounded-xl border-2 border-rose-200 bg-rose-50 p-3 min-w-0">
                                <div className="text-[10px] sm:text-[11px] font-bold text-slate-600 leading-tight truncate">إجمالي رسوم البنك</div>
                                <div className="flex items-baseline gap-1 mt-0.5 text-rose-800">
                                    <span className="text-sm sm:text-base lg:text-lg font-extrabold num truncate min-w-0"
                                          title={fmt(summary.total_bank_fees)}>{fmt(summary.total_bank_fees)}</span>
                                    <span className="text-[9px] sm:text-[10px] font-bold shrink-0">ر.س</span>
                                </div>
                            </div>
                            <div className="rounded-xl border-2 border-violet-200 bg-violet-50 p-3 min-w-0">
                                <div className="text-[10px] sm:text-[11px] font-bold text-slate-600 leading-tight truncate">إجمالي المديونية</div>
                                <div className="flex items-baseline gap-1 mt-0.5 text-violet-800">
                                    <span className="text-sm sm:text-base lg:text-lg font-extrabold num truncate min-w-0"
                                          title={fmt(summary.total_due)}>{fmt(summary.total_due)}</span>
                                    <span className="text-[9px] sm:text-[10px] font-bold shrink-0">ر.س</span>
                                </div>
                            </div>
                        </div>

                        {/* Global settings */}
                        <section className="mb-6 p-4 bg-slate-50 border-2 border-slate-200 rounded-xl">
                            <h2 className="text-sm font-extrabold text-slate-800 mb-3">
                                الإعدادات العامة
                            </h2>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                <label className="block">
                                    <span className="text-xs font-bold text-slate-700">
                                        سعر صرف الدولار (USD → SAR)
                                    </span>
                                    <input type="number" step="0.0001" min="0" max="20"
                                        value={settings.usd_to_sar_rate}
                                        onChange={(e) => setSettings({
                                            ...settings, usd_to_sar_rate: e.target.value,
                                        })}
                                        data-testid="usd-rate-input"
                                        className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg num font-bold focus:border-emerald-500 outline-none" />
                                    <span className="text-[10px] text-slate-500">مثال: 3.7544</span>
                                </label>
                                <label className="block">
                                    <span className="text-xs font-bold text-slate-700">
                                        نسبة العمولة البنكية (%)
                                    </span>
                                    <input type="number" step="0.01" min="0" max="20"
                                        value={settings.bank_commission_pct}
                                        onChange={(e) => setSettings({
                                            ...settings, bank_commission_pct: e.target.value,
                                        })}
                                        data-testid="bank-pct-input"
                                        className="mt-1 w-full px-3 py-2 border border-slate-300 rounded-lg num font-bold focus:border-emerald-500 outline-none" />
                                    <span className="text-[10px] text-slate-500">
                                        تطبق على الحسابات المُفعَّل لديها &quot;تطبيق العمولة&quot;
                                    </span>
                                </label>
                            </div>
                            <div className="mt-3 flex items-center justify-between">
                                <span className="text-[10px] text-slate-500">
                                    حساب رسوم البنك:
                                    {settings.bank_fees_expense_account_id ? (
                                        <strong className="text-emerald-700 mx-1">«رسوم بنكية وعمولات بطاقات» جاهز ✓</strong>
                                    ) : (
                                        <span className="text-amber-700 mx-1">سيُنشأ تلقائياً عند الحفظ.</span>
                                    )}
                                </span>
                                <button type="button" onClick={saveSettings} disabled={saving}
                                    data-testid="save-global-settings"
                                    className="px-4 py-1.5 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded-lg">
                                    {saving ? "جاري الحفظ…" : "حفظ الإعدادات"}
                                </button>
                            </div>
                        </section>

                        {/* Live preview */}
                        <section className="mb-6 grid grid-cols-1 lg:grid-cols-2 gap-4">
                            <div className="p-4 bg-sky-50 border-2 border-sky-200 rounded-xl">
                                <h3 className="text-sm font-extrabold text-sky-900 mb-2">
                                    🇺🇸 معاينة (حساب بالدولار)
                                </h3>
                                <label className="block mb-2">
                                    <span className="text-[10px] font-bold text-slate-600">المبلغ بالدولار</span>
                                    <input type="number" step="0.01" min="0"
                                        value={previewUsd}
                                        onChange={(e) => setPreviewUsd(e.target.value)}
                                        data-testid="preview-usd-input"
                                        className="mt-1 w-full px-3 py-2 border border-sky-300 rounded-lg num font-bold" />
                                </label>
                                <div className="text-xs space-y-1 mt-3">
                                    <div className="flex justify-between"><span>المبلغ بالريال</span>
                                        <strong className="num">{fmt(calcUsd.sar)}</strong></div>
                                    <div className="flex justify-between text-rose-700"><span>رسوم البنك ({fmt(settings.bank_commission_pct)}%)</span>
                                        <strong className="num">{fmt(calcUsd.fee)}</strong></div>
                                    <div className="flex justify-between border-t border-sky-300 pt-1 text-sm">
                                        <strong>إجمالي المديونية</strong>
                                        <strong className="num text-sky-900">{fmt(calcUsd.total)} ر.س</strong>
                                    </div>
                                </div>
                            </div>

                            <div className="p-4 bg-emerald-50 border-2 border-emerald-200 rounded-xl">
                                <h3 className="text-sm font-extrabold text-emerald-900 mb-2">
                                    🇸🇦 معاينة (حساب بالريال)
                                </h3>
                                <label className="block mb-2">
                                    <span className="text-[10px] font-bold text-slate-600">المبلغ بالريال</span>
                                    <input type="number" step="0.01" min="0"
                                        value={previewSar}
                                        onChange={(e) => setPreviewSar(e.target.value)}
                                        data-testid="preview-sar-input"
                                        className="mt-1 w-full px-3 py-2 border border-emerald-300 rounded-lg num font-bold" />
                                </label>
                                <div className="text-xs space-y-1 mt-3">
                                    <div className="flex justify-between"><span>المصروف الإعلاني</span>
                                        <strong className="num">{fmt(Number(previewSar) || 0)}</strong></div>
                                    <div className="flex justify-between text-rose-700"><span>رسوم البنك ({fmt(settings.bank_commission_pct)}%)</span>
                                        <strong className="num">{fmt(calcSar.fee)}</strong></div>
                                    <div className="flex justify-between border-t border-emerald-300 pt-1 text-sm">
                                        <strong>إجمالي المديونية</strong>
                                        <strong className="num text-emerald-900">{fmt(calcSar.total)} ر.س</strong>
                                    </div>
                                </div>
                            </div>
                        </section>

                        {/* Per-account config */}
                        <section className="p-4 bg-violet-50 border-2 border-violet-200 rounded-xl">
                            <h2 className="text-sm font-extrabold text-violet-900 mb-3">
                                إعداد كل حساب إعلاني
                            </h2>
                            {adAccounts.length === 0 ? (
                                <div className="text-center py-4 text-slate-500 text-xs">
                                    لا توجد حسابات إعلانية مُسجَّلة. أضِفها من صفحة «الأطراف».
                                </div>
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-xs">
                                        <thead className="bg-violet-100 text-violet-900">
                                            <tr>
                                                <th className="text-right p-2 font-extrabold">الحساب</th>
                                                <th className="text-right p-2 font-extrabold">المنصة</th>
                                                <th className="text-center p-2 font-extrabold">العملة</th>
                                                <th className="text-center p-2 font-extrabold">تطبيق العمولة البنكية</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {adAccounts.map((a) => (
                                                <tr key={a.id} className="border-t border-violet-200"
                                                    data-testid={`ad-account-row-${a.id}`}>
                                                    <td className="p-2 font-bold">{a.name}</td>
                                                    <td className="p-2 text-slate-600">{a.ad_provider || "—"}</td>
                                                    <td className="p-2 text-center">
                                                        <select value={a.currency || "SAR"}
                                                            onChange={(e) => saveAccountConfig(a.id, e.target.value, a.apply_bank_commission !== false)}
                                                            data-testid={`currency-select-${a.id}`}
                                                            className="px-2 py-1 border border-violet-300 rounded text-xs font-bold bg-white">
                                                            <option value="SAR">SAR (ريال)</option>
                                                            <option value="USD">USD (دولار)</option>
                                                        </select>
                                                    </td>
                                                    <td className="p-2 text-center">
                                                        <label className="inline-flex items-center gap-2 cursor-pointer">
                                                            <input type="checkbox"
                                                                checked={a.apply_bank_commission !== false}
                                                                onChange={(e) => saveAccountConfig(a.id, a.currency || "SAR", e.target.checked)}
                                                                data-testid={`apply-fee-checkbox-${a.id}`}
                                                                className="w-4 h-4 accent-violet-600" />
                                                            <span className="font-bold text-violet-900">
                                                                {a.apply_bank_commission !== false ? "نعم" : "لا"}
                                                            </span>
                                                        </label>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </section>

                        <p className="mt-4 text-[10px] text-slate-500 leading-relaxed">
                            💡 الإعدادات تُطبَّق على الفواتير الجديدة فقط. الفواتير القديمة تحتفظ بسعر الصرف والعمولة وقت إنشائها.
                            رسوم البنك تُسجَّل في حساب مصروف منفصل اسمه «رسوم بنكية وعمولات بطاقات».
                        </p>
                    </>
                )}
            </div>
        </div>
    );
}

/**
 * iter-46 — Quick "Daily total product cost" entry modal.
 *
 * Temporary tool: lets the merchant log a one-line aggregate cost for a
 * specific date (e.g., "Suppliers payment 5,000 SAR on 2026-06-03"). The
 * value goes into `daily_costs.product_costs` for that date and is
 * picked up by the dashboard's profit calculator until the proper
 * per-product cost tracking is fully populated.
 *
 * Backend (already exists, no changes needed):
 *   POST /api/daily-costs        upserts by date
 *   GET  /api/daily-costs        recent entries (sorted DESC by date)
 *   DELETE /api/daily-costs/{date}
 */
import { useCallback, useEffect, useState } from "react";
import {
    X, Calendar, Coins, CheckCircle, Trash, Warning, NotePencil,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const todayIso = () => new Date().toISOString().slice(0, 10);

const fmtMoney = (v) =>
    Number.isFinite(Number(v))
        ? Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : "0.00";

export default function DailyProductCostModal({ open, onClose, onSaved }) {
    const [date, setDate] = useState(todayIso());
    const [amount, setAmount] = useState("");
    const [notes, setNotes] = useState("");
    const [saving, setSaving] = useState(false);
    const [recent, setRecent] = useState([]);
    const [loadingRecent, setLoadingRecent] = useState(false);
    const [existingForDate, setExistingForDate] = useState(null);

    const loadRecent = useCallback(async () => {
        setLoadingRecent(true);
        try {
            const { data } = await api.get("/daily-costs");
            // Keep only entries with a non-zero product_costs value so the
            // history list focuses on what the merchant actually entered.
            const filtered = (Array.isArray(data) ? data : [])
                .filter((d) => Number(d.product_costs) > 0)
                .slice(0, 30);
            setRecent(filtered);
        } catch (err) {
            // non-fatal — list just stays empty
            console.warn("Failed to load recent daily costs", err);
        } finally {
            setLoadingRecent(false);
        }
    }, []);

    useEffect(() => {
        if (open) {
            setDate(todayIso());
            setAmount("");
            setNotes("");
            loadRecent();
        }
    }, [open, loadRecent]);

    // Whenever the merchant picks a different date, surface the existing
    // value for that day so they know they're EDITING vs ADDING.
    useEffect(() => {
        if (!open || !date) return;
        const hit = recent.find((r) => r.date === date);
        setExistingForDate(hit || null);
    }, [date, recent, open]);

    if (!open) return null;

    const submit = async () => {
        const amt = Number(String(amount).replace(/,/g, "").trim());
        if (!date) {
            toast.error("التاريخ مطلوب");
            return;
        }
        if (!Number.isFinite(amt) || amt <= 0) {
            toast.error("أدخل مبلغاً صحيحاً أكبر من صفر");
            return;
        }
        setSaving(true);
        try {
            // Preserve any other cost fields already saved for this date
            // (snapchat_ads, tiktok_ads, etc.) — otherwise the upsert
            // would zero them out.
            const prev = recent.find((r) => r.date === date) || {};
            await api.post("/daily-costs", {
                date,
                snapchat_ads: Number(prev.snapchat_ads) || 0,
                snapchat_ads_2: Number(prev.snapchat_ads_2) || 0,
                tiktok_ads: Number(prev.tiktok_ads) || 0,
                instagram_ads: Number(prev.instagram_ads) || 0,
                google_ads: Number(prev.google_ads) || 0,
                product_costs: amt,
                notes: notes.trim() || prev.notes || "",
            });
            toast.success(
                existingForDate
                    ? `تم تحديث تكلفة يوم ${date} إلى ${fmtMoney(amt)} ر.س`
                    : `تم حفظ ${fmtMoney(amt)} ر.س على يوم ${date}`,
            );
            await loadRecent();
            setAmount("");
            setNotes("");
            onSaved?.();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذّر الحفظ");
        } finally {
            setSaving(false);
        }
    };

    const removeEntry = async (entryDate) => {
        if (!window.confirm(`حذف تكلفة المنتجات ليوم ${entryDate}؟`)) return;
        try {
            // The DELETE /daily-costs/{date} removes the entire row (ads
            // included). To preserve ad spend we upsert with product_costs=0
            // when the date has other non-zero fields, otherwise we delete.
            const prev = recent.find((r) => r.date === entryDate) || {};
            const hasAds = (Number(prev.snapchat_ads) || 0) > 0
                || (Number(prev.snapchat_ads_2) || 0) > 0
                || (Number(prev.tiktok_ads) || 0) > 0
                || (Number(prev.instagram_ads) || 0) > 0
                || (Number(prev.google_ads) || 0) > 0;
            if (hasAds) {
                await api.post("/daily-costs", {
                    date: entryDate,
                    snapchat_ads: Number(prev.snapchat_ads) || 0,
                    snapchat_ads_2: Number(prev.snapchat_ads_2) || 0,
                    tiktok_ads: Number(prev.tiktok_ads) || 0,
                    instagram_ads: Number(prev.instagram_ads) || 0,
                    google_ads: Number(prev.google_ads) || 0,
                    product_costs: 0,
                    notes: prev.notes || "",
                });
            } else {
                await api.delete(`/daily-costs/${entryDate}`);
            }
            toast.success(`تم حذف تكلفة يوم ${entryDate}`);
            await loadRecent();
            onSaved?.();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذّر الحذف");
        }
    };

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-6 overflow-y-auto"
            data-testid="daily-product-cost-modal"
            onClick={onClose}
        >
            <div
                className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-5 sm:p-6 max-h-[90vh] overflow-y-auto"
                style={{ fontFamily: "Tajawal" }}
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div className="flex items-start justify-between gap-3 mb-4 pb-3 border-b border-slate-200">
                    <div className="flex items-start gap-2 min-w-0">
                        <div className="w-10 h-10 rounded-lg bg-amber-100 text-amber-700 flex items-center justify-center flex-shrink-0">
                            <Coins size={22} weight="duotone" />
                        </div>
                        <div className="min-w-0">
                            <h3 className="font-extrabold text-base sm:text-lg text-slate-900">
                                إضافة إجمالي تكلفة المنتجات حسب التاريخ
                            </h3>
                            <p className="text-[11px] text-slate-500 mt-0.5">
                                يُضاف المبلغ إلى تكلفة المنتجات الخاصة باليوم المحدد
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="text-slate-400 hover:text-slate-700 p-1 flex-shrink-0"
                        data-testid="daily-product-cost-close-btn"
                        aria-label="إغلاق"
                    >
                        <X size={20} weight="bold" />
                    </button>
                </div>

                {/* Temporary-workaround banner */}
                <div className="mb-4 rounded-lg bg-amber-50 border border-amber-200 p-3 text-xs text-amber-900 flex items-start gap-2">
                    <Warning size={16} weight="bold" className="flex-shrink-0 mt-0.5" />
                    <div className="leading-relaxed">
                        <b>حل مؤقت:</b> هذه الميزة لإدخال إجمالي تكلفة المنتجات للأيام التي لم تُسجَّل
                        فيها تكاليف على مستوى المنتج. عند توفر تكلفة لكل SKU في الكاتالوج،
                        ستحلّ تلك القيم تلقائياً مكان هذا الإجمالي ضمن حساب صافي الربح.
                    </div>
                </div>

                {/* Form */}
                <div className="space-y-3">
                    <div>
                        <label className="block text-[11px] font-bold text-slate-600 mb-1 flex items-center gap-1">
                            <Calendar size={12} weight="bold" />
                            التاريخ
                        </label>
                        <input
                            type="date"
                            value={date}
                            onChange={e => setDate(e.target.value)}
                            max={todayIso()}
                            className="w-full px-3 py-2 rounded-lg border border-slate-300 focus:border-amber-500 focus:ring-2 focus:ring-amber-100 text-sm outline-none"
                            data-testid="daily-product-cost-date"
                        />
                        {existingForDate && (
                            <div className="mt-1.5 text-[11px] text-indigo-700 bg-indigo-50 border border-indigo-200 rounded px-2 py-1">
                                يوجد إدخال سابق لهذا التاريخ بقيمة <b>{fmtMoney(existingForDate.product_costs)} ر.س</b>.
                                الحفظ سيستبدل القيمة القديمة.
                            </div>
                        )}
                    </div>

                    <div>
                        <label className="block text-[11px] font-bold text-slate-600 mb-1 flex items-center gap-1">
                            <Coins size={12} weight="bold" />
                            إجمالي تكلفة المنتجات لهذا اليوم (ر.س)
                        </label>
                        <input
                            type="number"
                            step="0.01"
                            min="0"
                            value={amount}
                            onChange={e => setAmount(e.target.value)}
                            placeholder="مثال: 1250.50"
                            className="w-full px-3 py-2 rounded-lg border border-slate-300 focus:border-amber-500 focus:ring-2 focus:ring-amber-100 text-sm outline-none num"
                            data-testid="daily-product-cost-amount"
                        />
                    </div>

                    <div>
                        <label className="block text-[11px] font-bold text-slate-600 mb-1 flex items-center gap-1">
                            <NotePencil size={12} weight="bold" />
                            ملاحظة (اختياري)
                        </label>
                        <input
                            type="text"
                            value={notes}
                            onChange={e => setNotes(e.target.value)}
                            placeholder="مثلاً: دفعة موردين، شحنة استيراد، إلخ"
                            maxLength={200}
                            className="w-full px-3 py-2 rounded-lg border border-slate-300 focus:border-amber-500 focus:ring-2 focus:ring-amber-100 text-sm outline-none"
                            data-testid="daily-product-cost-notes"
                        />
                    </div>
                </div>

                {/* Recent entries */}
                <div className="mt-5 pt-4 border-t border-slate-200">
                    <div className="flex items-center justify-between mb-2">
                        <h4 className="text-xs font-bold text-slate-700">آخر الإدخالات</h4>
                        <span className="text-[10px] text-slate-500">
                            {loadingRecent ? "جارٍ التحميل…" : `${recent.length} إدخال`}
                        </span>
                    </div>
                    {recent.length === 0 ? (
                        <div className="text-[11px] text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-3 text-center">
                            لا توجد إدخالات سابقة
                        </div>
                    ) : (
                        <div className="rounded-lg border border-slate-200 overflow-x-auto max-h-56 overflow-y-auto">
                            <table className="w-full text-xs">
                                <thead className="bg-slate-50 sticky top-0">
                                    <tr>
                                        <th className="text-start px-3 py-1.5 font-bold text-slate-700">التاريخ</th>
                                        <th className="text-end px-3 py-1.5 font-bold text-slate-700">المبلغ</th>
                                        <th className="text-start px-3 py-1.5 font-bold text-slate-700">ملاحظة</th>
                                        <th className="px-2 py-1.5"></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {recent.map((r) => (
                                        <tr
                                            key={r.date}
                                            className="border-t border-slate-100 hover:bg-amber-50/40"
                                            data-testid={`daily-cost-row-${r.date}`}
                                        >
                                            <td className="px-3 py-1.5 num font-bold text-slate-700">{r.date}</td>
                                            <td className="px-3 py-1.5 text-end num text-amber-700 font-bold">
                                                {fmtMoney(r.product_costs)}
                                            </td>
                                            <td className="px-3 py-1.5 text-slate-600 truncate max-w-[10rem]" title={r.notes || ""}>
                                                {r.notes || "—"}
                                            </td>
                                            <td className="px-2 py-1.5 text-end">
                                                <button
                                                    type="button"
                                                    onClick={() => removeEntry(r.date)}
                                                    className="p-1 rounded hover:bg-red-50 text-slate-400 hover:text-red-600"
                                                    data-testid={`daily-cost-delete-${r.date}`}
                                                    title="حذف"
                                                >
                                                    <Trash size={13} weight="bold" />
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>

                {/* Actions */}
                <div className="flex flex-wrap gap-2 justify-end mt-5 pt-4 border-t border-slate-200">
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={saving}
                        className="px-4 py-2 rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200 font-bold text-sm disabled:opacity-50"
                        data-testid="daily-product-cost-cancel-btn"
                    >
                        إغلاق
                    </button>
                    <button
                        type="button"
                        onClick={submit}
                        disabled={saving}
                        className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-amber-600 text-white hover:bg-amber-700 font-bold text-sm disabled:opacity-50"
                        data-testid="daily-product-cost-save-btn"
                    >
                        <CheckCircle size={14} weight="bold" />
                        {saving ? "جارٍ الحفظ…" : (existingForDate ? "تحديث" : "حفظ")}
                    </button>
                </div>
            </div>
        </div>
    );
}

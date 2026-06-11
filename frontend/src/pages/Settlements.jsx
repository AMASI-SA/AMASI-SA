import { useEffect, useMemo, useState } from "react";
import {
    Plus, Trash, PencilSimple, ArrowsClockwise, X, Receipt as ReceiptIcon,
    Clock, Warning,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA } from "../lib/dates";

const PROVIDER_TONES = {
    salla:         { label: "سلة",                cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
    tamara:        { label: "تمارا",              cls: "bg-pink-100 text-pink-800 border-pink-200" },
    tabby:         { label: "تابي",               cls: "bg-violet-100 text-violet-800 border-violet-200" },
    emkan:         { label: "إمكان",              cls: "bg-amber-100 text-amber-800 border-amber-200" },
    bank_transfer: { label: "تحويل بنكي",         cls: "bg-sky-100 text-sky-800 border-sky-200" },
    cod:           { label: "الدفع عند الاستلام",  cls: "bg-orange-100 text-orange-800 border-orange-200" },
    other:         { label: "أخرى",               cls: "bg-slate-100 text-slate-700 border-slate-200" },
};

const TYPE_LABELS = {
    partial_refund:    "استرجاع جزئي",
    full_refund:       "استرجاع كلي",
    item_removed:      "حذف منتج",
    order_cancelled:   "إلغاء الطلب",
    manual_adjustment: "تسوية يدوية",
};

const inputCls =
    "w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

const fmt = (v) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// ── Add/Edit modal ───────────────────────────────────────────────────────────
function SettlementModal({ initial, onClose, onSaved }) {
    const isEdit = !!initial;
    const today = todaySA();
    const [form, setForm] = useState(() => ({
        order_number:     initial?.order_number || "",
        payment_method:   initial?.payment_method || "مدفوعات سلة",
        original_amount:  initial?.original_amount ?? "",
        new_amount:       initial?.new_amount ?? "",
        adjustment_type:  initial?.adjustment_type || "partial_refund",
        order_created_at: initial?.order_created_at || today,
        adjusted_at:      initial?.adjusted_at || today,
        reason:           initial?.reason || "",
    }));
    const [busy, setBusy] = useState(false);

    const adjAmount = useMemo(() => {
        const o = parseFloat(form.original_amount);
        const n = parseFloat(form.new_amount);
        if (Number.isNaN(o) || Number.isNaN(n)) return null;
        return Math.round((o - n) * 100) / 100;
    }, [form.original_amount, form.new_amount]);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async (e) => {
        e.preventDefault();
        if (!form.order_number.trim()) return toast.error("رقم الطلب مطلوب");
        if (!form.payment_method.trim()) return toast.error("طريقة الدفع مطلوبة");
        if (adjAmount == null || adjAmount <= 0) {
            return toast.error("المبلغ الجديد يجب أن يكون أقل من المبلغ الأصلي");
        }
        setBusy(true);
        try {
            if (isEdit) {
                const { data } = await api.put(`/settlements/${initial.id}`, {
                    new_amount:      parseFloat(form.new_amount),
                    adjustment_type: form.adjustment_type,
                    adjusted_at:     form.adjusted_at,
                    reason:          form.reason,
                });
                toast.success("تم تحديث التسوية");
                onSaved(data);
            } else {
                const { data } = await api.post("/settlements", {
                    order_number:     form.order_number.trim(),
                    payment_method:   form.payment_method.trim(),
                    original_amount:  parseFloat(form.original_amount),
                    new_amount:       parseFloat(form.new_amount),
                    adjustment_type:  form.adjustment_type,
                    order_created_at: form.order_created_at,
                    adjusted_at:      form.adjusted_at,
                    reason:           form.reason,
                });
                toast.success(`تم تسجيل تسوية ${fmt(adjAmount)} ر.س`);
                onSaved(data);
            }
            onClose();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="settlement-modal">
            <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
                <header className="flex items-center justify-between border-b border-border px-5 py-3">
                    <h2 className="font-bold text-lg">{isEdit ? "تعديل تسوية" : "تسجيل تسوية جديدة"}</h2>
                    <button onClick={onClose} className="p-1.5 rounded hover:bg-accent" data-testid="settlement-close-btn">
                        <X size={20} />
                    </button>
                </header>
                <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">رقم الطلب</label>
                            <input
                                value={form.order_number}
                                onChange={(e) => set("order_number", e.target.value)}
                                className={inputCls}
                                disabled={isEdit}
                                data-testid="settlement-order-number-input"
                                placeholder="123456"
                                dir="ltr"
                                style={{ textAlign: "right" }}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">طريقة الدفع (من الطلب)</label>
                            <input
                                value={form.payment_method}
                                onChange={(e) => set("payment_method", e.target.value)}
                                className={inputCls}
                                disabled={isEdit}
                                data-testid="settlement-payment-method-input"
                                placeholder="سلة / تمارا / تابي ..."
                            />
                            <p className="text-[11px] text-muted-foreground mt-1">سيتم تصنيف المزوّد تلقائياً.</p>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">المبلغ الأصلي</label>
                            <input
                                type="number" step="0.01" min="0.01"
                                value={form.original_amount}
                                onChange={(e) => set("original_amount", e.target.value)}
                                className={inputCls}
                                disabled={isEdit}
                                data-testid="settlement-original-amount-input"
                                dir="ltr" style={{ textAlign: "right" }}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">المبلغ بعد التعديل</label>
                            <input
                                type="number" step="0.01" min="0"
                                value={form.new_amount}
                                onChange={(e) => set("new_amount", e.target.value)}
                                className={inputCls}
                                data-testid="settlement-new-amount-input"
                                dir="ltr" style={{ textAlign: "right" }}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">نوع التعديل</label>
                            <select
                                value={form.adjustment_type}
                                onChange={(e) => set("adjustment_type", e.target.value)}
                                className={inputCls}
                                data-testid="settlement-type-select"
                            >
                                {Object.entries(TYPE_LABELS).map(([k, v]) => (
                                    <option key={k} value={k}>{v}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">تاريخ آخر تعديل (يُحسب فيه الخصم)</label>
                            <input
                                type="date" value={form.adjusted_at}
                                onChange={(e) => set("adjusted_at", e.target.value)}
                                className={inputCls}
                                data-testid="settlement-adjusted-at-input"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">تاريخ إنشاء الطلب</label>
                            <input
                                type="date" value={form.order_created_at}
                                onChange={(e) => set("order_created_at", e.target.value)}
                                className={inputCls}
                                disabled={isEdit}
                                data-testid="settlement-order-date-input"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">السبب / ملاحظات</label>
                            <input
                                value={form.reason}
                                onChange={(e) => set("reason", e.target.value)}
                                className={inputCls}
                                placeholder="مثال: استرجاع منتج لم يصل بحالة جيدة"
                                maxLength={500}
                                data-testid="settlement-reason-input"
                            />
                        </div>
                    </div>

                    {adjAmount != null && adjAmount > 0 && (
                        <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm" data-testid="settlement-adj-preview">
                            <span className="font-semibold text-emerald-900">سيُسجّل خصم بقيمة:</span>{" "}
                            <span className="num font-extrabold text-emerald-700 text-lg">{fmt(adjAmount)} ر.س</span>
                        </div>
                    )}
                </form>
                <footer className="border-t border-border px-5 py-3 flex justify-end gap-2">
                    <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-accent" data-testid="settlement-cancel-btn">إلغاء</button>
                    <button onClick={submit} disabled={busy} className="px-4 py-2 text-sm bg-brand text-white font-semibold rounded-lg bg-brand-hover disabled:opacity-60" data-testid="settlement-save-btn">
                        {busy ? "جاري الحفظ…" : (isEdit ? "حفظ التعديلات" : "تسجيل التسوية")}
                    </button>
                </footer>
            </div>
        </div>
    );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function Settlements() {
    const today = todaySA();
    const firstDayOfMonth = new Date();
    firstDayOfMonth.setDate(1);
    const fromDefault = firstDayOfMonth.toISOString().slice(0, 10);

    const [fromDate, setFromDate] = useState(fromDefault);
    const [toDate, setToDate]     = useState(today);
    const [providerFilter, setProviderFilter] = useState("");
    const [windowFilter, setWindowFilter]     = useState("");
    const [items, setItems]   = useState([]);
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [modal, setModal]   = useState(null); // {initial}

    const load = async () => {
        setLoading(true);
        try {
            const params = { from_date: fromDate, to_date: toDate };
            if (providerFilter) params.provider = providerFilter;
            if (windowFilter) params.window = windowFilter;
            const [listRes, sumRes] = await Promise.all([
                api.get("/settlements", { params }),
                api.get("/settlements/summary", { params: { from_date: fromDate, to_date: toDate } }),
            ]);
            setItems(listRes.data);
            setSummary(sumRes.data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fromDate, toDate, providerFilter, windowFilter]);

    const onDelete = async (s) => {
        if (!window.confirm(`حذف تسوية ${s.order_number} (${fmt(s.adjustment_amount)} ر.س)؟`)) return;
        try {
            await api.delete(`/settlements/${s.id}`);
            toast.success("تم الحذف");
            setItems((arr) => arr.filter((x) => x.id !== s.id));
            load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    return (
        <div className="space-y-6" data-testid="settlements-page">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                        تسويات المدفوعات
                    </h1>
                    <p className="text-muted-foreground">سجل الاسترجاعات الجزئية والكلية وتعديلات الطلبات لكل طرق الدفع.</p>
                </div>
                <button
                    onClick={() => setModal({})}
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover"
                    data-testid="settlements-add-btn"
                >
                    <Plus size={18} weight="bold" /> تسجيل تسوية
                </button>
            </header>

            {/* Filters */}
            <div className="bg-white rounded-xl border border-border p-4 grid grid-cols-1 md:grid-cols-5 gap-3">
                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1">من تاريخ</label>
                    <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className={inputCls} data-testid="settlements-from-date" />
                </div>
                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1">إلى تاريخ</label>
                    <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className={inputCls} data-testid="settlements-to-date" />
                </div>
                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1">طريقة الدفع</label>
                    <select value={providerFilter} onChange={(e) => setProviderFilter(e.target.value)} className={inputCls} data-testid="settlements-provider-filter">
                        <option value="">كل المزوّدين</option>
                        {Object.entries(PROVIDER_TONES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="block text-xs font-bold text-muted-foreground mb-1">نافذة سلة 14 يوم</label>
                    <select value={windowFilter} onChange={(e) => setWindowFilter(e.target.value)} className={inputCls} data-testid="settlements-window-filter">
                        <option value="">الكل</option>
                        <option value="inside_14d">داخل 14 يوم</option>
                        <option value="outside_14d">خارج 14 يوم</option>
                    </select>
                </div>
                <div className="flex items-end">
                    <button onClick={load} className="w-full inline-flex items-center justify-center gap-1.5 px-4 py-2 bg-slate-800 text-white text-sm font-semibold rounded-lg hover:bg-slate-700" data-testid="settlements-refresh-btn">
                        <ArrowsClockwise size={16} /> تحديث
                    </button>
                </div>
            </div>

            {/* Summary cards per provider */}
            {summary && (
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
                    {Object.entries(summary.by_provider).map(([key, val]) => {
                        const tone = PROVIDER_TONES[key];
                        const active = val.count > 0;
                        return (
                            <div
                                key={key}
                                className={`rounded-lg border p-3 ${active ? "bg-white" : "bg-slate-50"} ${tone.cls.replace(/bg-\S+/g, "").replace(/text-\S+/g, "")}`}
                                data-testid={`settlement-summary-${key}`}
                            >
                                <div className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold border ${tone.cls}`}>
                                    {tone.label}
                                </div>
                                <div className="mt-1.5 num text-base font-extrabold text-rose-700" style={{ fontFamily: "Tajawal" }}>
                                    −{fmt(val.total_adjustment)}
                                </div>
                                <div className="text-[10px] text-muted-foreground">{val.count} تسوية</div>
                            </div>
                        );
                    })}
                </div>
            )}

            {summary && summary.grand_total_adjustment > 0 && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 flex items-center gap-3" data-testid="settlements-grand-total">
                    <ReceiptIcon size={22} className="text-rose-700" weight="duotone" />
                    <div className="flex-1">
                        <div className="text-sm font-bold text-rose-900">إجمالي تسويات الفترة</div>
                        <div className="text-[11px] text-rose-700">ستُخصم تلقائياً من صافي مبيعات كل مزوّد في لوحة التحكم</div>
                    </div>
                    <div className="num text-2xl font-extrabold text-rose-700" style={{ fontFamily: "Tajawal" }}>
                        −{fmt(summary.grand_total_adjustment)} ر.س
                    </div>
                </div>
            )}

            {/* Table */}
            <div className="bg-white rounded-xl border border-border overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="mezan-table w-full text-sm">
                        <thead className="bg-slate-50 text-xs text-muted-foreground">
                            <tr>
                                <th className="text-right px-3 py-2.5 font-bold">رقم الطلب</th>
                                <th className="text-right px-3 py-2.5 font-bold">تاريخ الطلب</th>
                                <th className="text-right px-3 py-2.5 font-bold">تاريخ التعديل</th>
                                <th className="text-right px-3 py-2.5 font-bold">المزوّد</th>
                                <th className="text-right px-3 py-2.5 font-bold">نافذة 14 يوم</th>
                                <th className="text-right px-3 py-2.5 font-bold">الأصلي</th>
                                <th className="text-right px-3 py-2.5 font-bold">الجديد</th>
                                <th className="text-right px-3 py-2.5 font-bold">الخصم</th>
                                <th className="text-right px-3 py-2.5 font-bold">النوع</th>
                                <th className="text-right px-3 py-2.5 font-bold">السبب</th>
                                <th className="text-right px-3 py-2.5 font-bold">إجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loading && (
                                <tr><td colSpan={11} className="text-center py-8 text-muted-foreground">جاري التحميل…</td></tr>
                            )}
                            {!loading && items.length === 0 && (
                                <tr><td colSpan={11} className="text-center py-10 text-muted-foreground" data-testid="settlements-empty">
                                    لا توجد تسويات في هذه الفترة. اضغط "تسجيل تسوية" لإضافة استرجاع جزئي أو حذف منتج.
                                </td></tr>
                            )}
                            {!loading && items.map((s) => {
                                const tone = PROVIDER_TONES[s.provider] || PROVIDER_TONES.other;
                                return (
                                    <tr key={s.id} className="border-t border-border hover:bg-slate-50/50" data-testid={`settlement-row-${s.id}`}>
                                        <td className="px-3 py-2.5 font-semibold" dir="ltr" style={{ textAlign: "right" }}>{s.order_number}</td>
                                        <td className="px-3 py-2.5 text-muted-foreground text-xs">{s.order_created_at}</td>
                                        <td className="px-3 py-2.5 text-muted-foreground text-xs">{s.adjusted_at}</td>
                                        <td className="px-3 py-2.5">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border ${tone.cls}`}>
                                                {tone.label}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            {s.window === "inside_14d" && (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-200">
                                                    <Clock size={11} /> داخل 14 يوم
                                                </span>
                                            )}
                                            {s.window === "outside_14d" && (
                                                <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-600 border border-slate-200">
                                                    خارج 14 يوم
                                                </span>
                                            )}
                                            {!s.window && <span className="text-[10px] text-muted-foreground">—</span>}
                                        </td>
                                        <td className="px-3 py-2.5 num">{fmt(s.original_amount)}</td>
                                        <td className="px-3 py-2.5 num text-sky-700">{fmt(s.new_amount)}</td>
                                        <td className="px-3 py-2.5 num font-extrabold text-rose-700">−{fmt(s.adjustment_amount)}</td>
                                        <td className="px-3 py-2.5 text-xs">{TYPE_LABELS[s.adjustment_type] || s.adjustment_type}</td>
                                        <td className="px-3 py-2.5 text-xs text-muted-foreground max-w-[18ch] truncate" title={s.reason}>{s.reason || "—"}</td>
                                        <td className="px-3 py-2.5">
                                            <div className="flex items-center gap-1">
                                                <button onClick={() => setModal({ initial: s })} className="p-1 rounded text-brand hover:bg-brand/10" data-testid={`settlement-edit-${s.id}`}>
                                                    <PencilSimple size={16} />
                                                </button>
                                                <button onClick={() => onDelete(s)} className="p-1 rounded text-rose-600 hover:bg-rose-50" data-testid={`settlement-delete-${s.id}`}>
                                                    <Trash size={16} />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="bg-amber-50/60 border border-amber-200 rounded-lg p-3 text-xs text-amber-900 flex items-start gap-2" data-testid="settlements-info">
                <Warning size={18} className="text-amber-700 shrink-0 mt-0.5" weight="duotone" />
                <div>
                    التسوية تُحسب في <strong>تاريخ التعديل</strong> (وليس تاريخ إنشاء الطلب) — كما تفعل سلة عند خصم المبلغ من محفظتك.
                    لمدفوعات سلة فقط، إذا كان عمر الطلب ≤ 14 يوم فهو ضمن محفظة سلة الحالية (غير المفوّترة)؛ غير ذلك فهو مفوتر سابقاً.
                </div>
            </div>

            {modal && (
                <SettlementModal
                    initial={modal.initial}
                    onClose={() => setModal(null)}
                    onSaved={() => load()}
                />
            )}
        </div>
    );
}

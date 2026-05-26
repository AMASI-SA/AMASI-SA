import { useEffect, useState } from "react";
import { Plus, Trash, FloppyDisk } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

export default function Settings() {
    const [payments, setPayments] = useState([]);
    const [shippings, setShippings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/settings");
                setPayments(data.payment_methods || []);
                setShippings(data.shipping_companies || []);
            } finally { setLoading(false); }
        })();
    }, []);

    const save = async () => {
        setSaving(true);
        try {
            await api.put("/settings", {
                payment_methods: payments.map((p) => ({
                    name: (p.name || "").trim(),
                    commission_percent: Number(p.commission_percent || 0),
                    fixed_fee: Number(p.fixed_fee || 0),
                    vat_percent: Number(p.vat_percent || 0),
                })).filter((p) => p.name),
                shipping_companies: shippings.map((s) => ({
                    name: (s.name || "").trim(),
                    cost_per_order: Number(s.cost_per_order || 0),
                    vat_percent: Number(s.vat_percent || 0),
                })).filter((s) => s.name),
            });
            toast.success("تم حفظ الإعدادات");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSaving(false); }
    };

    if (loading) return <div className="p-10 text-center" data-testid="settings-loading">جاري التحميل…</div>;

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="settings-page">
            <div className="flex items-start justify-between gap-4 flex-col md:flex-row">
                <div>
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>الإعدادات</h1>
                    <p className="text-muted-foreground mt-2 text-base">
                        اضبط نسب عمولات بوابات الدفع وتكاليف شركات الشحن. ستُستخدم تلقائياً في حساب الأرباح.
                    </p>
                </div>
                <button
                    onClick={save}
                    disabled={saving}
                    className="inline-flex items-center gap-2 px-5 py-3 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                    data-testid="save-settings-btn"
                >
                    <FloppyDisk size={20} weight="bold" />
                    {saving ? "جاري الحفظ…" : "حفظ التغييرات"}
                </button>
            </div>

            {/* Payment methods */}
            <div className="rounded-xl border border-border bg-white p-6">
                <div className="flex items-center justify-between mb-5">
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>طرق الدفع وعمولاتها</h2>
                        <p className="text-sm text-muted-foreground mt-1">نسبة % + مبلغ ثابت لكل طلب + نسبة ضريبة على إجمالي العمولة</p>
                    </div>
                    <button
                        onClick={() => setPayments([...payments, { name: "", commission_percent: 0, fixed_fee: 0, vat_percent: 15 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-payment-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-3" data-testid="payment-methods-list">
                    {/* Header row */}
                    <div className="grid grid-cols-12 gap-3 text-xs font-semibold text-muted-foreground px-1 hidden md:grid">
                        <div className="col-span-4">اسم بوابة الدفع</div>
                        <div className="col-span-2 text-center">النسبة %</div>
                        <div className="col-span-2 text-center">مبلغ ثابت (ر.س)</div>
                        <div className="col-span-3 text-center">نسبة الضريبة على العمولة %</div>
                        <div className="col-span-1"></div>
                    </div>
                    {payments.map((p, i) => (
                        <div key={i} className="grid grid-cols-12 gap-3 items-center">
                            <input
                                type="text"
                                value={p.name}
                                onChange={(e) => {
                                    const arr = [...payments];
                                    arr[i] = { ...arr[i], name: e.target.value };
                                    setPayments(arr);
                                }}
                                placeholder="اسم بوابة الدفع"
                                className="col-span-4 px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid={`payment-name-${i}`}
                            />
                            <div className="col-span-2 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={p.commission_percent ?? 0}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], commission_percent: e.target.value };
                                        setPayments(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    data-testid={`payment-rate-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border">%</span>
                            </div>
                            <div className="col-span-2 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} step="0.01"
                                    value={p.fixed_fee ?? 0}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], fixed_fee: e.target.value };
                                        setPayments(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    placeholder="0.00"
                                    data-testid={`payment-fixed-${i}`}
                                />
                                <span className="px-3 py-2.5 text-xs font-bold text-muted-foreground bg-accent/60 border-s border-border whitespace-nowrap">ر.س</span>
                            </div>
                            <div className="col-span-3 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={p.vat_percent ?? 0}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], vat_percent: e.target.value };
                                        setPayments(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    placeholder="15"
                                    data-testid={`payment-vat-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border">%</span>
                            </div>
                            <button
                                onClick={() => setPayments(payments.filter((_, idx) => idx !== i))}
                                className="col-span-1 p-2.5 rounded-lg border border-border hover:bg-red-50 hover:text-red-600 transition-colors"
                                title="حذف"
                                data-testid={`remove-payment-${i}`}
                            >
                                <Trash size={18} />
                            </button>
                        </div>
                    ))}
                </div>
            </div>

            {/* Shipping companies */}
            <div className="rounded-xl border border-border bg-white p-6">
                <div className="flex items-center justify-between mb-5">
                    <div>
                        <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>شركات الشحن وتكاليفها</h2>
                        <p className="text-sm text-muted-foreground mt-1">تكلفة الشحنة (ر.س) + نسبة الضريبة على الشحن</p>
                    </div>
                    <button
                        onClick={() => setShippings([...shippings, { name: "", cost_per_order: 0, vat_percent: 15 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-shipping-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-3" data-testid="shipping-companies-list">
                    {/* Header row */}
                    <div className="grid grid-cols-12 gap-3 text-xs font-semibold text-muted-foreground px-1 hidden md:grid">
                        <div className="col-span-5">اسم شركة الشحن</div>
                        <div className="col-span-3 text-center">تكلفة الشحنة (ر.س)</div>
                        <div className="col-span-3 text-center">نسبة الضريبة على الشحن %</div>
                        <div className="col-span-1"></div>
                    </div>
                    {shippings.map((s, i) => (
                        <div key={i} className="grid grid-cols-12 gap-3 items-center">
                            <input
                                type="text"
                                value={s.name}
                                onChange={(e) => {
                                    const arr = [...shippings];
                                    arr[i] = { ...arr[i], name: e.target.value };
                                    setShippings(arr);
                                }}
                                placeholder="اسم شركة الشحن"
                                className="col-span-5 px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid={`shipping-name-${i}`}
                            />
                            <div className="col-span-3 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} step="0.01"
                                    value={s.cost_per_order}
                                    onChange={(e) => {
                                        const arr = [...shippings];
                                        arr[i] = { ...arr[i], cost_per_order: e.target.value };
                                        setShippings(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    data-testid={`shipping-cost-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border whitespace-nowrap">ر.س</span>
                            </div>
                            <div className="col-span-3 flex items-center border border-border rounded-lg bg-white focus-within:ring-2 focus-within:ring-brand overflow-hidden">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={s.vat_percent ?? 0}
                                    onChange={(e) => {
                                        const arr = [...shippings];
                                        arr[i] = { ...arr[i], vat_percent: e.target.value };
                                        setShippings(arr);
                                    }}
                                    dir="ltr"
                                    className="flex-1 min-w-0 px-3 py-2.5 text-base bg-transparent focus:outline-none num text-right"
                                    placeholder="15"
                                    data-testid={`shipping-vat-${i}`}
                                />
                                <span className="px-3 py-2.5 text-sm font-bold text-muted-foreground bg-accent/60 border-s border-border">%</span>
                            </div>
                            <button
                                onClick={() => setShippings(shippings.filter((_, idx) => idx !== i))}
                                className="col-span-1 p-2.5 rounded-lg border border-border hover:bg-red-50 hover:text-red-600 transition-colors"
                                title="حذف"
                                data-testid={`remove-shipping-${i}`}
                            >
                                <Trash size={18} />
                            </button>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

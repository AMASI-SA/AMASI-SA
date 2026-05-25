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
                })).filter((p) => p.name),
                shipping_companies: shippings.map((s) => ({
                    name: (s.name || "").trim(),
                    cost_per_order: Number(s.cost_per_order || 0),
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
                        <p className="text-sm text-muted-foreground mt-1">حدّد نسبة العمولة (%) لكل طريقة دفع</p>
                    </div>
                    <button
                        onClick={() => setPayments([...payments, { name: "", commission_percent: 0 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-payment-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-2" data-testid="payment-methods-list">
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
                                className="col-span-7 px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid={`payment-name-${i}`}
                            />
                            <div className="col-span-4 relative">
                                <input
                                    type="number"
                                    min={0} max={100} step="0.01"
                                    value={p.commission_percent}
                                    onChange={(e) => {
                                        const arr = [...payments];
                                        arr[i] = { ...arr[i], commission_percent: e.target.value };
                                        setPayments(arr);
                                    }}
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand num pe-9"
                                    data-testid={`payment-rate-${i}`}
                                />
                                <span className="absolute top-2.5 left-3 text-muted-foreground text-sm font-bold">%</span>
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
                        <p className="text-sm text-muted-foreground mt-1">تكلفة الشحنة الواحدة (ر.س) لكل شركة</p>
                    </div>
                    <button
                        onClick={() => setShippings([...shippings, { name: "", cost_per_order: 0 }])}
                        className="inline-flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-semibold hover:bg-accent transition-colors"
                        data-testid="add-shipping-btn"
                    >
                        <Plus size={16} weight="bold" /> إضافة
                    </button>
                </div>
                <div className="space-y-2" data-testid="shipping-companies-list">
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
                                className="col-span-7 px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid={`shipping-name-${i}`}
                            />
                            <div className="col-span-4 relative">
                                <input
                                    type="number"
                                    min={0} step="0.01"
                                    value={s.cost_per_order}
                                    onChange={(e) => {
                                        const arr = [...shippings];
                                        arr[i] = { ...arr[i], cost_per_order: e.target.value };
                                        setShippings(arr);
                                    }}
                                    className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand num pe-12"
                                    data-testid={`shipping-cost-${i}`}
                                />
                                <span className="absolute top-2.5 left-3 text-muted-foreground text-sm font-bold">ر.س</span>
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

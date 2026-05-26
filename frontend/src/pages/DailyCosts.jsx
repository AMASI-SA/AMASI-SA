import { useEffect, useState } from "react";
import { Trash, FloppyDisk, Receipt } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { formatMoney, todayISO } from "../lib/format";

export default function DailyCosts() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);

    // form
    const [date, setDate] = useState(todayISO());
    const [snap, setSnap] = useState("");
    const [snap2, setSnap2] = useState("");
    const [tik, setTik] = useState("");
    const [insta, setInsta] = useState("");
    const [google, setGoogle] = useState("");
    const [prod, setProd] = useState("");
    const [notes, setNotes] = useState("");
    const [saving, setSaving] = useState(false);

    const load = async () => {
        try {
            const { data } = await api.get("/daily-costs");
            setItems(data || []);
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const submit = async (e) => {
        e.preventDefault();
        setSaving(true);
        try {
            await api.post("/daily-costs", {
                date,
                snapchat_ads: Number(snap || 0),
                snapchat_ads_2: Number(snap2 || 0),
                tiktok_ads: Number(tik || 0),
                instagram_ads: Number(insta || 0),
                google_ads: Number(google || 0),
                product_costs: Number(prod || 0),
                notes,
            });
            toast.success("تم حفظ التكاليف اليومية");
            setSnap(""); setSnap2(""); setTik(""); setInsta(""); setGoogle(""); setProd(""); setNotes("");
            await load();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setSaving(false); }
    };

    const remove = async (d) => {
        if (!window.confirm("حذف هذا اليوم؟")) return;
        await api.delete(`/daily-costs/${d}`);
        await load();
        toast.success("تم الحذف");
    };

    const total = (it) =>
        Number(it.snapchat_ads || 0)
        + Number(it.snapchat_ads_2 || 0)
        + Number(it.tiktok_ads || 0)
        + Number(it.instagram_ads || 0)
        + Number(it.google_ads || 0)
        + Number(it.product_costs || 0);

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="daily-costs-page">
            <div>
                <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>التكاليف اليومية</h1>
                <p className="text-muted-foreground mt-2 text-base">سجّل مصروفاتك اليومية على منصات الإعلانات والمنتجات.</p>
            </div>

            <form onSubmit={submit} className="rounded-xl border border-border bg-white p-6" data-testid="daily-costs-form">
                <h2 className="text-xl font-bold mb-5" style={{ fontFamily: "Tajawal" }}>إضافة / تحديث تكاليف يوم</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <div>
                        <label className="block text-sm font-semibold mb-1.5">التاريخ</label>
                        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required
                            className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="daily-date-input" dir="ltr" style={{ textAlign: "right" }} />
                    </div>
                    {[
                        { label: "سناب شات", value: snap, setter: setSnap, testid: "daily-snap-input" },
                        { label: "سناب شات 2", value: snap2, setter: setSnap2, testid: "daily-snap2-input" },
                        { label: "تيك توك", value: tik, setter: setTik, testid: "daily-tiktok-input" },
                        { label: "إنستقرام", value: insta, setter: setInsta, testid: "daily-insta-input" },
                        { label: "جوجل", value: google, setter: setGoogle, testid: "daily-google-input" },
                        { label: "المنتجات", value: prod, setter: setProd, testid: "daily-products-input" },
                    ].map((c) => (
                        <div key={c.testid}>
                            <label className="block text-sm font-semibold mb-1.5">{c.label} (ر.س)</label>
                            <input type="number" min={0} step="0.01" value={c.value} onChange={(e) => c.setter(e.target.value)}
                                placeholder="0.00"
                                className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand num"
                                data-testid={c.testid} />
                        </div>
                    ))}
                </div>
                <div className="mt-4">
                    <label className="block text-sm font-semibold mb-1.5">ملاحظات (اختياري)</label>
                    <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="…"
                        className="w-full px-3 py-2.5 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="daily-notes-input" />
                </div>
                <div className="mt-5 flex justify-end">
                    <button type="submit" disabled={saving}
                        className="inline-flex items-center gap-2 px-5 py-3 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                        data-testid="save-daily-btn">
                        <FloppyDisk size={20} weight="bold" />
                        {saving ? "جاري الحفظ…" : "حفظ"}
                    </button>
                </div>
            </form>

            <div className="rounded-xl border border-border bg-white p-6">
                <h2 className="text-xl font-bold mb-4 flex items-center gap-2" style={{ fontFamily: "Tajawal" }}>
                    <Receipt size={22} weight="duotone" className="text-brand" />
                    سجل التكاليف
                </h2>
                {loading ? (
                    <div className="text-center py-8 text-muted-foreground">جاري التحميل…</div>
                ) : items.length === 0 ? (
                    <div className="text-center py-8 text-muted-foreground">لم تسجل أي تكاليف بعد.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <table
                            className="w-full text-right text-sm border-collapse
                                [&_th]:px-3 [&_th]:border-s [&_th]:border-border
                                [&_td]:px-3 [&_td]:border-s [&_td]:border-border
                                [&_th:first-child]:border-s-0 [&_td:first-child]:border-s-0
                                [&_th:last-child]:border-s-0 [&_td:last-child]:border-s-0"
                            data-testid="daily-costs-table"
                        >
                            <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border">
                                <tr>
                                    <th className="py-3 font-semibold">التاريخ</th>
                                    <th className="py-3 font-semibold">سناب شات</th>
                                    <th className="py-3 font-semibold">سناب شات 2</th>
                                    <th className="py-3 font-semibold">تيك توك</th>
                                    <th className="py-3 font-semibold">إنستقرام</th>
                                    <th className="py-3 font-semibold">جوجل</th>
                                    <th className="py-3 font-semibold">المنتجات</th>
                                    <th className="py-3 font-semibold">الإجمالي</th>
                                    <th className="py-3 font-semibold"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {items.map((it) => (
                                    <tr key={it.date} className="border-b border-border last:border-0 hover:bg-accent/30 transition-colors">
                                        <td className="py-3 num font-semibold">{it.date}</td>
                                        <td className="py-3 num">{formatMoney(it.snapchat_ads)}</td>
                                        <td className="py-3 num">{formatMoney(it.snapchat_ads_2 || 0)}</td>
                                        <td className="py-3 num">{formatMoney(it.tiktok_ads)}</td>
                                        <td className="py-3 num">{formatMoney(it.instagram_ads)}</td>
                                        <td className="py-3 num">{formatMoney(it.google_ads || 0)}</td>
                                        <td className="py-3 num">{formatMoney(it.product_costs)}</td>
                                        <td className="py-3 num font-bold text-brand">{formatMoney(total(it))}</td>
                                        <td className="py-3">
                                            <button onClick={() => remove(it.date)}
                                                className="p-2 rounded-lg border border-border hover:bg-red-50 hover:text-red-600 transition-colors"
                                                title="حذف" data-testid={`delete-daily-${it.date}`}>
                                                <Trash size={16} />
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}

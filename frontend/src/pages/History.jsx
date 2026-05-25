import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Trash, ArrowRight, MagnifyingGlass } from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

export default function History() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [q, setQ] = useState("");

    const load = async () => {
        try {
            const { data } = await api.get("/analyses");
            setItems(data || []);
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const remove = async (id) => {
        if (!window.confirm("حذف هذا التحليل؟")) return;
        await api.delete(`/analyses/${id}`);
        setItems((prev) => prev.filter((i) => i.id !== id));
        toast.success("تم الحذف");
    };

    const filtered = items.filter((i) =>
        !q || i.name?.toLowerCase().includes(q.toLowerCase()) || i.filename?.toLowerCase().includes(q.toLowerCase())
    );

    return (
        <div className="space-y-6 animate-fade-in-up" data-testid="history-page">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>سجل التحاليل</h1>
                    <p className="text-muted-foreground mt-2 text-base">جميع التحاليل التي قمت بإجرائها مرتبة من الأحدث.</p>
                </div>
                <div className="relative w-full md:w-72">
                    <MagnifyingGlass size={18} className="absolute top-3 right-3 text-muted-foreground" />
                    <input
                        type="text"
                        value={q}
                        onChange={(e) => setQ(e.target.value)}
                        placeholder="بحث…"
                        className="w-full ps-3 pe-10 py-2.5 border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="history-search"
                    />
                </div>
            </div>

            <div className="rounded-xl border border-border bg-white p-6">
                {loading ? (
                    <div className="text-center py-8 text-muted-foreground">جاري التحميل…</div>
                ) : filtered.length === 0 ? (
                    <div className="text-center py-12 text-muted-foreground">
                        {q ? "لا نتائج." : "لم تقم بأي تحليل بعد. ابدأ من صفحة رفع الملفات."}
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-right" data-testid="history-table">
                            <thead className="text-sm text-muted-foreground border-b border-border">
                                <tr>
                                    <th className="py-3 font-semibold">الاسم</th>
                                    <th className="py-3 font-semibold">التاريخ</th>
                                    <th className="py-3 font-semibold">الطلبات</th>
                                    <th className="py-3 font-semibold">المبيعات</th>
                                    <th className="py-3 font-semibold">رسوم الدفع</th>
                                    <th className="py-3 font-semibold">الشحن</th>
                                    <th className="py-3 font-semibold">صافي الربح</th>
                                    <th className="py-3 font-semibold"></th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((a) => {
                                    const s = a.report.summary;
                                    return (
                                        <tr key={a.id} className="border-b border-border last:border-0 hover:bg-accent/40 transition-colors">
                                            <td className="py-3 font-semibold">{a.name}</td>
                                            <td className="py-3 num text-muted-foreground">{a.date}</td>
                                            <td className="py-3 num">{formatInt(s.total_orders)}</td>
                                            <td className="py-3 num font-semibold">{formatMoney(s.total_sales)}</td>
                                            <td className="py-3 num text-red-700">{formatMoney(s.total_payment_fees)}</td>
                                            <td className="py-3 num text-red-700">{formatMoney(s.total_shipping_cost)}</td>
                                            <td className={`py-3 num font-bold ${s.net_profit >= 0 ? "text-green-700" : "text-red-700"}`}>
                                                {formatMoney(s.net_profit)}
                                            </td>
                                            <td className="py-3">
                                                <div className="flex items-center gap-1">
                                                    <Link to={`/analyses/${a.id}`}
                                                        className="inline-flex items-center gap-1 text-brand font-semibold text-sm hover:underline"
                                                        data-testid={`open-analysis-${a.id}`}>
                                                        تفاصيل <ArrowRight size={14} />
                                                    </Link>
                                                    <button onClick={() => remove(a.id)}
                                                        className="p-1.5 rounded-lg hover:bg-red-50 hover:text-red-600 transition-colors ms-2"
                                                        title="حذف" data-testid={`delete-analysis-${a.id}`}>
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
                )}
            </div>
        </div>
    );
}

/**
 * Iter-144 — Unified per-courier ledger (Deferred companies only).
 *
 * Source of truth: GET /api/shipping-accounts/ledger
 *   net = cod_approved − shipping_cost − cod_fee
 *         − courier_to_bank + bank_to_courier
 *
 * Immediate companies are EXCLUDED — their shipping_cost is booked
 * as a direct operating expense, not as a courier liability.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../lib/api";


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });


export default function ShippingLedgerStub() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        api.get("/shipping-accounts/ledger")
            .then(({ data: d }) => alive && setData(d))
            .catch(() => alive && setData({ companies: [], totals: {} }))
            .finally(() => alive && setLoading(false));
        return () => { alive = false; };
    }, []);

    if (loading) return <div className="p-8 text-center text-slate-500">جاري حساب الأرصدة…</div>;
    const rows = data?.companies || [];
    const t = data?.totals || {};

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-5" dir="rtl" data-testid="shipping-ledger-page">
            <header className="flex items-center justify-between gap-2 flex-wrap">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">🚚 أرصدة شركات الشحن (موحَّد)</h1>
                    <p className="text-sm text-slate-500 mt-1">
                        تطبيق القاعدة الذهبية: لا تُحتسب أي قيود إلا للطلبات الموصَّلة فعلياً. الشركات الفورية مستبعَدة.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Link to="/shipping/transfers" className="px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg" data-testid="ledger-go-transfers">
                        📒 التحويلات
                    </Link>
                    <Link to="/shipping/settings" className="px-3 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 text-sm font-bold rounded-lg" data-testid="ledger-go-settings">
                        ⚙️ الإعدادات
                    </Link>
                </div>
            </header>

            {/* Top totals */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="ledger-top-totals">
                <Tile label="إجمالي لنا عند الشركات" value={t.net_owed_to_us} tone="emerald" testid="ledger-total-owed-to-us" />
                <Tile label="إجمالي علينا للشركات"   value={t.net_owed_by_us} tone="rose"    testid="ledger-total-owed-by-us" />
                <Tile label="صافي عام"                value={t.net_balance}   tone={t.net_balance >= 0 ? "emerald" : "rose"} bold testid="ledger-total-net" />
                <Tile label="COD معلَّق (متابعة)"     value={t.cod_pending}   tone="slate"   sub="غير معتمد · لا يدخل الصافي" testid="ledger-total-cod-pending" />
            </div>

            {/* Per-company table */}
            <div className="bg-white rounded-xl border-2 border-slate-200 overflow-hidden">
                {rows.length === 0 ? (
                    <div className="p-8 text-center text-sm text-slate-500" data-testid="ledger-empty">
                        لا توجد شركة شحن آجلة (Deferred) مُهيَّأة حالياً.<br />
                        اذهب إلى{" "}
                        <Link to="/shipping/settings" className="text-emerald-600 font-bold underline">إعدادات شركات الشحن</Link>{" "}
                        وفعِّل النوع «آجلة» لشركة واحدة على الأقل.
                    </div>
                ) : (
                    <table className="w-full text-xs" data-testid="ledger-table">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="p-2 text-right">الشركة</th>
                                <th className="p-2 num text-right">COD معتمد لنا</th>
                                <th className="p-2 num text-right">COD معلَّق</th>
                                <th className="p-2 num text-right">أجور الشحن</th>
                                <th className="p-2 num text-right">رسوم COD</th>
                                <th className="p-2 num text-right">محوَّل للبنك</th>
                                <th className="p-2 num text-right">دُفِع للشركة</th>
                                <th className="p-2 num text-right">صافي الرصيد</th>
                                <th className="p-2 text-right">التفسير</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => (
                                <tr key={r.name} className="border-t border-slate-100" data-testid={`ledger-row-${r.name}`}>
                                    <td className="p-2 font-bold text-slate-900">{r.name}<div className="text-[9px] text-slate-400">{r.delivered_orders_count} طلب موصَّل</div></td>
                                    <td className="p-2 num text-emerald-700">{fmt(r.cod_approved)}</td>
                                    <td className="p-2 num text-slate-500" title="غير داخل في الصافي">{fmt(r.cod_pending)}</td>
                                    <td className="p-2 num text-rose-700">{fmt(r.shipping_cost)}</td>
                                    <td className="p-2 num text-rose-700">
                                        <div className="font-bold">{fmt(r.cod_fee)}</div>
                                        <div className="text-[9px] text-slate-500">عمولة {fmt(r.cod_fee_net)} + ضريبة {fmt(r.cod_fee_vat)}</div>
                                        {r.cod_fee_rules_needing_review > 0 && <div className="text-[9px] font-bold text-rose-700">{r.cod_fee_rules_needing_review} شحنة خارج الشرائح</div>}
                                    </td>
                                    <td className="p-2 num text-blue-700">{fmt(r.courier_to_bank)}</td>
                                    <td className="p-2 num text-emerald-700">{fmt(r.bank_to_courier)}</td>
                                    <td className={`p-2 num font-extrabold ${r.net_balance >= 0 ? "text-emerald-700" : "text-rose-700"}`}>{fmt(r.net_balance)}</td>
                                    <td className="p-2 text-slate-600 text-[11px]">{r.interpretation}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            <div className="rounded-xl bg-amber-50 border-2 border-amber-200 text-amber-900 text-[11px] p-3" data-testid="ledger-formula-help">
                <strong>المعادلة:</strong> الصافي = COD المعتمد − أجور الشحن − رسوم COD − المحوَّل من الشركة + المدفوع للشركة.{" "}
                <span className="text-emerald-700 font-bold">موجب</span> = لنا عند الشركة (أصل) ·{" "}
                <span className="text-rose-700 font-bold">سالب</span> = علينا للشركة (التزام).
            </div>
        </div>
    );
}


function Tile({ label, value, sub, tone = "slate", bold = false, testid }) {
    const tones = {
        slate:   "bg-slate-50 border-slate-200 text-slate-900",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        rose:    "bg-rose-50 border-rose-200 text-rose-900",
    };
    return (
        <div className={`rounded-xl border-2 p-3 ${tones[tone]}`} data-testid={testid}>
            <div className="text-[10px] text-slate-500 mb-1">{label}</div>
            <div className={`num ${bold ? "text-xl font-extrabold" : "text-lg font-bold"}`}>{fmt(value)} ر.س</div>
            {sub && <div className="text-[9px] text-slate-400 mt-1">{sub}</div>}
        </div>
    );
}

import { useEffect, useState } from "react";
import api from "../lib/api";

const QOYOD_BASE = "/integrations/qoyod";

const STATUS_STYLE = {
  "أُرسل":    "bg-emerald-100 text-emerald-800 border-emerald-300",
  "لم يُرسل": "bg-amber-100 text-amber-800 border-amber-300",
  "فشل":      "bg-red-100 text-red-700 border-red-300",
  "مكرر":     "bg-slate-200 text-slate-700 border-slate-300",
};
const TABS = ["الكل", "لم يُرسل", "فشل", "مكرر", "أُرسل"];

function CountCard({ label, value, active, onClick }) {
  return (
    <button onClick={onClick} data-testid={`unsent-count-${label}`}
      className={`rounded-xl border px-4 py-3 text-right transition-colors ${
        active ? "border-slate-800 bg-slate-900 text-white"
               : "border-slate-200 bg-white hover:bg-slate-50"}`}>
      <div className="text-xs opacity-70">{label}</div>
      <div className="text-2xl font-bold">{value ?? 0}</div>
    </button>
  );
}

export default function QoyodUnsentOrders() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("لم يُرسل");
  const [days, setDays] = useState(30);
  const [error, setError] = useState(null);

  const fetchAll = async (d = days) => {
    setLoading(true);
    try {
      const res = await api.get(`${QOYOD_BASE}/unsent-orders`,
        { params: { days: d, limit: 1000 } });
      setData(res.data);
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || "تعذر تحميل البيانات");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { fetchAll(); /* eslint-disable-next-line */ }, [days]);

  const orders = (data?.orders || []).filter(
    (o) => tab === "الكل" || o.status === tab);
  const counts = data?.counts || {};

  return (
    <div className="space-y-6" dir="rtl" data-testid="qoyod-unsent-orders-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            طلبات لم تُرسل إلى قيود
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            شاشة التشغيل اليومية — كل طلب بحالة واحدة واضحة وسببها.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
            data-testid="unsent-days-select"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value={7}>آخر 7 أيام</option>
            <option value={30}>آخر 30 يوم</option>
            <option value={90}>آخر 90 يوم</option>
          </select>
          <button onClick={() => fetchAll()} data-testid="unsent-refresh-btn"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700">
            تحديث
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {["لم يُرسل", "فشل", "مكرر", "أُرسل"].map((s) => (
          <CountCard key={s} label={s} value={counts[s]}
            active={tab === s} onClick={() => setTab(s)} />
        ))}
      </div>

      <div className="flex gap-2 flex-wrap">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            data-testid={`unsent-tab-${t}`}
            className={`rounded-full border px-4 py-1.5 text-sm ${
              tab === t ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"}`}>
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
             data-testid="unsent-error">{String(error)}</div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto"
           data-testid="unsent-orders-table">
        {loading ? (
          <div className="p-6 text-sm text-slate-500">جاري التحميل…</div>
        ) : orders.length === 0 ? (
          <div className="p-6 text-sm text-slate-500" data-testid="unsent-empty">
            {tab === "لم يُرسل"
              ? "✅ لا توجد طلبات بانتظار الإرسال في هذه الفترة."
              : "لا توجد طلبات بهذه الحالة في الفترة المحددة."}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 text-right">رقم الطلب</th>
                <th className="px-3 py-2 text-right">التاريخ</th>
                <th className="px-3 py-2 text-right">المبلغ</th>
                <th className="px-3 py-2 text-right">طريقة الدفع</th>
                <th className="px-3 py-2 text-right">الحالة</th>
                <th className="px-3 py-2 text-right">السبب</th>
                <th className="px-3 py-2 text-right">فاتورة قيود</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o, i) => (
                <tr key={`${o.trace_id}-${i}`}
                    className="border-t border-slate-100 hover:bg-slate-50"
                    data-testid={`unsent-row-${o.order_number}`}>
                  <td className="px-3 py-2 font-medium">{o.order_number || "—"}</td>
                  <td className="px-3 py-2 text-slate-500" dir="ltr">
                    {o.received_at ? String(o.received_at).slice(0, 16).replace("T", " ") : "—"}
                  </td>
                  <td className="px-3 py-2">{o.total_amount != null ? `${o.total_amount} ر.س` : "—"}</td>
                  <td className="px-3 py-2">{o.payment_method || "—"}</td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[o.status] || ""}`}>
                      {o.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-600 max-w-md">{o.reason}</td>
                  <td className="px-3 py-2" dir="ltr">{o.qoyod_invoice_id || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

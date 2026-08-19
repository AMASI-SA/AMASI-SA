import { useEffect, useState } from "react";
import { Bank, CheckCircle, Image, XCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value || 0));
}

export default function StoreDeliveryPaymentReview() {
  const [items, setItems] = useState([]);
  const [busyId, setBusyId] = useState("");
  const [filter, setFilter] = useState("");

  async function load() {
    try {
      const response = await api.get("/store-delivery/payment-review/pending", { params: filter ? { method: filter } : {} });
      setItems(response.data?.items || []);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تحميل مراجعات الدفع");
    }
  }

  useEffect(() => { load(); }, [filter]);

  async function decide(item, decision) {
    const note = decision === "rejected" ? window.prompt("سبب الرفض للموصل:", "الإيصال غير واضح") : "تمت المطابقة";
    if (decision === "rejected" && note === null) return;
    setBusyId(item.assignment_id);
    try {
      await api.post(`/store-delivery/payment-review/${item.assignment_id}`, { decision, note: note || "" });
      toast.success(decision === "approved" ? "تم اعتماد الدفعة" : "تم رفض الإيصال وإعادته للموصل");
      await load();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر اعتماد المراجعة");
    } finally {
      setBusyId("");
    }
  }

  return (
    <main className="space-y-5 p-4 sm:p-6" dir="rtl">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><h1 className="text-2xl font-black text-slate-950">مراجعة تحصيلات الموصلين</h1><p className="mt-1 text-sm font-bold text-slate-500">الشبكة والتحويل البنكي لا يصبحان مدفوعين إلا بعد مطابقة المحاسب.</p></div>
        <select value={filter} onChange={(e) => setFilter(e.target.value)} className="h-11 rounded-xl border bg-white px-3 text-sm font-bold"><option value="">الكل</option><option value="card_terminal">شبكة</option><option value="bank_transfer">تحويل بنكي</option></select>
      </header>

      <section className="grid gap-4 xl:grid-cols-2">
        {items.map((item) => (
          <article key={item.assignment_id} className="rounded-3xl border bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3"><div><div className="text-xs font-black text-slate-500">الطلب</div><div className="text-xl font-black">#{item.order_number || item.order_id}</div><div className="mt-1 text-sm font-bold text-slate-600">الموصل: {item.driver_name || "—"} {item.driver_phone ? `· ${item.driver_phone}` : ""}</div></div><div className="rounded-2xl bg-emerald-50 px-3 py-2 text-lg font-black text-emerald-800" dir="ltr">{money(item.amount)}</div></div>
            <div className="mt-4 grid gap-2 text-sm font-bold sm:grid-cols-2"><div className="rounded-xl bg-slate-50 p-3">طريقة التحصيل<br/><b>{item.payment_method === "bank_transfer" ? "تحويل بنكي" : "شبكة"}</b></div><div className="rounded-xl bg-slate-50 p-3">البنك<br/><b>{item.bank_name_snapshot || "—"}</b></div></div>
            {item.receipt_url ? <a href={item.receipt_url} target="_blank" rel="noreferrer" className="mt-4 flex items-center justify-center gap-2 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 font-black text-sky-800"><Image size={20} />عرض صورة الإيصال</a> : <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900">لا يوجد إيصال صالح</div>}
            <div className="mt-4 grid grid-cols-2 gap-2"><button disabled={busyId === item.assignment_id} onClick={() => decide(item, "rejected")} className="flex items-center justify-center gap-2 rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 font-black text-rose-800 disabled:opacity-40"><XCircle size={20} />رفض</button><button disabled={busyId === item.assignment_id} onClick={() => decide(item, "approved")} className="flex items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-40"><CheckCircle size={20} />اعتماد مدفوع</button></div>
          </article>
        ))}
        {!items.length && <div className="rounded-3xl border border-dashed bg-white p-10 text-center text-sm font-bold text-slate-500 xl:col-span-2"><Bank className="mx-auto mb-2" size={32} />لا توجد دفعات بانتظار المراجعة.</div>}
      </section>
    </main>
  );
}

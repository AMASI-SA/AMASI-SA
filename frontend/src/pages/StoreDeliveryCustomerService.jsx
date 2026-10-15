import { useState } from "react";
import { BellRinging, MagnifyingGlass, Truck, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

const EMPTY = { instruction_type: "general", priority: "normal", note: "", delivery_date: "", delivery_time: "" };

export default function StoreDeliveryCustomerService() {
  const [query, setQuery] = useState("");
  const [data, setData] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);

  async function search(event) {
    event?.preventDefault?.();
    const value = query.trim();
    if (!value) return;
    setBusy(true);
    try {
      const response = await api.get(`/store-delivery/customer-service/order/${encodeURIComponent(value)}`);
      setData(response.data);
      setForm(EMPTY);
    } catch (error) {
      setData(null);
      toast.error(error?.response?.data?.detail?.code || "الطلب غير موجود");
    } finally {
      setBusy(false);
    }
  }

  async function addInstruction(event) {
    event.preventDefault();
    if (!data?.assignment) return toast.error("الطلب غير مسند لموصل متجر");
    if (!form.note.trim() && !form.delivery_date) return toast.error("اكتب التعليمات أو حدد الموعد");
    setBusy(true);
    try {
      await api.post("/store-delivery/instructions", {
        order_id: data.canonical_order_id,
        instruction_type: form.instruction_type,
        priority: form.priority,
        note: form.note.trim(),
        delivery_date: form.delivery_date || null,
        delivery_time: form.delivery_time || null,
      });
      toast.success("تم إرسال التعليمات للموصل");
      await search();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر حفظ التعليمات");
    } finally {
      setBusy(false);
    }
  }

  const order = data?.order;
  const assignment = data?.assignment;

  return (
    <main className="space-y-5 p-4 sm:p-6" dir="rtl">
      <header><h1 className="text-2xl font-black text-slate-950">تعليمات التوصيل — خدمة العملاء</h1><p className="mt-1 text-sm font-bold text-slate-500">ابحث برقم الطلب أو رقم التتبع، ثم أرسل للموصل موعدًا أو تنبيهًا ملزمًا.</p></header>

      <form onSubmit={search} className="flex max-w-3xl gap-2 rounded-2xl border bg-white p-3 shadow-sm"><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="رقم الطلب أو التتبع" className="h-12 flex-1 rounded-xl border px-4 text-lg font-black" dir="ltr" /><button disabled={busy || !query.trim()} className="flex items-center gap-2 rounded-xl bg-slate-950 px-5 font-black text-white disabled:opacity-40"><MagnifyingGlass size={20} />بحث</button></form>

      {order && (
        <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
          <section className="space-y-4">
            <article className="rounded-3xl border bg-white p-5 shadow-sm"><div className="text-xs font-bold text-slate-500">الطلب</div><div className="mt-1 text-2xl font-black">#{order.order_number || order.order_id}</div><div className="mt-4 grid gap-2 text-sm font-bold sm:grid-cols-2"><div className="rounded-xl bg-slate-50 p-3">العميل<br/><b>{order.customer_name || "—"}</b></div><div className="rounded-xl bg-slate-50 p-3">الجوال<br/><b dir="ltr">{order.customer_mobile || "—"}</b></div><div className="rounded-xl bg-slate-50 p-3">المدينة<br/><b>{order.shipping_city || "—"}</b></div><div className="rounded-xl bg-slate-50 p-3">الحالة<br/><b>{order.order_status || "—"}</b></div></div></article>
            {assignment ? <article className="rounded-3xl border border-emerald-200 bg-emerald-50 p-5"><div className="flex items-center gap-2 text-lg font-black text-emerald-950"><Truck size={24} />مع الموصل {assignment.driver_name_snapshot}</div><div className="mt-2 text-sm font-bold text-emerald-800">الحالة: {assignment.status} · المدينة: {assignment.shipping_city_snapshot}</div></article> : <article className="rounded-3xl border border-amber-200 bg-amber-50 p-5 text-sm font-black text-amber-950"><WarningCircle className="ml-1 inline" />الطلب ليس مسندًا حاليًا لموصل من المتجر.</article>}

            <section className="space-y-2"><h2 className="font-black">التعليمات الحالية</h2>{(data.instructions || []).map((item) => <article key={item.id} className="rounded-2xl border bg-white p-4"><div className="flex items-center justify-between"><div className="font-black">{item.priority === "urgent" ? "🔴 عاجل" : item.instruction_type}</div><div className="text-xs font-bold text-slate-500">{item.delivery_date || ""} {item.delivery_time || ""}</div></div><div className="mt-2 text-sm font-bold text-slate-700">{item.note || "—"}</div><div className="mt-2 text-xs font-bold text-slate-400">{item.acknowledged_at ? "اطلع عليها الموصل ✅" : "لم يطلع عليها بعد"}</div></article>)}{!(data.instructions || []).length && <div className="rounded-2xl border border-dashed p-5 text-center text-sm font-bold text-slate-500">لا توجد تعليمات.</div>}</section>
          </section>

          {assignment && <form onSubmit={addInstruction} className="h-fit rounded-3xl border bg-white p-5 shadow-sm"><div className="flex items-center gap-2 text-lg font-black"><BellRinging size={24} />إضافة تعليمات للموصل</div><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-xs font-black">نوع التعليمات<select value={form.instruction_type} onChange={(e) => setForm({ ...form, instruction_type: e.target.value })} className="mt-1 h-11 w-full rounded-xl border px-3"><option value="urgent">عاجل</option><option value="scheduled">موعد تسليم محدد</option><option value="do_not_deliver_today">لا توصل اليوم</option><option value="call_before_arrival">اتصل قبل الوصول</option><option value="general">ملاحظة عامة</option></select></label><label className="text-xs font-black">الأولوية<select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className="mt-1 h-11 w-full rounded-xl border px-3"><option value="normal">عادي</option><option value="high">مهم</option><option value="urgent">عاجل</option></select></label><label className="text-xs font-black">تاريخ التوصيل<input type="date" value={form.delivery_date} onChange={(e) => setForm({ ...form, delivery_date: e.target.value })} className="mt-1 h-11 w-full rounded-xl border px-3" /></label><label className="text-xs font-black">الوقت<input type="time" value={form.delivery_time} onChange={(e) => setForm({ ...form, delivery_time: e.target.value })} className="mt-1 h-11 w-full rounded-xl border px-3" /></label></div><label className="mt-4 block text-xs font-black">التعليمات<textarea rows={5} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="مثال: العميل مسافر، يجب التوصيل اليوم قبل 8 مساءً" className="mt-1 w-full rounded-xl border p-3 text-sm font-bold" /></label><button disabled={busy} className="mt-4 w-full rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">إرسال للموصل</button></form>}
        </div>
      )}
    </main>
  );
}

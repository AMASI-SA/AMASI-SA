import { useEffect, useMemo, useState } from "react";
import { Barcode, Bank, Camera, CashRegister, CheckCircle, Clock, CurrencyCircleDollar, Package, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value || 0));
}

export default function AmasiDeliveryApp() {
  const [tab, setTab] = useState("deliveries");
  const [deliveries, setDeliveries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [instructions, setInstructions] = useState([]);
  const [barcode, setBarcode] = useState("");
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);

  const urgentByOrder = useMemo(() => {
    const map = new Map();
    for (const item of instructions) {
      const current = map.get(item.order_id);
      if (!current || item.priority === "urgent") map.set(item.order_id, item);
    }
    return map;
  }, [instructions]);

  async function refresh() {
    setBusy(true);
    try {
      const [deliveryRes, summaryRes, instructionsRes] = await Promise.all([
        api.get("/store-delivery/driver-app/deliveries"),
        api.get("/store-delivery/driver-app/accounts-summary"),
        api.get("/store-delivery/instructions/driver/me"),
      ]);
      setDeliveries(deliveryRes.data?.items || []);
      setSummary(summaryRes.data || {});
      setInstructions(instructionsRes.data?.items || []);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تحميل تواصيلك");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  async function lookup(event) {
    event.preventDefault();
    if (!barcode.trim()) return;
    setBusy(true);
    try {
      const response = await api.post("/store-delivery/driver-app/scan", { barcode: barcode.trim() });
      setSelected(response.data);
      setBarcode("");
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "هذه الشحنة غير مسندة لك");
      setBarcode("");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status) {
    if (!selected?.id) return;
    setBusy(true);
    try {
      const response = await api.post(`/store-delivery/driver-app/deliveries/${selected.id}/status`, { status });
      setSelected(response.data);
      toast.success(status === "delivered" ? "تم تسجيل التوصيل" : "تم تحديث الحالة");
      await refresh();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تحديث الحالة");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 pb-24" dir="rtl">
      <header className="sticky top-0 z-20 border-b bg-white/95 px-4 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-xl items-center justify-between">
          <div><div className="text-xs font-black text-emerald-700">توصيل أماسي</div><h1 className="text-xl font-black text-slate-950">تواصيلي</h1></div>
          <button onClick={refresh} disabled={busy} className="rounded-xl border px-3 py-2 text-xs font-black">تحديث</button>
        </div>
      </header>

      <div className="mx-auto max-w-xl space-y-4 p-4">
        {tab === "deliveries" && (
          <>
            <form onSubmit={lookup} className="rounded-3xl border bg-white p-4 shadow-sm">
              <div className="flex items-center gap-2 font-black"><Camera size={24} />مسح باركود الشحنة</div>
              <input autoFocus value={barcode} onChange={(e) => setBarcode(e.target.value)} placeholder="امسح الباركود" className="mt-3 h-14 w-full rounded-2xl border px-4 text-lg font-black" />
              <button disabled={busy || !barcode.trim()} className="mt-3 w-full rounded-2xl bg-slate-950 px-4 py-3 font-black text-white disabled:opacity-40"><Barcode className="ml-2 inline" />فتح الشحنة</button>
            </form>

            {selected && (
              <section className="rounded-3xl border bg-white p-5 shadow-sm">
                <div className="flex items-center justify-between"><div><div className="text-xs font-bold text-slate-500">الطلب</div><div className="text-2xl font-black">#{selected.order_number || selected.order_id}</div></div><Package size={34} className="text-emerald-700" /></div>
                {urgentByOrder.get(selected.order_id) && <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900"><WarningCircle className="ml-1 inline" />{urgentByOrder.get(selected.order_id).note || "تعليمات عاجلة من خدمة العملاء"}</div>}
                <div className="mt-4 grid grid-cols-2 gap-2 text-sm font-bold"><div className="rounded-xl bg-slate-50 p-3">المدينة<br/><b>{selected.shipping_city_snapshot || selected.shipping_city}</b></div><div className="rounded-xl bg-slate-50 p-3">أجرة التوصيل<br/><b>{money(selected.delivery_fee_snapshot)}</b></div></div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2"><button disabled={busy || selected.delivery_status === "delivered"} onClick={() => setStatus("out_for_delivery")} className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 font-black text-amber-900"><Clock className="ml-1 inline" />جاري التوصيل</button><button disabled={busy || selected.delivery_status === "delivered"} onClick={() => setStatus("delivered")} className="rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white"><CheckCircle className="ml-1 inline" />تم التوصيل</button></div>
              </section>
            )}

            <section className="space-y-2">
              {deliveries.map((row) => (
                <button key={row.id} onClick={() => setSelected(row)} className="w-full rounded-2xl border bg-white p-4 text-right shadow-sm">
                  <div className="flex items-center justify-between"><div><div className="font-black">#{row.order_number || row.order_id}</div><div className="mt-1 text-xs font-bold text-slate-500">{row.shipping_city_snapshot} · {row.delivery_status}</div></div><span className="text-sm font-black text-emerald-700">{money(row.delivery_fee_snapshot)}</span></div>
                  {urgentByOrder.get(row.order_id) && <div className="mt-2 rounded-xl bg-rose-50 px-3 py-2 text-xs font-black text-rose-800">{urgentByOrder.get(row.order_id).note}</div>}
                </button>
              ))}
            </section>
          </>
        )}

        {tab === "accounts" && summary && (
          <section className="space-y-3">
            <div className="grid grid-cols-2 gap-3"><Card label="تم التوصيل" value={summary.delivered_count || 0} Icon={CheckCircle} /><Card label="جاري التوصيل" value={summary.out_for_delivery_count || 0} Icon={Clock} /></div>
            <Card label="مستحقاتي" value={money(summary.delivery_earnings_due)} Icon={CurrencyCircleDollar} />
            <Card label="كاش بعهدتي" value={money(summary.cod_cash_custody)} Icon={CashRegister} />
            <Card label="شبكة بانتظار المراجعة" value={money(summary.card_terminal_pending)} Icon={Barcode} />
            <Card label="تحويلات بانتظار المراجعة" value={money(summary.bank_transfer_pending)} Icon={Bank} />
          </section>
        )}
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t bg-white p-3"><div className="mx-auto grid max-w-xl grid-cols-2 gap-2"><button onClick={() => setTab("deliveries")} className={`rounded-2xl px-4 py-3 font-black ${tab === "deliveries" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>تواصيلي</button><button onClick={() => setTab("accounts")} className={`rounded-2xl px-4 py-3 font-black ${tab === "accounts" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>حساباتي</button></div></nav>
    </main>
  );
}

function Card({ label, value, Icon }) {
  return <div className="rounded-3xl border bg-white p-4 shadow-sm"><div className="flex items-center gap-2 text-xs font-black text-slate-500"><Icon size={20} />{label}</div><div className="mt-2 text-2xl font-black text-slate-950" dir="ltr">{value}</div></div>;
}

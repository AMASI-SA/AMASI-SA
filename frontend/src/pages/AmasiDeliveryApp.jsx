import { useEffect, useMemo, useState } from "react";
import {
  Barcode,
  Bank,
  Camera,
  CashRegister,
  CheckCircle,
  Clock,
  CurrencyCircleDollar,
  Package,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

function money(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "SAR",
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function deliveryStatusLabel(value) {
  if (value === "assigned") return "مسندة لي";
  if (value === "out_for_delivery") return "جاري التوصيل";
  if (value === "delivered") return "تم التوصيل";
  return value || "—";
}

export default function AmasiDeliveryApp() {
  const [tab, setTab] = useState("deliveries");
  const [deliveries, setDeliveries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [instructions, setInstructions] = useState([]);
  const [banks, setBanks] = useState([]);
  const [barcode, setBarcode] = useState("");
  const [selected, setSelected] = useState(null);
  const [paymentOpen, setPaymentOpen] = useState(false);
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
      const [deliveryRes, summaryRes, instructionsRes, banksRes] = await Promise.all([
        api.get("/store-delivery/app/deliveries"),
        api.get("/store-delivery/app/accounts/summary"),
        api.get("/store-delivery/instructions/driver/me"),
        api.get("/store-delivery/app/bank-accounts"),
      ]);
      setDeliveries(deliveryRes.data?.items || []);
      setSummary(summaryRes.data || {});
      setInstructions(instructionsRes.data?.items || []);
      setBanks(banksRes.data?.items || []);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تحميل تواصيلك");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function lookup(event) {
    event.preventDefault();
    const value = barcode.trim();
    if (!value) return;
    const match = deliveries.find((row) => [
      row.barcode,
      row.order_number,
      row.order_id,
      row.shipping_barcode,
      row.tracking_number,
    ].filter(Boolean).some((candidate) => String(candidate).trim() === value));
    if (!match) {
      toast.error("هذه الشحنة غير مسندة لك");
      setBarcode("");
      return;
    }
    setSelected(match);
    setBarcode("");
  }

  async function setOutForDelivery() {
    if (!selected) return;
    setBusy(true);
    try {
      const response = await api.post("/store-delivery/app/deliveries/status", {
        barcode: selected.barcode || selected.order_number || selected.order_id,
        target_status: "out_for_delivery",
      });
      setSelected(response.data);
      toast.success("تم تسجيل جاري التوصيل");
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
          <div>
            <div className="text-xs font-black text-emerald-700">توصيل أماسي</div>
            <h1 className="text-xl font-black text-slate-950">{tab === "deliveries" ? "تواصيلي" : "حساباتي"}</h1>
          </div>
          <button onClick={refresh} disabled={busy} className="rounded-xl border px-3 py-2 text-xs font-black">تحديث</button>
        </div>
      </header>

      <div className="mx-auto max-w-xl space-y-4 p-4">
        {tab === "deliveries" && (
          <>
            <form onSubmit={lookup} className="rounded-3xl border bg-white p-4 shadow-sm">
              <div className="flex items-center gap-2 font-black"><Camera size={24} />مسح باركود الشحنة</div>
              <input
                autoFocus
                value={barcode}
                onChange={(event) => setBarcode(event.target.value)}
                placeholder="امسح الباركود"
                className="mt-3 h-14 w-full rounded-2xl border px-4 text-lg font-black"
              />
              <button disabled={!barcode.trim()} className="mt-3 w-full rounded-2xl bg-slate-950 px-4 py-3 font-black text-white disabled:opacity-40">
                <Barcode className="ml-2 inline" />فتح الشحنة
              </button>
            </form>

            {selected && (
              <section className="rounded-3xl border bg-white p-5 shadow-sm">
                <div className="flex items-center justify-between">
                  <div><div className="text-xs font-bold text-slate-500">الطلب</div><div className="text-2xl font-black">#{selected.order_number || selected.order_id}</div></div>
                  <Package size={34} className="text-emerald-700" />
                </div>
                {urgentByOrder.get(selected.order_id) && (
                  <div className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900">
                    <WarningCircle className="ml-1 inline" />
                    {urgentByOrder.get(selected.order_id).note || "تعليمات عاجلة من خدمة العملاء"}
                  </div>
                )}
                <div className="mt-4 grid grid-cols-2 gap-2 text-sm font-bold">
                  <div className="rounded-xl bg-slate-50 p-3">المدينة<br/><b>{selected.shipping_city_snapshot}</b></div>
                  <div className="rounded-xl bg-slate-50 p-3">أجرة التوصيل<br/><b>{money(selected.delivery_fee_snapshot)}</b></div>
                </div>
                <div className="mt-4 rounded-xl bg-slate-50 p-3 text-sm font-bold">الحالة: {deliveryStatusLabel(selected.status)}</div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2">
                  <button
                    disabled={busy || selected.status !== "assigned"}
                    onClick={setOutForDelivery}
                    className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 font-black text-amber-900 disabled:opacity-40"
                  >
                    <Clock className="ml-1 inline" />جاري التوصيل
                  </button>
                  <button
                    disabled={busy || selected.status !== "out_for_delivery"}
                    onClick={() => setPaymentOpen(true)}
                    className="rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-40"
                  >
                    <CheckCircle className="ml-1 inline" />تم التوصيل
                  </button>
                </div>
              </section>
            )}

            <section className="space-y-2">
              {deliveries.map((row) => (
                <button key={row.id} onClick={() => setSelected(row)} className="w-full rounded-2xl border bg-white p-4 text-right shadow-sm">
                  <div className="flex items-center justify-between">
                    <div><div className="font-black">#{row.order_number || row.order_id}</div><div className="mt-1 text-xs font-bold text-slate-500">{row.shipping_city_snapshot} · {deliveryStatusLabel(row.status)}</div></div>
                    <span className="text-sm font-black text-emerald-700">{money(row.delivery_fee_snapshot)}</span>
                  </div>
                  {urgentByOrder.get(row.order_id) && <div className="mt-2 rounded-xl bg-rose-50 px-3 py-2 text-xs font-black text-rose-800">{urgentByOrder.get(row.order_id).note}</div>}
                </button>
              ))}
            </section>
          </>
        )}

        {tab === "accounts" && summary && (
          <section className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Card label="تم التوصيل" value={summary.delivery_counts?.delivered || 0} Icon={CheckCircle} />
              <Card label="جاري التوصيل" value={summary.delivery_counts?.out_for_delivery || 0} Icon={Clock} />
            </div>
            <Card label="مستحقاتي" value={money(summary.earnings_due)} Icon={CurrencyCircleDollar} />
            <Card label="كاش بعهدتي" value={money(summary.cod_cash_custody)} Icon={CashRegister} />
            <Card label="شبكة بانتظار المراجعة" value={money(summary.card_pending_review)} Icon={Barcode} />
            <Card label="تحويلات بانتظار المراجعة" value={money(summary.bank_transfer_pending_review)} Icon={Bank} />
          </section>
        )}
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t bg-white p-3">
        <div className="mx-auto grid max-w-xl grid-cols-2 gap-2">
          <button onClick={() => setTab("deliveries")} className={`rounded-2xl px-4 py-3 font-black ${tab === "deliveries" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>تواصيلي</button>
          <button onClick={() => setTab("accounts")} className={`rounded-2xl px-4 py-3 font-black ${tab === "accounts" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>حساباتي</button>
        </div>
      </nav>

      {paymentOpen && selected && (
        <DeliveryPaymentModal
          assignment={selected}
          banks={banks}
          busy={busy}
          onClose={() => setPaymentOpen(false)}
          onSaved={async (result) => {
            setPaymentOpen(false);
            setSelected(result);
            await refresh();
          }}
          setBusy={setBusy}
        />
      )}
    </main>
  );
}

function DeliveryPaymentModal({ assignment, banks, onClose, onSaved, busy, setBusy }) {
  const [outstanding, setOutstanding] = useState(assignment.outstanding_amount ?? "");
  const [method, setMethod] = useState("");
  const [receipt, setReceipt] = useState("");
  const [bankId, setBankId] = useState("");

  async function submit(event) {
    event.preventDefault();
    const amount = Number(outstanding || 0);
    if (amount > 0 && !method) return toast.error("حدد طريقة استلام المبلغ");
    if (amount > 0 && ["card_terminal", "bank_transfer"].includes(method) && !receipt.trim()) return toast.error("صورة/مرجع الإيصال مطلوب");
    if (amount > 0 && method === "bank_transfer" && !bankId) return toast.error("اختر حساب المؤسسة الذي تم التحويل إليه");
    setBusy(true);
    try {
      const response = await api.post("/store-delivery/app/deliveries/status", {
        barcode: assignment.barcode || assignment.order_number || assignment.order_id,
        target_status: "delivered",
        outstanding_amount: amount,
        payment_method: amount > 0 ? method : null,
        receipt_reference: receipt.trim() || null,
        bank_account_id: method === "bank_transfer" ? bankId : null,
      });
      toast.success("تم تسجيل التوصيل والتحصيل");
      await onSaved(response.data);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر إكمال التوصيل");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/70 sm:items-center sm:p-4">
      <form onSubmit={submit} className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-3xl bg-white p-5 sm:rounded-3xl" dir="rtl">
        <div className="flex items-center justify-between"><h2 className="text-lg font-black">إكمال التوصيل والتحصيل</h2><button type="button" onClick={onClose} className="rounded-xl p-2"><X size={20} /></button></div>
        <label className="mt-5 block text-xs font-black text-slate-600">المبلغ المتبقي على العميل<input type="number" min="0" step="0.01" value={outstanding} onChange={(e) => setOutstanding(e.target.value)} className="mt-1 h-12 w-full rounded-2xl border px-3 text-lg font-black" dir="ltr" /></label>
        {Number(outstanding || 0) > 0 && (
          <>
            <div className="mt-4 grid grid-cols-3 gap-2">
              {[{ key: "cash", label: "كاش" }, { key: "card_terminal", label: "شبكة" }, { key: "bank_transfer", label: "تحويل بنكي" }].map((item) => (
                <button type="button" key={item.key} onClick={() => setMethod(item.key)} className={`rounded-2xl border px-2 py-3 text-xs font-black ${method === item.key ? "border-emerald-600 bg-emerald-50 text-emerald-800" : "bg-white text-slate-700"}`}>{item.label}</button>
              ))}
            </div>
            {["card_terminal", "bank_transfer"].includes(method) && <label className="mt-4 block text-xs font-black text-slate-600">صورة/مرجع الإيصال<input value={receipt} onChange={(e) => setReceipt(e.target.value)} placeholder="مرجع الملف المرفوع" className="mt-1 h-12 w-full rounded-2xl border px-3" /></label>}
            {method === "bank_transfer" && <label className="mt-4 block text-xs font-black text-slate-600">حساب المؤسسة<select value={bankId} onChange={(e) => setBankId(e.target.value)} className="mt-1 h-12 w-full rounded-2xl border px-3 font-bold"><option value="">اختر البنك</option>{banks.map((bank) => <option key={bank.id} value={bank.id}>{bank.name || bank.provider} {bank.iban ? `— ${bank.iban}` : ""}</option>)}</select></label>}
          </>
        )}
        <button disabled={busy} className="mt-5 w-full rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">تأكيد تم التوصيل</button>
      </form>
    </div>
  );
}

function Card({ label, value, Icon }) {
  return (
    <div className="rounded-3xl border bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 text-xs font-black text-slate-500"><Icon size={20} />{label}</div>
      <div className="mt-2 text-2xl font-black text-slate-950" dir="ltr">{value}</div>
    </div>
  );
}

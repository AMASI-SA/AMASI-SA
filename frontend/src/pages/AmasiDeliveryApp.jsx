import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bank,
  Barcode,
  BellRinging,
  CashRegister,
  CheckCircle,
  Clock,
  CurrencyCircleDollar,
  Package,
  SignOut,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import DriverPhysicalCash from "../components/driver/DriverPhysicalCash";
import BarcodeCameraScanner from "../components/BarcodeCameraScanner";
import { useAuth } from "../context/AuthContext";
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

async function uploadReceipt(assignmentId, file) {
  const body = new FormData();
  body.append("assignment_id", assignmentId);
  body.append("file", file);
  return (await api.post("/store-delivery/evidence/receipt", body, {
    headers: { "Content-Type": "multipart/form-data" },
  })).data;
}

export default function AmasiDeliveryApp() {
  const { logout } = useAuth();
  const [tab, setTab] = useState("deliveries");
  const [driver, setDriver] = useState(null);
  const [deliveries, setDeliveries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [instructions, setInstructions] = useState([]);
  const [banks, setBanks] = useState([]);
  const [reminders, setReminders] = useState([]);
  const [barcode, setBarcode] = useState("");
  const [selected, setSelected] = useState(null);
  const [paymentOpen, setPaymentOpen] = useState(false);
  const [resubmitOpen, setResubmitOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const instructionByOrder = useMemo(() => {
    const map = new Map();
    for (const item of instructions) {
      const current = map.get(item.order_id);
      if (!current || item.priority === "urgent") map.set(item.order_id, item);
    }
    return map;
  }, [instructions]);

  const refresh = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setBusy(true);
    try {
      const [meRes, deliveryRes, summaryRes, instructionsRes, banksRes] = await Promise.all([
        api.get("/store-delivery/app/me"),
        api.get("/store-delivery/app/deliveries"),
        api.get("/store-delivery/app/accounts/summary"),
        api.get("/store-delivery/instructions/driver/me"),
        api.get("/store-delivery/app/bank-accounts"),
      ]);
      setDriver(meRes.data || null);
      setDeliveries(deliveryRes.data?.items || []);
      setSummary(summaryRes.data || {});
      setInstructions(instructionsRes.data?.items || []);
      setBanks(banksRes.data?.items || []);
      setSelected((current) => {
        if (!current?.id) return current;
        return (deliveryRes.data?.items || []).find((row) => row.id === current.id) || current;
      });
    } catch (error) {
      if (!silent) toast.error(error?.response?.data?.detail?.code || "تعذر تحميل تواصيلك");
    } finally {
      if (!silent) setBusy(false);
    }
  }, []);

  const pollReminders = useCallback(async () => {
    try {
      const response = await api.get("/store-delivery/app/reminders");
      const items = response.data?.items || [];
      setReminders(items);
      items.forEach((item) => toast.warning(item.message, { duration: 9000 }));
    } catch {
      // Reminder polling must never block delivery work.
    }
  }, []);

  useEffect(() => {
    refresh();
    pollReminders();
    const interval = window.setInterval(() => {
      refresh({ silent: true });
      pollReminders();
    }, 60_000);
    return () => window.clearInterval(interval);
  }, [refresh, pollReminders]);

  const selectByBarcode = useCallback((value) => {
    const clean = String(value || "").trim();
    if (!clean) return;
    const match = deliveries.find((row) => [
      row.barcode,
      row.order_number,
      row.order_id,
      row.shipping_barcode,
      row.tracking_number,
    ].filter(Boolean).some((candidate) => String(candidate).trim() === clean));
    if (!match) {
      toast.error("هذه الشحنة غير مسندة لك");
      return;
    }
    setSelected(match);
    setBarcode("");
  }, [deliveries]);

  function lookup(event) {
    event.preventDefault();
    selectByBarcode(barcode);
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
      await refresh({ silent: true });
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تحديث الحالة");
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge(instructionId) {
    try {
      await api.post(`/store-delivery/instructions/${instructionId}/acknowledge`);
      setInstructions((current) => current.map((item) => item.id === instructionId ? { ...item, acknowledged_at: new Date().toISOString() } : item));
      toast.success("تم تسجيل اطلاعك على التعليمات");
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تسجيل الاطلاع");
    }
  }

  const selectedInstruction = selected ? instructionByOrder.get(selected.order_id) : null;

  return (
    <main className="min-h-screen bg-slate-50 pb-24" dir="rtl">
      <header className="sticky top-0 z-20 border-b bg-white/95 px-4 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-xl items-center justify-between gap-3">
          <div>
            <div className="text-xs font-black text-emerald-700">توصيل أماسي</div>
            <h1 className="text-xl font-black text-slate-950">{tab === "deliveries" ? "تواصيلي" : "حساباتي"}</h1>
            {driver?.name && <div className="mt-0.5 text-xs font-bold text-slate-500">{driver.name} · {driver.city}</div>}
          </div>
          <div className="flex gap-2"><button onClick={() => refresh()} disabled={busy} className="rounded-xl border px-3 py-2 text-xs font-black">تحديث</button><button onClick={logout} className="rounded-xl border border-rose-200 bg-rose-50 p-2 text-rose-700" aria-label="تسجيل الخروج"><SignOut size={20} /></button></div>
        </div>
      </header>

      <div className="mx-auto max-w-xl space-y-4 p-4">
        {reminders.map((item) => <div key={`${item.instruction_id}-${item.code}`} className={`rounded-2xl border p-4 text-sm font-black ${item.overdue ? "border-rose-300 bg-rose-50 text-rose-950" : "border-amber-300 bg-amber-50 text-amber-950"}`}><BellRinging className="ml-1 inline" size={20} />{item.message}</div>)}

        {tab === "deliveries" && (
          <>
            <BarcodeCameraScanner active={!busy} onDetected={selectByBarcode} label="تشغيل كاميرا الباركود" />
            <form onSubmit={lookup} className="rounded-3xl border bg-white p-4 shadow-sm">
              <div className="flex items-center gap-2 font-black"><Barcode size={24} />إدخال الباركود يدويًا</div>
              <input value={barcode} onChange={(event) => setBarcode(event.target.value)} placeholder="امسح أو اكتب الباركود" className="mt-3 h-14 w-full rounded-2xl border px-4 text-lg font-black" />
              <button disabled={!barcode.trim()} className="mt-3 w-full rounded-2xl bg-slate-950 px-4 py-3 font-black text-white disabled:opacity-40">فتح الشحنة</button>
            </form>

            {selected && (
              <section className="rounded-3xl border bg-white p-5 shadow-sm">
                <div className="flex items-center justify-between"><div><div className="text-xs font-bold text-slate-500">الطلب</div><div className="text-2xl font-black">#{selected.order_number || selected.order_id}</div></div><Package size={34} className="text-emerald-700" /></div>
                {selectedInstruction && <div className={`mt-4 rounded-2xl border p-3 text-sm font-black ${selectedInstruction.priority === "urgent" ? "border-rose-200 bg-rose-50 text-rose-900" : "border-amber-200 bg-amber-50 text-amber-900"}`}><WarningCircle className="ml-1 inline" />{selectedInstruction.note || "تعليمات من خدمة العملاء"}{(selectedInstruction.delivery_date || selectedInstruction.delivery_time) && <div className="mt-1 text-xs">الموعد: {selectedInstruction.delivery_date || ""} {selectedInstruction.delivery_time || ""}</div>}{!selectedInstruction.acknowledged_at && <button onClick={() => acknowledge(selectedInstruction.id)} className="mt-3 rounded-xl bg-white px-3 py-2 text-xs font-black">تم الاطلاع</button>}</div>}
                <div className="mt-4 grid grid-cols-2 gap-2 text-sm font-bold"><div className="rounded-xl bg-slate-50 p-3">المدينة<br/><b>{selected.shipping_city_snapshot}</b></div><div className="rounded-xl bg-slate-50 p-3">أجرة التوصيل<br/><b>{money(selected.delivery_fee_snapshot)}</b></div><div className="rounded-xl bg-slate-50 p-3">العميل<br/><b>{selected.customer_name || "—"}</b></div><div className="rounded-xl bg-slate-50 p-3">المبلغ المتبقي<br/><b>{selected.outstanding_amount_available ? money(selected.outstanding_amount) : "يحتاج تحديث"}</b></div></div>
                <div className="mt-4 rounded-xl bg-slate-50 p-3 text-sm font-bold">الحالة: {deliveryStatusLabel(selected.status)}</div>
                {selected.payment_review_status === "rejected" && <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900">الإيصال مرفوض: {selected.payment_review_note || "راجع المحاسب"}<button onClick={() => setResubmitOpen(true)} className="mt-3 block w-full rounded-xl bg-white px-3 py-2">رفع إيصال بديل</button></div>}
                <div className="mt-4 grid gap-2 sm:grid-cols-2"><button disabled={busy || selected.status !== "assigned"} onClick={setOutForDelivery} className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 font-black text-amber-900 disabled:opacity-40"><Clock className="ml-1 inline" />جاري التوصيل</button><button disabled={busy || selected.status !== "out_for_delivery" || selected.outstanding_amount_available === false} onClick={() => setPaymentOpen(true)} className="rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-40"><CheckCircle className="ml-1 inline" />تم التوصيل</button></div>
              </section>
            )}

            <section className="space-y-2">{deliveries.map((row) => <button key={row.id} onClick={() => setSelected(row)} className="w-full rounded-2xl border bg-white p-4 text-right shadow-sm"><div className="flex items-center justify-between"><div><div className="font-black">#{row.order_number || row.order_id}</div><div className="mt-1 text-xs font-bold text-slate-500">{row.shipping_city_snapshot} · {deliveryStatusLabel(row.status)}</div></div><span className="text-sm font-black text-emerald-700">{money(row.delivery_fee_snapshot)}</span></div>{instructionByOrder.get(row.order_id) && <div className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-xs font-black text-amber-900">{instructionByOrder.get(row.order_id).note}</div>}{row.payment_review_status === "rejected" && <div className="mt-2 rounded-xl bg-rose-50 px-3 py-2 text-xs font-black text-rose-900">إيصال الدفع مرفوض — يحتاج إعادة رفع</div>}</button>)}</section>
          </>
        )}

        {tab === "accounts" && <DriverPhysicalCash />}
        {tab === "accounts" && summary && <section className="space-y-3"><div className="grid grid-cols-2 gap-3"><Card label="تم التوصيل" value={summary.delivery_counts?.delivered || 0} Icon={CheckCircle} /><Card label="جاري التوصيل" value={summary.delivery_counts?.out_for_delivery || 0} Icon={Clock} /></div><Card label="إجمالي أجوري" value={money(summary.earnings_total)} Icon={CurrencyCircleDollar} /><Card label="تم دفعه لي" value={money(summary.earnings_paid)} Icon={CurrencyCircleDollar} /><Card label="المتبقي لي" value={money(summary.earnings_due)} Icon={CurrencyCircleDollar} /><Card label="تحصيل تشغيلي مسجل" value={money(summary.cod_cash_collected)} Icon={CashRegister} /><Card label="توريد تشغيلي مسجل" value={money(summary.cod_cash_remitted)} Icon={CashRegister} /><Card label="متبقي تشغيلي — ليس إثبات حيازة فعلية" value={money(summary.cod_cash_custody)} Icon={CashRegister} /><Card label="شبكة بانتظار المراجعة" value={money(summary.card_pending_review)} Icon={Barcode} /><Card label="تحويلات بانتظار المراجعة" value={money(summary.bank_transfer_pending_review)} Icon={Bank} /></section>}
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t bg-white p-3"><div className="mx-auto grid max-w-xl grid-cols-2 gap-2"><button onClick={() => setTab("deliveries")} className={`rounded-2xl px-4 py-3 font-black ${tab === "deliveries" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>تواصيلي</button><button onClick={() => setTab("accounts")} className={`rounded-2xl px-4 py-3 font-black ${tab === "accounts" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>حساباتي</button></div></nav>

      {paymentOpen && selected && <DeliveryPaymentModal assignment={selected} banks={banks} busy={busy} setBusy={setBusy} onClose={() => setPaymentOpen(false)} onSaved={async (result) => { setPaymentOpen(false); setSelected(result); await refresh({ silent: true }); }} />}
      {resubmitOpen && selected && <ResubmitModal assignment={selected} banks={banks} busy={busy} setBusy={setBusy} onClose={() => setResubmitOpen(false)} onSaved={async () => { setResubmitOpen(false); await refresh({ silent: true }); }} />}
    </main>
  );
}

export function DeliveryPaymentModal({ assignment, banks, onClose, onSaved, busy, setBusy }) {
  const amount = Number(assignment.outstanding_amount || 0);
  const [method, setMethod] = useState("");
  const [receiptFile, setReceiptFile] = useState(null);
  const [proofFile, setProofFile] = useState(null);
  const [bankId, setBankId] = useState("");
  const [actualCash, setActualCash] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(null);
  const locked = busy || Boolean(pending);
  useEffect(() => {
    if (!pending) return undefined;
    const warn = event => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);
  const isCash = amount > 0 && method === "cash";
  const validCash = /^\d{1,7}(?:\.\d{1,2})?$/.test(actualCash) && Number(actualCash) <= 1000000;

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (busy) return;
    if (!pending) {
    if (amount > 0 && !method) return setError("حدد طريقة استلام المبلغ");
    if (isCash && (!validCash || !confirmed)) return setError("أدخل النقد الذي استلمته فعليًا وأكد استلامه");
    if (!proofFile) return setError("صورة إثبات تسليم الطلب مطلوبة");
    if (amount > 0 && ["card_terminal", "bank_transfer"].includes(method) && !receiptFile) return setError("صورة الإيصال مطلوبة");
    if (amount > 0 && method === "bank_transfer" && !bankId) return setError("اختر حساب المؤسسة الذي تم التحويل إليه");
    }
    setBusy(true);
    try {
      let payload = pending;
      if (!payload) {
      const proofBody = new FormData();
      proofBody.append("assignment_id", assignment.id);
      proofBody.append("file", proofFile);
      const proof = (await api.post("/store-delivery/evidence/delivery-proof", proofBody, { headers: { "Content-Type": "multipart/form-data" } })).data;
      if (!proof?.proof_reference) throw new Error("delivery_proof_missing");
      let receiptReference = null;
      if (amount > 0 && ["card_terminal", "bank_transfer"].includes(method)) {
        const uploaded = await uploadReceipt(assignment.id, receiptFile);
        receiptReference = uploaded.receipt_reference;
        if (!receiptReference) throw new Error("receipt_reference_missing");
      }
      payload = {
        barcode: assignment.barcode || assignment.order_number || assignment.order_id,
        target_status: "delivered",
        payment_method: amount > 0 ? method : null,
        delivery_proof_reference: proof.proof_reference,
        receipt_reference: receiptReference,
        bank_account_id: method === "bank_transfer" ? bankId : null,
        ...(isCash ? { physical_cash_amount: actualCash, physical_cash_confirmed: true } : {}),
      };
      setPending(payload);
      }
      const response = await api.post("/store-delivery/app/deliveries/status", payload);
      setPending(null);
      toast.success("تم تسجيل التوصيل وبيانات التحصيل");
      await onSaved(response.data);
    } catch (failure) {
      if (failure?.response?.status >= 400 && failure.response.status < 500) setPending(null);
      setError(failure?.response?.data?.detail?.code || "تعذر إكمال التوصيل؛ تحقق من الحالة قبل إعادة المحاولة");
    } finally {
      setBusy(false);
    }
  }

  return <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/70 sm:items-center sm:p-4"><form onSubmit={submit} className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-3xl bg-white p-5 sm:rounded-3xl" dir="rtl">
    <div className="flex items-center justify-between"><h2 className="text-lg font-black">إكمال التوصيل والتحصيل</h2><button type="button" disabled={locked} onClick={onClose} aria-label="إغلاق" className="rounded-xl p-2"><X size={20} /></button></div>
    <p className="mt-4 rounded-xl bg-sky-50 p-3">المبلغ المتوقع تحصيله: <b dir="ltr">{money(amount)}</b><br/>مصدره الطلب ولا يتغير بإدخال النقد الفعلي.</p>
    {amount > 0 && <>
      <div className="mt-4 grid grid-cols-3 gap-2">{[{ key: "cash", label: "كاش" }, { key: "card_terminal", label: "شبكة" }, { key: "bank_transfer", label: "تحويل بنكي" }].map(item => <button type="button" disabled={locked} key={item.key} aria-pressed={method === item.key} onClick={() => { setMethod(item.key); setConfirmed(false); }} className={`rounded-xl border p-3 ${method === item.key ? "bg-emerald-50" : "bg-white"}`}>{item.label}</button>)}</div>
      {isCash && <div className="mt-4 space-y-3 rounded-xl border p-3">
        <label className="block">النقد المستلم فعليًا (ر.س)<input aria-label="النقد المستلم فعليًا" inputMode="decimal" value={actualCash} disabled={locked} onChange={e => { setActualCash(e.target.value); setConfirmed(false); }} className="mt-1 block w-full rounded-xl border p-3" /></label>
        {validCash && <p>الفرق عن المتوقع: <b dir="ltr">{money(Number(actualCash) - amount)}</b> — {Number(actualCash) === amount ? "مطابق" : Number(actualCash) < amount ? "نقص في النقد المستلم" : "زيادة في النقد المستلم"}</p>}
        <label className="flex gap-2"><input type="checkbox" checked={confirmed} disabled={locked} onChange={e => setConfirmed(e.target.checked)} />أؤكد أنني استلمت هذا المبلغ نقدًا عند تسليم الطلب</label>
        <p className="text-xs">هذا إقرار بالنقد الفعلي؛ لا يغيّر المبلغ المستحق على الطلب ولا يسجل توريدًا للمؤسسة.</p>
      </div>}
      {["card_terminal", "bank_transfer"].includes(method) && <label className="mt-4 block">صورة إيصال الشبكة/التحويل<input aria-label="صورة إيصال الدفع" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={locked} onChange={e => setReceiptFile(e.target.files?.[0] || null)} className="mt-1 block w-full border p-3" /></label>}
      {method === "bank_transfer" && <label className="mt-4 block">حساب المؤسسة<select aria-label="حساب المؤسسة" value={bankId} disabled={locked} onChange={e => setBankId(e.target.value)} className="mt-1 block w-full border p-3"><option value="">اختر البنك</option>{banks.map(bank => <option key={bank.id} value={bank.id}>{bank.name || bank.provider} {bank.iban || ""}</option>)}</select></label>}
    </>}
    <label className="mt-4 block">صورة إثبات تسليم الطلب<input aria-label="صورة إثبات تسليم الطلب" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={locked} onChange={e => setProofFile(e.target.files?.[0] || null)} className="mt-1 block w-full border p-3" /></label>
    {error && <p role="alert" className="mt-3 text-rose-800">{error}</p>}
    {pending && <p role="status">لم تتأكد نتيجة الطلب؛ أعد إرسال البيانات نفسها دون تغيير الإثبات أو المبلغ.</p>}
    <button disabled={busy} className="mt-5 w-full rounded-2xl bg-emerald-700 p-4 font-black text-white disabled:opacity-40">{pending ? "إعادة إرسال التأكيد نفسه" : "تأكيد تم التوصيل"}</button>
  </form></div>;
}

function ResubmitModal({ assignment, banks, onClose, onSaved, busy, setBusy }) {
  const [receiptFile, setReceiptFile] = useState(null);
  const [bankId, setBankId] = useState("");
  const method = assignment.collection_method || assignment.payment_method_snapshot;
  async function submit(event) {
    event.preventDefault();
    if (!receiptFile) return toast.error("اختر صورة إيصال جديدة");
    if (method === "bank_transfer" && !bankId) return toast.error("اختر حساب المؤسسة");
    setBusy(true);
    try {
      const uploaded = await uploadReceipt(assignment.id, receiptFile);
      await api.post(`/store-delivery/app/payment-review/${assignment.id}/resubmit`, {
        receipt_reference: uploaded.receipt_reference,
        bank_account_id: method === "bank_transfer" ? bankId : null,
      });
      toast.success("تم إرسال الإيصال الجديد للمراجعة");
      await onSaved();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر إعادة إرسال الإيصال");
    } finally {
      setBusy(false);
    }
  }
  return <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/70 sm:items-center sm:p-4"><form onSubmit={submit} className="w-full max-w-lg rounded-t-3xl bg-white p-5 sm:rounded-3xl" dir="rtl"><div className="flex items-center justify-between"><h2 className="text-lg font-black">رفع إيصال بديل</h2><button type="button" onClick={onClose} className="rounded-xl p-2"><X size={20} /></button></div><div className="mt-3 rounded-xl bg-rose-50 p-3 text-sm font-bold text-rose-900">سبب الرفض: {assignment.payment_review_note || "الإيصال السابق لم يعتمد"}</div><label className="mt-4 block text-xs font-black">الإيصال الجديد<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={(e) => setReceiptFile(e.target.files?.[0] || null)} className="mt-1 block w-full rounded-xl border p-3" /></label>{method === "bank_transfer" && <label className="mt-4 block text-xs font-black">حساب المؤسسة<select value={bankId} onChange={(e) => setBankId(e.target.value)} className="mt-1 h-12 w-full rounded-xl border px-3"><option value="">اختر البنك</option>{banks.map((bank) => <option key={bank.id} value={bank.id}>{bank.name || bank.provider}</option>)}</select></label>}<button disabled={busy} className="mt-5 w-full rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">إرسال للمراجعة</button></form></div>;
}

function Card({ label, value, Icon }) {
  return <div className="rounded-3xl border bg-white p-4 shadow-sm"><div className="flex items-center gap-2 text-xs font-black text-slate-500"><Icon size={20} />{label}</div><div className="mt-2 text-2xl font-black text-slate-950" dir="ltr">{value}</div></div>;
}

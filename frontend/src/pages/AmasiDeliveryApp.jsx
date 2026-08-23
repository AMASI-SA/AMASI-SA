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

function reviewStatusLabel(value, method) {
  if (method === "cash") return "في عهدتك حتى توريده";
  if (value === "approved") return "معتمد — وصل للمؤسسة";
  if (value === "rejected") return "مرفوض — يحتاج تصحيح";
  if (value === "pending_accountant_review" || value === "pending") return "بانتظار مراجعة المحاسب";
  return value || "—";
}

function paymentMethodLabel(value) {
  if (value === "cash") return "كاش";
  if (value === "card_terminal") return "شبكة";
  if (value === "bank_transfer") return "تحويل بنكي";
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

function buildLedger(deliveries, summary) {
  const rows = [];
  for (const row of deliveries) {
    if (row.status !== "delivered") continue;
    const order = row.order_number || row.order_id || "—";
    const when = row.delivered_at || row.updated_at || row.assigned_at || "";
    const earning = Number(row.delivery_fee_snapshot || 0);
    if (earning > 0) {
      rows.push({
        id: `earning-${row.id}`,
        when,
        order,
        title: "استحقاق أجرة توصيل",
        method: "أجرة توصيل",
        debit: 0,
        credit: earning,
        status: "مستحق لك",
      });
    }
    const amount = Number(row.collection_amount || 0);
    const method = row.collection_method || row.payment_method_snapshot;
    if (amount > 0 && method === "cash") {
      rows.push({
        id: `cash-${row.id}`,
        when,
        order,
        title: "تحصيل نقدي من العميل",
        method: "كاش",
        debit: amount,
        credit: 0,
        status: "على عهدتك حتى توريده",
      });
    } else if (amount > 0 && ["card_terminal", "bank_transfer"].includes(method)) {
      rows.push({
        id: `direct-${row.id}`,
        when,
        order,
        title: "تحصيل مباشر للمؤسسة",
        method: paymentMethodLabel(method),
        debit: 0,
        credit: 0,
        status: reviewStatusLabel(row.payment_review_status, method),
      });
    }
  }
  const remitted = Number(summary?.cod_cash_remitted || 0);
  if (remitted > 0) rows.push({ id: "summary-remitted", when: "", order: "—", title: "إجمالي مبالغ وردتها للمؤسسة", method: "توريد", debit: 0, credit: remitted, status: "مورد" });
  const earningsPaid = Number(summary?.earnings_paid || 0);
  if (earningsPaid > 0) rows.push({ id: "summary-paid", when: "", order: "—", title: "إجمالي مستحقات صُرفت لك", method: "صرف", debit: earningsPaid, credit: 0, status: "مصروف" });
  return rows.sort((a, b) => String(b.when || "").localeCompare(String(a.when || "")));
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

  const ledger = useMemo(() => buildLedger(deliveries, summary), [deliveries, summary]);
  const debitTotal = useMemo(() => ledger.reduce((sum, row) => sum + Number(row.debit || 0), 0), [ledger]);
  const creditTotal = useMemo(() => ledger.reduce((sum, row) => sum + Number(row.credit || 0), 0), [ledger]);
  const netBalance = debitTotal - creditTotal;

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
                {selected.status === "delivered" && selected.collection_method ? <div className="mt-3 rounded-2xl border border-sky-200 bg-sky-50 p-3 text-sm font-black text-sky-900">التحصيل: {paymentMethodLabel(selected.collection_method)} • {reviewStatusLabel(selected.payment_review_status, selected.collection_method)}</div> : null}
                {selected.payment_review_status === "rejected" && <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900">الإيصال مرفوض: {selected.payment_review_note || "راجع المحاسب"}<button onClick={() => setResubmitOpen(true)} className="mt-3 block w-full rounded-xl bg-white px-3 py-2">رفع إيصال بديل</button></div>}
                <div className="mt-4 grid gap-2 sm:grid-cols-2"><button disabled={busy || selected.status !== "assigned"} onClick={setOutForDelivery} className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 font-black text-amber-900 disabled:opacity-40"><Clock className="ml-1 inline" />جاري التوصيل</button><button disabled={busy || selected.status !== "out_for_delivery" || selected.outstanding_amount_available === false} onClick={() => setPaymentOpen(true)} className="rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-40"><CheckCircle className="ml-1 inline" />تم التوصيل</button></div>
              </section>
            )}

            <section className="space-y-2">{deliveries.map((row) => <button key={row.id} onClick={() => setSelected(row)} className="w-full rounded-2xl border bg-white p-4 text-right shadow-sm"><div className="flex items-center justify-between"><div><div className="font-black">#{row.order_number || row.order_id}</div><div className="mt-1 text-xs font-bold text-slate-500">{row.shipping_city_snapshot} · {deliveryStatusLabel(row.status)}</div></div><span className="text-sm font-black text-emerald-700">{money(row.delivery_fee_snapshot)}</span></div>{row.status === "delivered" && row.collection_method ? <div className="mt-2 rounded-xl bg-sky-50 px-3 py-2 text-xs font-black text-sky-900">{paymentMethodLabel(row.collection_method)} • {reviewStatusLabel(row.payment_review_status, row.collection_method)}</div> : null}{instructionByOrder.get(row.order_id) && <div className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-xs font-black text-amber-900">{instructionByOrder.get(row.order_id).note}</div>}{row.payment_review_status === "rejected" && <div className="mt-2 rounded-xl bg-rose-50 px-3 py-2 text-xs font-black text-rose-900">إيصال الدفع مرفوض — يحتاج إعادة رفع</div>}</button>)}</section>
          </>
        )}

        {tab === "accounts" && summary && <section className="space-y-3">
          <div className="grid grid-cols-2 gap-3"><Card label="تم التوصيل" value={summary.delivery_counts?.delivered || 0} Icon={CheckCircle} /><Card label="جاري التوصيل" value={summary.delivery_counts?.out_for_delivery || 0} Icon={Clock} /></div>
          <div className="grid grid-cols-2 gap-3"><Card label="مدين — عليك للمتجر" value={money(summary.cod_cash_custody)} Icon={CashRegister} /><Card label="دائن — لك على المتجر" value={money(summary.earnings_due)} Icon={CurrencyCircleDollar} /></div>
          <div className={`rounded-3xl border p-4 shadow-sm ${netBalance > 0 ? "border-rose-200 bg-rose-50" : netBalance < 0 ? "border-emerald-200 bg-emerald-50" : "bg-white"}`}><div className="text-xs font-black text-slate-500">الرصيد الصافي</div><div className="mt-2 text-xl font-black">{netBalance > 0 ? `عليك للمتجر ${money(netBalance)}` : netBalance < 0 ? `لك على المتجر ${money(Math.abs(netBalance))}` : "الحساب متوازن"}</div></div>
          <Card label="شبكة بانتظار المراجعة" value={money(summary.card_pending_review)} Icon={Barcode} />
          <Card label="تحويلات بانتظار المراجعة" value={money(summary.bank_transfer_pending_review)} Icon={Bank} />
          <div className="rounded-3xl border bg-white p-4 shadow-sm">
            <div className="mb-3 text-base font-black text-slate-950">سجل المدين والدائن</div>
            <div className="overflow-hidden rounded-2xl border">
              <div className="grid grid-cols-[1fr_72px_72px] bg-slate-100 px-3 py-2 text-xs font-black text-slate-600"><div>الحركة</div><div className="text-center">مدين</div><div className="text-center">دائن</div></div>
              {ledger.map((row) => <div key={row.id} className="grid grid-cols-[1fr_72px_72px] gap-1 border-t px-3 py-3 text-xs"><div><div className="font-black text-slate-900">{row.title}</div><div className="mt-1 font-bold text-slate-500">طلب #{row.order} • {row.method}</div><div className={`mt-1 font-black ${row.status.includes("مرفوض") ? "text-rose-700" : row.status.includes("بانتظار") ? "text-amber-700" : "text-emerald-700"}`}>{row.status}</div></div><div className="text-center font-black text-rose-700">{row.debit ? money(row.debit) : "—"}</div><div className="text-center font-black text-emerald-700">{row.credit ? money(row.credit) : "—"}</div></div>)}
              {!ledger.length && <div className="p-5 text-center text-sm font-bold text-slate-500">لا توجد حركات مالية بعد.</div>}
            </div>
          </div>
        </section>}
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t bg-white p-3"><div className="mx-auto grid max-w-xl grid-cols-2 gap-2"><button onClick={() => setTab("deliveries")} className={`rounded-2xl px-4 py-3 font-black ${tab === "deliveries" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>تواصيلي</button><button onClick={() => setTab("accounts")} className={`rounded-2xl px-4 py-3 font-black ${tab === "accounts" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>حساباتي</button></div></nav>

      {paymentOpen && selected && <DeliveryPaymentModal assignment={selected} banks={banks} busy={busy} setBusy={setBusy} onClose={() => setPaymentOpen(false)} onSaved={async (result) => { setPaymentOpen(false); setSelected(result); await refresh({ silent: true }); }} />}
      {resubmitOpen && selected && <ResubmitModal assignment={selected} banks={banks} busy={busy} setBusy={setBusy} onClose={() => setResubmitOpen(false)} onSaved={async () => { setResubmitOpen(false); await refresh({ silent: true }); }} />}
    </main>
  );
}

function DeliveryPaymentModal({ assignment, banks, onClose, onSaved, busy, setBusy }) {
  const amount = Number(assignment.outstanding_amount || 0);
  const [method, setMethod] = useState("");
  const [receiptFile, setReceiptFile] = useState(null);
  const [bankId, setBankId] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (amount > 0 && !method) return toast.error("حدد طريقة استلام المبلغ");
    if (amount > 0 && ["card_terminal", "bank_transfer"].includes(method) && !receiptFile) return toast.error("صورة الإيصال مطلوبة");
    if (amount > 0 && method === "bank_transfer" && !bankId) return toast.error("اختر حساب المؤسسة الذي تم التحويل إليه");
    setBusy(true);
    try {
      let receiptReference = null;
      if (receiptFile) {
        const uploaded = await uploadReceipt(assignment.id, receiptFile);
        receiptReference = uploaded.receipt_reference;
      }
      const response = await api.post("/store-delivery/app/deliveries/status", {
        barcode: assignment.barcode || assignment.order_number || assignment.order_id,
        target_status: "delivered",
        payment_method: amount > 0 ? method : null,
        receipt_reference: receiptReference,
        bank_account_id: method === "bank_transfer" ? bankId : null,
      });
      toast.success(method === "cash" ? "تم تسجيل التوصيل والكاش في عهدتك" : "تم تسجيل التوصيل وإرسال التحصيل لمراجعة المحاسب");
      await onSaved(response.data);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر إكمال التوصيل");
    } finally {
      setBusy(false);
    }
  }

  return <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/70 sm:items-center sm:p-4"><form onSubmit={submit} className="max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-3xl bg-white p-5 sm:rounded-3xl" dir="rtl"><div className="flex items-center justify-between"><h2 className="text-lg font-black">إكمال التوصيل والتحصيل</h2><button type="button" onClick={onClose} className="rounded-xl p-2"><X size={20} /></button></div><div className="mt-5 rounded-2xl border border-sky-200 bg-sky-50 p-4"><div className="text-xs font-black text-sky-700">المبلغ المتبقي من ميزان/سلة</div><div className="mt-1 text-3xl font-black text-sky-950" dir="ltr">{money(amount)}</div><div className="mt-1 text-xs font-bold text-sky-800">غير قابل للتعديل من الموصل</div></div>{amount > 0 && <><div className="mt-4 grid grid-cols-3 gap-2">{[{ key: "cash", label: "كاش" }, { key: "card_terminal", label: "شبكة" }, { key: "bank_transfer", label: "تحويل بنكي" }].map((item) => <button type="button" key={item.key} onClick={() => setMethod(item.key)} className={`rounded-2xl border px-2 py-3 text-xs font-black ${method === item.key ? "border-emerald-600 bg-emerald-50 text-emerald-800" : "bg-white text-slate-700"}`}>{item.label}</button>)}</div>{method === "cash" && <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm font-black text-amber-900">سيُسجل المبلغ مدينًا عليك وفي عهدتك حتى تسليمه للمؤسسة.</div>}{["card_terminal", "bank_transfer"].includes(method) && <><div className="mt-4 rounded-2xl border border-sky-200 bg-sky-50 p-3 text-sm font-black text-sky-900">لن يُسجل المبلغ في عهدتك. سيبقى بانتظار مراجعة المحاسب حتى التأكد من وصوله.</div><label className="mt-4 block text-xs font-black text-slate-600">صورة إيصال الشبكة/التحويل<input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={(e) => setReceiptFile(e.target.files?.[0] || null)} className="mt-1 block w-full rounded-2xl border p-3" /></label></>}{method === "bank_transfer" && <label className="mt-4 block text-xs font-black text-slate-600">حساب المؤسسة<select value={bankId} onChange={(e) => setBankId(e.target.value)} className="mt-1 h-12 w-full rounded-2xl border px-3 font-bold"><option value="">اختر البنك</option>{banks.map((bank) => <option key={bank.id} value={bank.id}>{bank.name || bank.provider} {bank.iban ? `— ${bank.iban}` : ""}</option>)}</select></label>}</>}<button disabled={busy} className="mt-5 w-full rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">تأكيد تم التوصيل</button></form></div>;
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

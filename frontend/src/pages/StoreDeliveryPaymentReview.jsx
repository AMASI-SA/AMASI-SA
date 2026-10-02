import { useEffect, useState } from "react";
import { Bank, CheckCircle, Image, XCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value || 0));
}

function cents(value) {
  const text = String(value ?? "");
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  const amount = BigInt(whole) * BigInt(100) + BigInt(fraction.padEnd(2, "0"));
  return amount > BigInt(0) ? amount : null;
}

function isPosDestination(fact) {
  return fact?.status === "active" && fact.currency === "SAR" &&
    fact.source === "documented_opening_fact" && fact.category === "other_receivable" &&
    fact.entity_type === "asset" && fact.sub_account === "other_receivable" &&
    typeof fact.id === "string" && fact.id.length > 0 && fact.id === fact.entity_id &&
    typeof fact.display_name === "string" && fact.display_name.trim().length > 0;
}

export default function StoreDeliveryPaymentReview() {
  const [items, setItems] = useState([]);
  const [busyId, setBusyId] = useState("");
  const [filter, setFilter] = useState("");
  const [banks, setBanks] = useState([]);
  const [destinations, setDestinations] = useState({});
  const [bankError, setBankError] = useState("");
  const [posFacts, setPosFacts] = useState([]);
  const [posError, setPosError] = useState("");
  useEffect(() => {
    let active = true;
    api.get("/store-delivery/payment-review/bank-accounts").then(({ data }) => {
      if (active && data?.source === "mz2_financial_accounts") setBanks(data.items || []);
    }, () => { if (active) setBankError("تعذر تحميل البنوك الأصلية؛ الاعتماد غير متاح"); });
    api.get("/accounting-module/onboarding/typed-facts").then(({ data }) => {
      if (active) setPosFacts(Array.isArray(data?.items) ? data.items.filter(isPosDestination) : []);
    }, () => { if (active) setPosError("تعذر تحميل الذمم الأصلية؛ اعتماد الشبكة غير متاح"); });
    return () => { active = false; };
  }, []);
  const destination = item => destinations[item.assignment_id] || {};
  const canApprove = item => item.payment_method === "bank_transfer"
    ? banks.some(bank => bank.id === destination(item).bank) && destination(item).movement?.trim()
    : item.payment_method === "card_terminal" && Boolean(item.receipt_reference?.trim() && item.receipt_url?.trim()) &&
      posFacts.some(fact => fact.id === destination(item).pos) && cents(item.amount) !== null &&
      cents(destination(item).amount) === cents(item.amount);
  const updateDestination = (item, key, value) => setDestinations(previous => ({ ...previous,
    [item.assignment_id]: { ...previous[item.assignment_id], [key]: value } }));

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
    if (decision === "approved" && !canApprove(item)) return;
    const note = decision === "rejected" ? window.prompt("سبب الرفض للموصل:", "الإيصال غير واضح") : "تمت المطابقة";
    if (decision === "rejected" && note === null) return;
    setBusyId(item.assignment_id);
    try {
      const payload = { decision, note: note || "" };
      if (decision === "approved") {
        if (item.payment_method === "bank_transfer") Object.assign(payload, {
          destination_financial_id: destination(item).bank,
          settlement_reference: destination(item).movement.trim(),
        });
        else {
          payload.destination_financial_id = destination(item).pos;
          payload.pos_reviewed_amount = destination(item).amount;
          if (destination(item).reference?.trim()) payload.settlement_reference = destination(item).reference.trim();
        }
      }
      await api.post(`/store-delivery/payment-review/${item.assignment_id}`, payload);
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
            {item.payment_method === "bank_transfer" ? <div className="mt-4 space-y-2">
              <label>البنك المستلم المؤكد<select value={destination(item).bank || ""} onChange={e => updateDestination(item, "bank", e.target.value)}><option value="">اختر البنك</option>{banks.map(bank => <option key={bank.id} value={bank.id}>{bank.name || bank.id}</option>)}</select></label>
              <label>معرف حركة البنك الواردة المستوردة<input value={destination(item).movement || ""} onChange={e => updateDestination(item, "movement", e.target.value)} /></label>
              <p>استخدم معرف الحركة من كشف البنك المستورد؛ رقم الإيصال أو وصف التحويل لا يثبت وصول المبلغ. يتحقق النظام من البنك والمبلغ وأصل الكشف.</p>
              {bankError && <p role="alert">{bankError}</p>}
            </div> : item.payment_method === "card_terminal" ? <div className="mt-4 space-y-2">
              <label>ذمة الشبكة المختارة<select value={destination(item).pos || ""} onChange={e => updateDestination(item, "pos", e.target.value)}><option value="">اختر ذمة موثقة من الذمم المدينة الأخرى</option>{posFacts.map(fact => <option key={fact.id} value={fact.id}>{fact.display_name} · asset/{fact.entity_id}/other_receivable</option>)}</select></label>
              <label>مبلغ إيصال الشبكة المطابق<input inputMode="decimal" value={destination(item).amount || ""} onChange={e => updateDestination(item, "amount", e.target.value)} /></label>
              <label>مرجع عملية الشبكة (اختياري)<input value={destination(item).reference || ""} onChange={e => updateDestination(item, "reference", e.target.value)} /></label>
              <p>راجع الإيصال المرتبط وطابق مبلغه مع مبلغ الطلب. اعتماد المحاسب ينقل مسؤولية COD إلى ذمة الشبكة المختارة ضمن الذمم المدينة الأخرى؛ لا يثبت وصول المبلغ إلى البنك.</p>
              <p>مرجع الإيصال المرتبط: <bdi>{item.receipt_reference || "غير متاح"}</bdi></p>
              {(!item.receipt_reference || !item.receipt_url) && <p role="alert">يلزم إيصال مرتبط قابل للعرض قبل الاعتماد.</p>}
              {destination(item).amount && cents(destination(item).amount) !== cents(item.amount) && <p role="alert">أدخل مبلغًا صحيحًا مطابقًا لمبلغ الطلب، بمنزلتين عشريتين كحد أقصى.</p>}
              {posError && <p role="alert">{posError}</p>}
            </div> : <p role="status">طريقة التحصيل غير متاحة للاعتماد.</p>}
            <div className="mt-4 grid grid-cols-2 gap-2"><button disabled={busyId === item.assignment_id} onClick={() => decide(item, "rejected")} className="flex items-center justify-center gap-2 rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 font-black text-rose-800 disabled:opacity-40"><XCircle size={20} />رفض</button><button disabled={busyId === item.assignment_id || !canApprove(item)} onClick={() => decide(item, "approved")} className="flex items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-40"><CheckCircle size={20} />اعتماد مدفوع</button></div>
          </article>
        ))}
        {!items.length && <div className="rounded-3xl border border-dashed bg-white p-10 text-center text-sm font-bold text-slate-500 xl:col-span-2"><Bank className="mx-auto mb-2" size={32} />لا توجد دفعات بانتظار المراجعة.</div>}
      </section>
    </main>
  );
}

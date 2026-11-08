import { useEffect, useRef, useState } from "react";
import { CashRegister, CurrencyCircleDollar } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";
import StoreDeliveryPaymentReview from "./StoreDeliveryPaymentReview";

function money(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "غير متاح";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value));
}

const settlementStatus = {
  pending_driver_confirmation: "بانتظار تأكيد الموصل",
  posted: "أكد الموصل التسوية",
  rejected: "رفض الموصل التسوية",
};

export default function StoreDeliverySettlements() {
  const [drivers, setDrivers] = useState([]);
  const [driverId, setDriverId] = useState("");
  const [summary, setSummary] = useState(null);
  const [items, setItems] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [summaryError, setSummaryError] = useState("");
  const [loading, setLoading] = useState(false);
  const loadVersion = useRef(0);
  const alive = useRef(true);
  const ledgerLinked = summary && Number.isFinite(summary.ledger_cod_receivable) &&
    Number.isFinite(summary.ledger_delivery_fee_payable);

  useEffect(() => { alive.current = true; return () => { alive.current = false; loadVersion.current += 1; }; }, []);

  async function loadDrivers() {
    const response = await api.get("/store-delivery/settlements/drivers");
    if (alive.current) setDrivers(response.data?.items || []);
  }

  useEffect(() => {
    loadDrivers().catch(() => { if (alive.current) toast.error("تعذر تحميل قائمة الموصلين؛ يمكن مراجعة إيصالات التحصيل أدناه."); });
    api.get("/store-delivery/settlements/accounts")
      .then(response => { if (alive.current) setAccounts(response.data?.items || []); })
      .catch(() => { if (alive.current) setAccounts([]); });
  }, []);

  async function load(id = driverId) {
    const version = ++loadVersion.current;
    setSummary(null); setItems([]); setSummaryError("");
    if (!id) { setLoading(false); return; }
    setLoading(true);
    try {
      const response = await api.get(`/store-delivery/settlements/driver/${id}`);
      if (!alive.current || version !== loadVersion.current) return;
      setSummary(response.data?.summary || null);
      setItems(response.data?.items || []);
    } catch (error) {
      if (!alive.current || version !== loadVersion.current) return;
      setSummaryError("تعذر تحميل حساب الموصل. مراجعة إيصالات الشبكة والتحويل متاحة أدناه.");
    } finally {
      if (alive.current && version === loadVersion.current) setLoading(false);
    }
  }

  async function create(type) {
    if (busy || !ledgerLinked) return;
    const available = type === "cod-remittance"
      ? Math.min(summary?.cod_cash_custody || 0, summary?.ledger_cod_receivable || 0)
      : type === "earning-payment"
        ? Math.min(summary?.delivery_earnings_due || 0, summary?.ledger_delivery_fee_payable || 0)
        : Math.min(summary?.cod_cash_custody || 0, summary?.delivery_earnings_due || 0, summary?.ledger_cod_receivable || 0, summary?.ledger_delivery_fee_payable || 0);
    if (!(available > 0)) return;
    let amount = 0;
    let earning_offset = 0;
    if (type === "net-settlement") {
      const offsetText = window.prompt("كم من أجرة الموصل ستُخصم صراحة من عهدة COD؟", String(available));
      if (offsetText === null) return;
      earning_offset = Number(offsetText);
      if (!(earning_offset > 0) || earning_offset > available) return toast.error("قيمة المقاصة غير صحيحة");
      const ledgerCod = Number(summary?.ledger_cod_receivable || 0);
      const defaultBank = Math.max(ledgerCod - earning_offset, 0);
      const amountText = window.prompt("كم سيسلّم الموصل للبنك/الصندوق بعد المقاصة؟", String(defaultBank));
      if (amountText === null) return;
      amount = Number(amountText);
      if (!(amount >= 0) || amount + earning_offset > ledgerCod) return toast.error("مبلغ التوريد أكبر من عهدة COD المرحلة محاسبيًا");
    } else {
      const amountText = window.prompt(type === "cod-remittance" ? "كم سلّم الموصل من الكاش؟" : "كم تم دفعه للموصل؟", String(available));
      if (amountText === null) return;
      amount = Number(amountText);
      if (!(amount > 0)) return toast.error("المبلغ غير صحيح");
    }
    let account_id = "";
    if (amount > 0 && accounts.length) {
      const names = accounts.map((a, i) => `${i + 1}. ${a.name || a.provider}`).join("\n");
      const indexText = window.prompt(`اختر رقم حساب البنك/الصندوق المرتبط بطلب التسوية:\n${names}`, "1");
      const index = Number(indexText) - 1;
      if (Number.isInteger(index) && accounts[index]) account_id = accounts[index].id;
    }
    if (amount > 0 && !account_id) return toast.error("اختر الحساب البنكي قبل تسجيل التسوية");
    setBusy(true);
    try {
      const response = await api.post(`/store-delivery/settlements/driver/${driverId}/${type}`, {
        amount,
        earning_offset,
        account_id: account_id || null,
        reference: "",
        note: "",
      });
      setSummary(response.data?.summary || summary);
      toast.success("تم إرسال طلب التسوية؛ ينتظر تأكيد الموصل ولا يثبت وصول مبلغ للبنك.");
      await load();
      await loadDrivers();
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تسجيل التسوية");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="space-y-5 p-4 sm:p-6" dir="rtl">
      <header>
        <h1 className="text-2xl font-black text-slate-950">مندوب المتجر — حسابات الموصلين</h1>
        <p className="mt-1 text-sm font-bold text-slate-500">اختر الموصل لعرض عهدته وأجرة توصيله، ومراجعة إيصالات الشبكة والتحويل الخاصة بطلباته.</p>
        <a href="/bank-transfer-review?workspace=store-delivery" className="mt-3 inline-block font-bold text-sky-800">فتح صفحة مراجعة تحصيلات جميع الموصلين</a>
      </header>
      <section className="max-w-2xl rounded-2xl border bg-white p-4">
        <label className="text-sm font-black">الموصل
          <select value={driverId} disabled={busy} onChange={(e) => { const id = e.target.value; setDriverId(id); load(id); }} className="mt-2 h-12 w-full rounded-xl border px-3 font-bold">
            <option value="">جميع الموصلين — مراجعة التحصيلات</option>
            {drivers.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — عهدة الكاش {money(row.cod_cash_custody)}</option>)}
          </select>
        </label>
      </section>
      {loading && <p role="status">جارٍ تحميل حساب الموصل…</p>}
      {summaryError && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-amber-900">{summaryError}</p>}
      {summary && <>
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-label="عهدة وأجرة الموصل">
          <Card label="كاش محصل" value={money(summary.cod_cash_collected)} Icon={CashRegister} />
          <Card label="توريدات أكدها الموصل" value={money(summary.cod_cash_remitted)} Icon={CashRegister} />
          <Card label="عهدة الكاش المتبقية" value={money(summary.cod_cash_custody)} Icon={CashRegister} />
          <Card label="أجرة التوصيل المتبقية" value={money(summary.delivery_earnings_due)} Icon={CurrencyCircleDollar} />
          <Card label="صافي مستحق من الموصل" value={money(summary.net_due_from_driver)} Icon={CashRegister} />
          <Card label="صافي مستحق للموصل" value={money(summary.net_due_to_driver)} Icon={CurrencyCircleDollar} />
        </section>
        {summary.noncash_payment_reviews && <section className="overflow-x-auto rounded-2xl border bg-white p-4">
          <h2 className="font-black">تحصيلات العملاء بالشبكة والتحويل</h2>
          <p className="mt-1 text-sm text-slate-600">هذه إجماليات الإيصالات حسب قرار المراجعة. المعلق والمرفوض لا يثبتان وصول المال، وإجمالي المعتمد ليس الرصيد الحالي للبنك أو الشبكة.</p>
          <table className="mt-3 w-full text-right text-sm" aria-label="إجماليات مراجعة التحصيل">
            <thead><tr><th className="p-2">طريقة الدفع</th><th className="p-2">بانتظار المراجعة</th><th className="p-2">معتمد</th><th className="p-2">مرفوض</th><th className="p-2">حالة غير متاحة</th></tr></thead>
            <tbody>{[["card_terminal", "شبكة / بطاقة"], ["bank_transfer", "تحويل بنكي"]].map(([method, label]) => <tr key={method} className="border-t">
              <th className="p-2">{label}</th>
              {["pending", "approved", "rejected", "unknown"].map(reviewStatus => {
                const value = summary.noncash_payment_reviews[method]?.[reviewStatus];
                return <td className="p-2" key={reviewStatus}>{money(value?.amount)}{value && <span className="block text-xs text-slate-500">{value.count} إيصال</span>}</td>;
              })}
            </tr>)}</tbody>
          </table>
          {["card_terminal", "bank_transfer"].some(method => summary.noncash_payment_reviews[method]?.unknown?.count > 0) &&
            <p role="alert" className="mt-3 text-amber-900">توجد إيصالات لم تتحدد حالة مراجعتها؛ لم تُحتسب ضمن المبالغ المعتمدة. راجع بيانات هذه الإيصالات قبل المطابقة.</p>}
        </section>}
        <section className="space-y-3 rounded-2xl border bg-white p-4" aria-label="طلبات تسوية عهدة الموصل">
          <h2 className="font-black">توريد عهدة الكاش وتسوية أجرة الموصل</h2>
          <p className="text-sm text-slate-600">إذا جمع الموصل كاشًا ثم حوّله للمتجر، فهذا توريد لعهدته. تأكيد الموصل لطلب التسوية لا يثبت وصول التحويل إلى بنك المتجر.</p>
          {!ledgerLinked && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">اعتماد توريد عهدة الموصل إلى البنك غير متاح من هذه الصفحة حاليًا. المبالغ أعلاه من سجل الموصل؛ طابق وصول البنك قبل إقفال العهدة ماليًا.</p>}
          <div className="grid gap-3 sm:grid-cols-3">
            <button disabled={busy || !ledgerLinked || !(summary.ledger_cod_receivable > 0)} onClick={() => create("cod-remittance")} className="rounded-2xl bg-slate-950 px-4 py-4 font-black text-white disabled:opacity-40">طلب توريد عهدة الكاش</button>
            <button disabled={busy || !ledgerLinked || !(summary.ledger_delivery_fee_payable > 0)} onClick={() => create("earning-payment")} className="rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">طلب دفع مستحقات التوصيل</button>
            <button disabled={busy || !ledgerLinked || !(summary.ledger_cod_receivable > 0 && summary.ledger_delivery_fee_payable > 0)} onClick={() => create("net-settlement")} className="rounded-2xl bg-amber-600 px-4 py-4 font-black text-white disabled:opacity-40">طلب مقاصة وصافي تسوية</button>
          </div>
        </section>
        <section className="space-y-2">
          <h2 className="font-black">سجل طلبات التسوية</h2>
          {items.map((row) => <article key={row.id} className="flex items-center justify-between rounded-2xl border bg-white p-4">
            <div>
              <div className="font-black">{row.settlement_type === "cod_remittance" ? "توريد عهدة الكاش" : row.settlement_type === "earning_payment" ? "دفع مستحق توصيل" : "مقاصة وصافي تسوية"}</div>
              <div className="mt-1 text-sm font-bold">{settlementStatus[row.status] || "حالة التسوية غير متاحة"}</div>
              <div className="mt-1 text-xs font-bold text-slate-500">{row.created_at} {row.account_name_snapshot ? `· ${row.account_name_snapshot}` : ""}</div>
              {row.driver_rejection_reason && <p className="mt-1 text-sm">سبب الرفض: {row.driver_rejection_reason}</p>}
              {row.settlement_type === "net_settlement" && <div className="mt-1 text-xs font-bold text-amber-700">العهدة ضمن الطلب: {money(row.cod_settled_amount)} · الأجرة ضمن الطلب: {money(row.delivery_fee_settled_amount)}</div>}
            </div>
            <div className="font-black" dir="ltr">{money(row.amount)}</div>
          </article>)}
          {!items.length && <div className="rounded-2xl border border-dashed p-5 text-center text-sm font-bold text-slate-500">لا توجد طلبات تسوية مسجلة.</div>}
        </section>
      </>}
      <StoreDeliveryPaymentReview key={driverId || "all-drivers"} driverId={driverId} embedded onReviewed={() => { if (driverId) load(driverId); }} />
    </main>
  );
}

function Card({ label, value, Icon }) {
  return <div className="rounded-2xl border bg-white p-4 shadow-sm"><div className="flex items-center gap-2 text-xs font-black text-slate-500"><Icon size={20} />{label}</div><div className="mt-2 text-2xl font-black" dir="ltr">{value}</div></div>;
}

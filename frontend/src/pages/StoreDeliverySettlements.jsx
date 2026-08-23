import { useEffect, useState } from "react";
import { CashRegister, CurrencyCircleDollar } from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR", maximumFractionDigits: 2 }).format(Number(value || 0));
}

export default function StoreDeliverySettlements() {
  const [drivers, setDrivers] = useState([]);
  const [driverId, setDriverId] = useState("");
  const [summary, setSummary] = useState(null);
  const [items, setItems] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [busy, setBusy] = useState(false);

  async function loadDrivers() {
    const response = await api.get("/store-delivery/settlements/drivers");
    setDrivers(response.data?.items || []);
  }

  useEffect(() => {
    Promise.all([loadDrivers(), api.get("/store-delivery/payment-review/bank-accounts")])
      .then(([, accountResponse]) => setAccounts(accountResponse.data?.items || []))
      .catch(() => toast.error("تعذر تحميل بيانات التسويات"));
  }, []);

  async function load(id = driverId) {
    if (!id) return;
    try {
      const response = await api.get(`/store-delivery/settlements/driver/${id}`);
      setSummary(response.data?.summary || null);
      setItems(response.data?.items || []);
    } catch (error) {
      toast.error(error?.response?.data?.detail?.code || "تعذر تحميل حساب الموصل");
    }
  }

  async function create(type) {
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
      const indexText = window.prompt(`اختر رقم الحساب البنكي الذي ستُسجل عليه الحركة:\n${names}`, "1");
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
      toast.success(type === "cod-remittance" ? "تم تسجيل توريد الكاش" : type === "earning-payment" ? "تم تسجيل دفع مستحق الموصل" : "تم تسجيل صافي التسوية مع إظهار المقاصة");
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
      <header><h1 className="text-2xl font-black text-slate-950">مندوب المتجر — حسابات الموصلين</h1><p className="mt-1 text-sm font-bold text-slate-500">«مندوب المتجر» مجموعة عرض فقط؛ كل موصل تحته له عهدة COD وأجرة توصيل ودفتر مدين/دائن مستقل.</p></header>
      <section className="max-w-2xl rounded-2xl border bg-white p-4"><label className="text-sm font-black">الموصل<select value={driverId} onChange={(e) => { const id = e.target.value; setDriverId(id); setSummary(null); setItems([]); if (id) load(id); }} className="mt-2 h-12 w-full rounded-xl border px-3 font-bold"><option value="">اختر الموصل</option>{drivers.map((row) => <option key={row.id} value={row.id}>{row.name} — {row.city} — الصافي {money(row.ledger_net_balance)}</option>)}</select></label></section>
      {summary && <><section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6"><Card label="كاش محصل" value={money(summary.cod_cash_collected)} Icon={CashRegister} /><Card label="تم توريده" value={money(summary.cod_cash_remitted)} Icon={CashRegister} /><Card label="عليه لنا (COD)" value={money(summary.ledger_cod_receivable)} Icon={CashRegister} /><Card label="له علينا (التوصيل)" value={money(summary.ledger_delivery_fee_payable)} Icon={CurrencyCircleDollar} /><Card label="صافي عليه لنا" value={money(Math.max(Number(summary.ledger_net_balance || 0), 0))} Icon={CashRegister} /><Card label="صافي له علينا" value={money(Math.max(-Number(summary.ledger_net_balance || 0), 0))} Icon={CurrencyCircleDollar} /></section><section className="grid gap-3 sm:grid-cols-3"><button disabled={busy || !(summary.ledger_cod_receivable > 0)} onClick={() => create("cod-remittance")} className="rounded-2xl bg-slate-950 px-4 py-4 font-black text-white disabled:opacity-40">تسجيل توريد COD</button><button disabled={busy || !(summary.ledger_delivery_fee_payable > 0)} onClick={() => create("earning-payment")} className="rounded-2xl bg-emerald-700 px-4 py-4 font-black text-white disabled:opacity-40">دفع مستحقات التوصيل</button><button disabled={busy || !(summary.ledger_cod_receivable > 0 && summary.ledger_delivery_fee_payable > 0)} onClick={() => create("net-settlement")} className="rounded-2xl bg-amber-600 px-4 py-4 font-black text-white disabled:opacity-40">تسجيل مقاصة وصافي تسوية</button></section><section className="space-y-2"><h2 className="font-black">سجل التسويات</h2>{items.map((row) => <article key={row.id} className="flex items-center justify-between rounded-2xl border bg-white p-4"><div><div className="font-black">{row.settlement_type === "cod_remittance" ? "توريد COD" : row.settlement_type === "earning_payment" ? "دفع مستحق توصيل" : "مقاصة وصافي تسوية"}</div><div className="mt-1 text-xs font-bold text-slate-500">{row.created_at} {row.account_name_snapshot ? `· ${row.account_name_snapshot}` : ""}</div>{row.settlement_type === "net_settlement" && <div className="mt-1 text-xs font-bold text-amber-700">COD مقفل: {money(row.cod_settled_amount)} · أجرة مخصومة: {money(row.delivery_fee_settled_amount)}</div>}</div><div className="font-black" dir="ltr">{money(row.amount)}</div></article>)}{!items.length && <div className="rounded-2xl border border-dashed p-5 text-center text-sm font-bold text-slate-500">لا توجد تسويات مسجلة.</div>}</section></>}
    </main>
  );
}

function Card({ label, value, Icon }) {
  return <div className="rounded-2xl border bg-white p-4 shadow-sm"><div className="flex items-center gap-2 text-xs font-black text-slate-500"><Icon size={20} />{label}</div><div className="mt-2 text-2xl font-black" dir="ltr">{value}</div></div>;
}

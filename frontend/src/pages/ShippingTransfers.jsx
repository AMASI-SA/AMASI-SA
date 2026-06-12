/**
 * Iter-144 — Two-way courier transfers ledger.
 *
 * • courier_to_bank — money received FROM the courier (e.g. weekly COD settlement)
 * • bank_to_courier — money we paid TO the courier (e.g. monthly shipping fees)
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { todaySA } from "../lib/dates";


const fmt = (v) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });


export default function ShippingTransfers() {
    const [items, setItems] = useState([]);
    const [banks, setBanks] = useState([]);
    const [companies, setCompanies] = useState([]);
    const [form, setForm] = useState({
        company_name: "", direction: "courier_to_bank",
        amount: "", transfer_date: todaySA(),
        bank_account_id: "", reference: "", note: "",
    });
    const [busy, setBusy] = useState(false);

    const reload = () => {
        api.get("/shipping-accounts/transfers")
            .then(({ data }) => setItems(data.items || []))
            .catch(() => { });
    };

    useEffect(() => {
        reload();
        api.get("/accounts").then(({ data }) => setBanks(data || [])).catch(() => { });
        api.get("/shipping-accounts/ledger").then(({ data }) => setCompanies(data.companies || [])).catch(() => { });
    }, []);

    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

    // Iter-152 — Live "owed" + "available" hints under the form.
    const selectedCompany = companies.find((c) => c.name === form.company_name);
    const companyNet = Number(selectedCompany?.net_balance || 0);
    const owedToUs = companyNet > 0 ? companyNet : 0;          // courier owes us
    const owedToCourier = companyNet < 0 ? -companyNet : 0;    // we owe courier
    const selectedBank = banks.find((b) => b.id === form.bank_account_id);
    const bankBalance = Number(selectedBank?.current_balance || 0);
    const amtNum = Number(form.amount || 0);

    let inlineWarning = null;
    if (form.direction === "courier_to_bank" && selectedCompany) {
        if (owedToUs <= 0) {
            inlineWarning = `لا توجد مديونية حالية على «${form.company_name}» — لا يمكن استلام تحويل.`;
        } else if (amtNum > owedToUs + 0.01) {
            inlineWarning = `المبلغ (${fmt(amtNum)} ر.س) أكبر من المستحق على الشركة (${fmt(owedToUs)} ر.س).`;
        }
    }
    if (form.direction === "bank_to_courier") {
        if (selectedBank && bankBalance + 0.01 < amtNum) {
            inlineWarning = `رصيد «${selectedBank.name}» غير كافٍ — المتاح ${fmt(bankBalance)} ر.س فقط.`;
        } else if (selectedCompany && amtNum > owedToCourier + 0.01) {
            const over = amtNum - owedToCourier;
            inlineWarning = (owedToCourier > 0
                ? `تنبيه: المبلغ يتجاوز المستحق للشركة بـ ${fmt(over)} ر.س — سيتم اعتبار الفرق مديونية للشركة لصالحك.`
                : `تنبيه: لا توجد مديونية حالياً على «${form.company_name}» — سيتم تسجيل التحويل كمبلغ مدفوع زيادة (${fmt(amtNum)} ر.س) ستصبح مديونية للشركة لصالحك.`);
        }
    }
    const blocksSubmit =
        (form.direction === "courier_to_bank"
            && selectedCompany
            && (owedToUs <= 0 || amtNum > owedToUs + 0.01))
        || (form.direction === "bank_to_courier"
            && selectedBank
            && bankBalance + 0.01 < amtNum);

    const submit = async () => {
        if (!form.company_name.trim()) return toast.error("اختر شركة الشحن");
        if (!Number(form.amount)) return toast.error("أدخل المبلغ");
        setBusy(true);
        try {
            const { data } = await api.post("/shipping-accounts/transfers", {
                ...form,
                amount: Number(form.amount),
                bank_account_id: form.bank_account_id || null,
            });
            if (data?.overpayment_note) {
                toast.success(data.overpayment_note, { duration: 7000 });
            } else {
                toast.success("تم تسجيل التحويل");
            }
            setForm({ ...form, amount: "", reference: "", note: "" });
            reload();
            // Refresh the companies ledger so the inline hint updates.
            api.get("/shipping-accounts/ledger").then(({ data }) => setCompanies(data.companies || [])).catch(() => { });
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    const del = async (id) => {
        if (!window.confirm("حذف هذا التحويل؟ (سيُعكس أثره على البنك)")) return;
        try {
            await api.delete(`/shipping-accounts/transfers/${id}`);
            toast.success("تم الحذف");
            reload();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        }
    };

    return (
        <div className="p-6 max-w-6xl mx-auto space-y-5" dir="rtl" data-testid="shipping-transfers-page">
            <header className="flex items-center justify-between gap-2 flex-wrap">
                <h1 className="text-2xl font-extrabold text-slate-900">📒 تحويلات شركات الشحن</h1>
                <Link to="/shipping/ledger" className="px-3 py-2 bg-slate-200 hover:bg-slate-300 text-slate-800 text-sm font-bold rounded-lg" data-testid="transfers-back">
                    ← الأرصدة
                </Link>
            </header>

            {/* Form */}
            <div className="bg-white border-2 border-slate-200 rounded-xl p-4 grid grid-cols-1 md:grid-cols-3 gap-3" data-testid="transfer-form">
                <div>
                    <label className="text-xs text-slate-600 mb-1 block">شركة الشحن</label>
                    <select value={form.company_name} onChange={(e) => set("company_name", e.target.value)} className="w-full border border-slate-300 rounded-lg px-2 py-2 text-sm" data-testid="tx-company">
                        <option value="">— اختر —</option>
                        {companies.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                    </select>
                </div>
                <div>
                    <label className="text-xs text-slate-600 mb-1 block">الاتجاه</label>
                    <div className="grid grid-cols-2 gap-1" role="radiogroup">
                        <button type="button" onClick={() => set("direction", "courier_to_bank")} className={`px-3 py-2 rounded-lg text-xs font-bold ${form.direction === "courier_to_bank" ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-700"}`} data-testid="tx-dir-in">↓ من الشركة للبنك</button>
                        <button type="button" onClick={() => set("direction", "bank_to_courier")} className={`px-3 py-2 rounded-lg text-xs font-bold ${form.direction === "bank_to_courier" ? "bg-rose-600 text-white" : "bg-slate-100 text-slate-700"}`} data-testid="tx-dir-out">↑ من البنك للشركة</button>
                    </div>
                </div>
                <div>
                    <label className="text-xs text-slate-600 mb-1 block">المبلغ (ر.س)</label>
                    <input type="number" step="0.01" min="0" value={form.amount} onChange={(e) => set("amount", e.target.value)} className="w-full border border-slate-300 rounded-lg px-2 py-2 text-sm num" data-testid="tx-amount" />
                </div>
                <div>
                    <label className="text-xs text-slate-600 mb-1 block">التاريخ</label>
                    <input type="date" value={form.transfer_date} onChange={(e) => set("transfer_date", e.target.value)} className="w-full border border-slate-300 rounded-lg px-2 py-2 text-sm" data-testid="tx-date" />
                </div>
                <div>
                    <label className="text-xs text-slate-600 mb-1 block">الحساب البنكي</label>
                    <select value={form.bank_account_id} onChange={(e) => set("bank_account_id", e.target.value)} className="w-full border border-slate-300 rounded-lg px-2 py-2 text-sm" data-testid="tx-bank">
                        <option value="">— بدون ربط —</option>
                        {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({fmt(b.current_balance)} ر.س)</option>)}
                    </select>
                </div>
                <div>
                    <label className="text-xs text-slate-600 mb-1 block">المرجع/الفاتورة</label>
                    <input value={form.reference} onChange={(e) => set("reference", e.target.value)} className="w-full border border-slate-300 rounded-lg px-2 py-2 text-sm" data-testid="tx-reference" />
                </div>
                <div className="md:col-span-3">
                    <label className="text-xs text-slate-600 mb-1 block">ملاحظات</label>
                    <input value={form.note} onChange={(e) => set("note", e.target.value)} className="w-full border border-slate-300 rounded-lg px-2 py-2 text-sm" data-testid="tx-note" />
                </div>
                <div className="md:col-span-3 text-left flex items-center justify-between gap-3 flex-wrap">
                    {/* Iter-152 — live hints */}
                    <div className="flex items-center gap-2 flex-wrap">
                        {selectedCompany && form.direction === "courier_to_bank" && (
                            <span className="text-[11px] px-2 py-1 bg-emerald-50 text-emerald-700 rounded-lg font-bold" data-testid="hint-owed-to-us">
                                المستحق علينا من «{form.company_name}»: <span className="num">{fmt(owedToUs)}</span> ر.س
                            </span>
                        )}
                        {selectedCompany && form.direction === "bank_to_courier" && (
                            <span className="text-[11px] px-2 py-1 bg-rose-50 text-rose-700 rounded-lg font-bold" data-testid="hint-owed-to-courier">
                                المستحق علينا لـ «{form.company_name}»: <span className="num">{fmt(owedToCourier)}</span> ر.س
                            </span>
                        )}
                        {selectedBank && (
                            <span className="text-[11px] px-2 py-1 bg-sky-50 text-sky-700 rounded-lg font-bold" data-testid="hint-bank-balance">
                                رصيد «{selectedBank.name}»: <span className="num">{fmt(bankBalance)}</span> ر.س
                            </span>
                        )}
                        {inlineWarning && (
                            <span className={`text-[11px] px-2 py-1 rounded-lg font-bold ${blocksSubmit ? "bg-rose-100 text-rose-800" : "bg-amber-100 text-amber-800"}`} data-testid="tx-inline-warning">
                                {blocksSubmit ? "⛔ " : "⚠️ "}{inlineWarning}
                            </span>
                        )}
                    </div>
                    <button onClick={submit} disabled={busy || blocksSubmit} className="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-bold rounded-lg disabled:opacity-50 disabled:cursor-not-allowed" data-testid="tx-submit">
                        {busy ? "جاري الحفظ…" : "تسجيل التحويل"}
                    </button>
                </div>
            </div>

            {/* List */}
            <div className="bg-white border-2 border-slate-200 rounded-xl overflow-hidden">
                <table className="w-full text-xs" data-testid="transfers-table">
                    <thead className="bg-slate-50 text-slate-700">
                        <tr>
                            <th className="p-2 text-right">التاريخ</th>
                            <th className="p-2 text-right">الشركة</th>
                            <th className="p-2 text-right">الاتجاه</th>
                            <th className="p-2 num text-right">المبلغ</th>
                            <th className="p-2 text-right">البنك</th>
                            <th className="p-2 text-right">المرجع</th>
                            <th className="p-2 text-right">ملاحظات</th>
                            <th className="p-2 text-right"></th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.length === 0 && (
                            <tr><td colSpan={8} className="p-4 text-center text-slate-500">لا توجد تحويلات بعد.</td></tr>
                        )}
                        {items.map((it) => {
                            const isIn = it.direction === "courier_to_bank";
                            return (
                                <tr key={it.id} className="border-t border-slate-100" data-testid={`tx-row-${it.id}`}>
                                    <td className="p-2 num text-slate-700">{it.transfer_date}</td>
                                    <td className="p-2 font-bold">{it.company_name}</td>
                                    <td className="p-2"><span className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold ${isIn ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"}`}>{isIn ? "↓ وارد" : "↑ صادر"}</span></td>
                                    <td className={`p-2 num font-bold ${isIn ? "text-emerald-700" : "text-rose-700"}`}>{isIn ? "+" : "−"}{fmt(it.amount)}</td>
                                    <td className="p-2 text-slate-600">{banks.find((b) => b.id === it.bank_account_id)?.name || "—"}</td>
                                    <td className="p-2 text-slate-600 text-[11px]">{it.reference || "—"}</td>
                                    <td className="p-2 text-slate-600 text-[11px]">{it.note || "—"}</td>
                                    <td className="p-2"><button onClick={() => del(it.id)} className="text-rose-600 hover:text-rose-800 text-xs font-bold" data-testid={`tx-del-${it.id}`}>حذف</button></td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

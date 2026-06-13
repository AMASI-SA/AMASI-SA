// Iter-161 Phase 2 — Unified Financial Entry Screen
//
// ONE input gateway for every financial movement in the app.
// Each operation produces a balanced txn_group in general_ledger
// via the /api/accounting/* endpoints. No direct mutation of stored
// balances, no DELETE — corrections only via reverse / settlement /
// writeoff / adjustment on the resulting ledger entry.

import React, { useState, useEffect, useMemo } from "react";
import api from "../lib/api";
import { toast } from "sonner";

const OP_TYPES = [
    { value: "advance_grant",        label: "💰 سلفة موظف",         section: "employees" },
    { value: "salary_settle",        label: "📅 صرف راتب",            section: "employees" },
    { value: "salary_accrual",       label: "📝 استحقاق راتب شهري",   section: "employees" },
    { value: "custody_grant",        label: "🎒 تسليم عهدة",           section: "employees" },
    { value: "custody_return",       label: "🔙 إرجاع عهدة نقداً",     section: "employees" },
    { value: "custody_settle",       label: "🧾 تسوية عهدة بفواتير",  section: "employees" },
    { value: "supplier_invoice",     label: "📄 فاتورة مورد",          section: "suppliers" },
    { value: "supplier_pay",         label: "💸 سداد مورد",            section: "suppliers" },
    { value: "external_grant",       label: "🤝 سلفة لشخص خارجي",    section: "externals" },
    { value: "external_collect",     label: "💵 تحصيل من شخص خارجي",  section: "externals" },
    { value: "expense_record",       label: "🛒 مصروف عام",             section: "general" },
    { value: "bank_transfer",        label: "🔄 تحويل بين الحسابات",   section: "general" },
];

export default function UnifiedEntryScreen() {
    const [opType, setOpType] = useState("");
    const [busy, setBusy] = useState(false);
    const [employees, setEmployees] = useState([]);
    const [suppliers, setSuppliers] = useState([]);

    // Iter-173 — Smart source-account filter based on the operation type.
    // Cash outflows (sending money to an employee/supplier/external/expense)
    // can ONLY be funded from a real cash account: a bank or a manual
    // cash account. Pulling them from Salla/Tamara/Tabby/Imkan/COD is
    // accounting nonsense — those balances represent money STILL HELD
    // by the payment platforms, not money in the merchant's wallet.
    //
    // Returns the allowed account_type list for the «خصم من حساب»
    // dropdown. `null` means «show all groups» (used by bank_transfer
    // since transfers between platforms ARE legitimate).
    const allowedSourceAccountTypes = (op) => {
        const cashOut = [
            "advance_grant", "salary_settle", "custody_grant",
            "supplier_pay", "external_grant", "expense_record",
        ];
        const cashIn = [
            "custody_return", "external_collect",
        ];
        if (cashOut.includes(op) || cashIn.includes(op)) {
            return ["bank", "cash"];
        }
        // bank_transfer + any future settlement op → allow everything
        return null;
    };
    const [externals, setExternals] = useState([]);
    const [banks, setBanks] = useState([]);
    const [categories, setCategories] = useState([]);
    const [result, setResult] = useState(null);

    // Form fields (used by various ops)
    const [entityId, setEntityId] = useState("");
    const [amount, setAmount] = useState("");
    const [bankId, setBankId] = useState("");
    const [bankToId, setBankToId] = useState("");
    const [paymentDate, setPaymentDate] = useState(
        () => new Date().toISOString().slice(0, 10));
    const [notes, setNotes] = useState("");
    const [period, setPeriod] = useState(
        () => new Date().toISOString().slice(0, 7));
    const [expCategory, setExpCategory] = useState("");
    const [invoiceNo, setInvoiceNo] = useState("");
    const [applyAdvances, setApplyAdvances] = useState(true);
    const [custodyItems, setCustodyItems] = useState([
        { expense_category: "", amount: "", notes: "" },
    ]);

    useEffect(() => { (async () => {
        try {
            const [emp, sup, ext, bnk, cat] = await Promise.all([
                api.get("/operating-expenses/salaries"),
                api.get("/counterparties?kind=supplier&limit=500"),
                api.get("/counterparties?limit=500"),
                api.get("/accounts?account_type=bank&limit=200"),
                api.get("/accounting/expense-categories"),
            ]);
            setEmployees((emp.data?.items || emp.data || []).filter(
                e => (e.category || "employee") === "employee"));
            setSuppliers(sup.data?.items || sup.data || []);
            setExternals((ext.data?.items || ext.data || []).filter(
                x => !["ad_account", "supplier", "courier"].includes(x.kind)));
            // Iter-170 — Fix duplicate accounts in dropdown:
            // Backend originally ignored ?type= (it expects ?account_type=).
            // Result: both queries below returned ALL accounts → merge
            // produced 2× every account in the "خصم من حساب" picker.
            // We now use account_type AND deduplicate by id as a defense
            // in depth. The accounts are also tagged for grouping.
            const bankItems = bnk.data?.items || bnk.data || [];
            const seen = new Set();
            const dedupe = (list) => list.filter((a) => {
                if (!a?.id || seen.has(a.id)) return false;
                seen.add(a.id);
                return true;
            });
            let allAccounts = dedupe(bankItems);
            try {
                const pp = await api.get("/accounts?account_type=payment_platform&limit=200");
                const ppItems = pp.data?.items || pp.data || [];
                allAccounts = [...allAccounts, ...dedupe(ppItems)];
            } catch (e) { /* optional */ }
            setBanks(allAccounts);
            setCategories(cat.data || []);
            if (cat.data?.length) setExpCategory(cat.data[0].code);
        } catch (e) {
            toast.error("فشل تحميل البيانات الأساسية");
        }
    })(); }, []);

    const resetForm = () => {
        setEntityId(""); setAmount(""); setBankId(""); setBankToId("");
        setNotes(""); setInvoiceNo(""); setResult(null);
        setCustodyItems([{ expense_category: "", amount: "", notes: "" }]);
    };

    useEffect(() => { resetForm(); }, [opType]);

    // Preview computation — show the entries that will be created
    const previewEntries = useMemo(() => {
        const amt = Number(amount) || 0;
        if (!opType || amt <= 0) return null;
        const empName = employees.find(x => x.id === entityId)?.name;
        const supName = suppliers.find(x => x.id === entityId)?.name;
        const extName = externals.find(x => x.id === entityId)?.name;
        const bankName = banks.find(x => x.id === bankId)?.name;
        const bankToName = banks.find(x => x.id === bankToId)?.name;
        const catName = categories.find(x => x.code === expCategory)?.name;

        const e = []; // { debit/credit account label, amount }
        switch (opType) {
            case "advance_grant":
                e.push({ side: "debit",  acc: `سلفة ${empName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "salary_settle":
                e.push({ side: "debit",  acc: `راتب مستحق ${empName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt, note: "(قد تختلف القسمة الفعلية حسب الأرصدة)" });
                break;
            case "salary_accrual":
                e.push({ side: "debit",  acc: "مصروف رواتب",  amt });
                e.push({ side: "credit", acc: `راتب مستحق ${empName || ""}`, amt });
                break;
            case "custody_grant":
                e.push({ side: "debit",  acc: `عهدة ${empName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "custody_return":
                e.push({ side: "debit",  acc: bankName,    amt });
                e.push({ side: "credit", acc: `عهدة ${empName || ""}`, amt });
                break;
            case "custody_settle": {
                const total = custodyItems.reduce((s, it) => s + (Number(it.amount) || 0), 0);
                for (const it of custodyItems) {
                    const cAmt = Number(it.amount) || 0;
                    if (cAmt > 0) {
                        const cName = categories.find(c => c.code === it.expense_category)?.name;
                        e.push({ side: "debit", acc: `مصروف: ${cName || it.expense_category}`, amt: cAmt });
                    }
                }
                if (total > 0) {
                    e.push({ side: "credit", acc: `عهدة ${empName || ""}`, amt: total });
                }
                break;
            }
            case "supplier_invoice":
                e.push({ side: "debit",  acc: `مصروف: ${catName || ""}`, amt });
                e.push({ side: "credit", acc: `مورد ${supName || ""}`,    amt });
                break;
            case "supplier_pay":
                e.push({ side: "debit",  acc: `مورد ${supName || ""}`,    amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "external_grant":
                e.push({ side: "debit",  acc: `قرض/مستحق على ${extName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "external_collect":
                e.push({ side: "debit",  acc: bankName,    amt });
                e.push({ side: "credit", acc: `قرض/مستحق على ${extName || ""}`, amt });
                break;
            case "expense_record":
                e.push({ side: "debit",  acc: `مصروف: ${catName || ""}`, amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
            case "bank_transfer":
                e.push({ side: "debit",  acc: bankToName,  amt });
                e.push({ side: "credit", acc: bankName,    amt });
                break;
        }
        const debit = e.filter(x => x.side === "debit").reduce((s, x) => s + x.amt, 0);
        const credit = e.filter(x => x.side === "credit").reduce((s, x) => s + x.amt, 0);
        return { entries: e, debit, credit, balanced: Math.abs(debit - credit) < 0.01 };
    }, [opType, amount, entityId, bankId, bankToId, expCategory, custodyItems,
        employees, suppliers, externals, banks, categories]);

    const submit = async () => {
        const amt = Number(amount) || 0;
        if (opType !== "custody_settle" && amt <= 0) {
            toast.error("أدخل مبلغاً صحيحاً"); return;
        }
        setBusy(true); setResult(null);
        try {
            let url = ""; let body = {};
            const common = { payment_date: paymentDate, notes };
            switch (opType) {
                case "advance_grant":
                    url = `/accounting/employees/${entityId}/advances`;
                    body = { amount: amt, paid_from_account_id: bankId, ...common };
                    break;
                case "salary_settle":
                    url = `/accounting/employees/${entityId}/settle`;
                    body = { amount: amt, paid_from_account_id: bankId,
                             apply_open_advances: applyAdvances, ...common };
                    break;
                case "salary_accrual":
                    url = `/accounting/employees/${entityId}/salary-accrual`;
                    body = { amount: amt, period, notes };
                    break;
                case "custody_grant":
                    url = `/accounting/employees/${entityId}/custody`;
                    body = { amount: amt, paid_from_account_id: bankId, ...common };
                    break;
                case "custody_return":
                    url = `/accounting/employees/${entityId}/custody/return`;
                    body = { amount: amt, deposited_to_account_id: bankId, ...common };
                    break;
                case "custody_settle":
                    url = `/accounting/employees/${entityId}/custody/settle-with-receipts`;
                    body = {
                        items: custodyItems.filter(it => Number(it.amount) > 0)
                            .map(it => ({
                                expense_category: it.expense_category,
                                amount: Number(it.amount),
                                notes: it.notes || "",
                            })),
                        ...common,
                    };
                    break;
                case "supplier_invoice":
                    url = `/accounting/suppliers/${entityId}/invoice`;
                    body = { amount: amt, expense_category: expCategory,
                             invoice_no: invoiceNo, ...common };
                    break;
                case "supplier_pay":
                    url = `/accounting/suppliers/${entityId}/pay`;
                    body = { amount: amt, paid_from_account_id: bankId, ...common };
                    break;
                case "external_grant":
                    url = `/accounting/external-persons/${entityId}/grant`;
                    body = { amount: amt, paid_from_account_id: bankId, ...common };
                    break;
                case "external_collect":
                    url = `/accounting/external-persons/${entityId}/collect`;
                    body = { amount: amt, deposited_to_account_id: bankId, ...common };
                    break;
                case "expense_record":
                    url = "/accounting/expenses";
                    body = { amount: amt, expense_category: expCategory,
                             paid_from_account_id: bankId, ...common };
                    break;
                case "bank_transfer":
                    url = "/accounting/bank-transfer";
                    body = { amount: amt, from_account_id: bankId,
                             to_account_id: bankToId, ...common };
                    break;
                default:
                    toast.error("اختر نوع العملية"); setBusy(false); return;
            }
            const { data } = await api.post(url, body);
            toast.success("تم اعتماد القيد بنجاح");
            setResult(data);
        } catch (e) {
            const det = e.response?.data?.detail;
            toast.error(typeof det === "string" ? det : (det?.[0]?.msg || "فشل الاعتماد"));
        } finally { setBusy(false); }
    };

    const op = OP_TYPES.find(x => x.value === opType);
    const needsEmployee = ["advance_grant","salary_settle","salary_accrual",
        "custody_grant","custody_return","custody_settle"].includes(opType);
    const needsSupplier = ["supplier_invoice","supplier_pay"].includes(opType);
    const needsExternal = ["external_grant","external_collect"].includes(opType);
    const needsBank = !["salary_accrual","custody_settle","supplier_invoice"].includes(opType);
    const needsBankTo = opType === "bank_transfer";
    const needsCategory = ["supplier_invoice","expense_record"].includes(opType);

    return (
        <div className="p-6 max-w-4xl mx-auto" data-testid="unified-entry-screen">
            <div className="bg-white rounded-2xl shadow-lg p-6">
                <h1 className="text-2xl font-extrabold text-slate-900 mb-1">
                    ➕ حركة مالية جديدة
                </h1>
                <p className="text-sm text-slate-500 mb-6">
                    شاشة موحدة لكل العمليات المحاسبية. ينشئ النظام القيود المزدوجة تلقائياً.
                </p>

                {/* Op type selector */}
                <div className="mb-6">
                    <label className="block text-sm font-bold text-slate-700 mb-2">
                        نوع العملية:
                    </label>
                    <select value={opType} onChange={e => setOpType(e.target.value)}
                        className="w-full px-3 py-2.5 border-2 border-slate-300 rounded-lg text-sm font-bold focus:border-emerald-500 focus:outline-none"
                        data-testid="unified-op-type">
                        <option value="">— اختر نوع العملية —</option>
                        <optgroup label="موظفون">
                            {OP_TYPES.filter(x => x.section === "employees").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="موردون">
                            {OP_TYPES.filter(x => x.section === "suppliers").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="أشخاص خارجيون">
                            {OP_TYPES.filter(x => x.section === "externals").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                        <optgroup label="عمليات عامة">
                            {OP_TYPES.filter(x => x.section === "general").map(o =>
                                <option key={o.value} value={o.value}>{o.label}</option>)}
                        </optgroup>
                    </select>
                </div>

                {opType && (
                    <div className="space-y-4 border-t border-slate-200 pt-4">
                        {/* Entity */}
                        {needsEmployee && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">الموظف:</label>
                                <select value={entityId} onChange={e => setEntityId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-entity-employee">
                                    <option value="">— اختر —</option>
                                    {employees.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
                                </select>
                            </div>
                        )}
                        {needsSupplier && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">المورد:</label>
                                <select value={entityId} onChange={e => setEntityId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-entity-supplier">
                                    <option value="">— اختر —</option>
                                    {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                                </select>
                            </div>
                        )}
                        {needsExternal && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">الشخص:</label>
                                <select value={entityId} onChange={e => setEntityId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-entity-external">
                                    <option value="">— اختر —</option>
                                    {externals.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
                                </select>
                            </div>
                        )}
                        {needsCategory && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">فئة المصاريف:</label>
                                <select value={expCategory} onChange={e => setExpCategory(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-expense-category">
                                    {categories.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                                </select>
                            </div>
                        )}

                        {/* Amount (or items for custody settle) */}
                        {opType !== "custody_settle" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">المبلغ (ر.س):</label>
                                <input type="number" step="0.01" value={amount}
                                    onChange={e => setAmount(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-amount" />
                            </div>
                        )}

                        {/* Bank */}
                        {needsBank && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">
                                    {opType === "custody_return" || opType === "external_collect"
                                        ? "إيداع في حساب:"
                                        : opType === "bank_transfer" ? "من حساب:" : "خصم من حساب:"}
                                </label>
                                {/* Iter-173 — smart filter; show a hint when restricted */}
                                {allowedSourceAccountTypes(opType) && (
                                    <div className="text-[11px] text-slate-500 mb-1 leading-tight">
                                        💡 هذه العملية صرف نقدي حقيقي — متاحة فقط من البنوك والصندوق
                                        (لا تظهر بوابات الدفع ولا الـ COD لأن أرصدتها مُحتجَزة لدى المنصة).
                                    </div>
                                )}
                                <select value={bankId} onChange={e => setBankId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-bank">
                                    <option value="">— اختر —</option>
                                    <optgroup label="🏦 الحسابات البنكية">
                                        {banks.filter(b => b.account_type === "bank").map(b =>
                                            <option key={b.id} value={b.id}>{b.name} · بنك</option>)}
                                    </optgroup>
                                    {banks.some(b => b.account_type === "cash") && (
                                        <optgroup label="💵 الصندوق النقدي">
                                            {banks.filter(b => b.account_type === "cash").map(b =>
                                                <option key={b.id} value={b.id}>{b.name} · صندوق</option>)}
                                        </optgroup>
                                    )}
                                    {(!allowedSourceAccountTypes(opType)) && (
                                        <>
                                            <optgroup label="💳 بوابات الدفع">
                                                {banks.filter(b => b.account_type === "payment_platform" && !/الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b =>
                                                    <option key={b.id} value={b.id}>{b.name} · بوابة دفع</option>)}
                                            </optgroup>
                                            {banks.some(b => b.account_type === "payment_platform" && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")) && (
                                                <optgroup label="💵 الدفع عند الاستلام">
                                                    {banks.filter(b => b.account_type === "payment_platform" && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b =>
                                                        <option key={b.id} value={b.id}>{b.name} · COD</option>)}
                                                </optgroup>
                                            )}
                                            {banks.some(b => b.account_type === "courier") && (
                                                <optgroup label="📦 شركات الشحن">
                                                    {banks.filter(b => b.account_type === "courier").map(b =>
                                                        <option key={b.id} value={b.id}>{b.name} · شركة شحن</option>)}
                                                </optgroup>
                                            )}
                                            {banks.some(b => b.account_type && !["bank","cash","payment_platform","courier"].includes(b.account_type)) && (
                                                <optgroup label="📁 أخرى">
                                                    {banks.filter(b => b.account_type && !["bank","cash","payment_platform","courier"].includes(b.account_type)).map(b =>
                                                        <option key={b.id} value={b.id}>{b.name} · {b.account_type}</option>)}
                                                </optgroup>
                                            )}
                                        </>
                                    )}
                                </select>
                            </div>
                        )}
                        {needsBankTo && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">إلى حساب:</label>
                                <select value={bankToId} onChange={e => setBankToId(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-bank-to">
                                    <option value="">— اختر —</option>
                                    <optgroup label="🏦 الحسابات البنكية">
                                        {banks.filter(b => b.account_type === "bank" && b.id !== bankId).map(b =>
                                            <option key={b.id} value={b.id}>{b.name} · بنك</option>)}
                                    </optgroup>
                                    <optgroup label="💳 بوابات الدفع">
                                        {banks.filter(b => b.account_type === "payment_platform" && b.id !== bankId && !/الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b =>
                                            <option key={b.id} value={b.id}>{b.name} · بوابة دفع</option>)}
                                    </optgroup>
                                    {banks.some(b => b.account_type === "payment_platform" && b.id !== bankId && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")) && (
                                        <optgroup label="💵 الدفع عند الاستلام">
                                            {banks.filter(b => b.account_type === "payment_platform" && b.id !== bankId && /الدفع\s*عند\s*الاستلام|cash[\s_-]*on[\s_-]*delivery|COD/i.test(b.name || "")).map(b =>
                                                <option key={b.id} value={b.id}>{b.name} · COD</option>)}
                                        </optgroup>
                                    )}
                                </select>
                            </div>
                        )}

                        {/* Salary accrual: period */}
                        {opType === "salary_accrual" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">الفترة (YYYY-MM):</label>
                                <input type="month" value={period}
                                    onChange={e => setPeriod(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm"
                                    data-testid="unified-period" />
                            </div>
                        )}

                        {/* Custody receipts */}
                        {opType === "custody_settle" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-2">بنود الفواتير:</label>
                                {custodyItems.map((it, idx) => (
                                    <div key={idx} className="flex gap-2 items-start mb-2"
                                         data-testid={`unified-custody-item-${idx}`}>
                                        <select value={it.expense_category}
                                            onChange={e => {
                                                const copy = [...custodyItems];
                                                copy[idx].expense_category = e.target.value;
                                                setCustodyItems(copy);
                                            }}
                                            className="px-2 py-1.5 border border-slate-300 rounded text-xs flex-1">
                                            <option value="">— الفئة —</option>
                                            {categories.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                                        </select>
                                        <input type="number" step="0.01" placeholder="المبلغ"
                                            value={it.amount}
                                            onChange={e => {
                                                const copy = [...custodyItems];
                                                copy[idx].amount = e.target.value;
                                                setCustodyItems(copy);
                                            }}
                                            className="w-28 px-2 py-1.5 border border-slate-300 rounded text-xs" />
                                        <input type="text" placeholder="ملاحظات"
                                            value={it.notes}
                                            onChange={e => {
                                                const copy = [...custodyItems];
                                                copy[idx].notes = e.target.value;
                                                setCustodyItems(copy);
                                            }}
                                            className="flex-1 px-2 py-1.5 border border-slate-300 rounded text-xs" />
                                        {custodyItems.length > 1 && (
                                            <button onClick={() => setCustodyItems(
                                                custodyItems.filter((_, i) => i !== idx))}
                                                className="text-rose-600 hover:text-rose-800 text-sm px-2">×</button>
                                        )}
                                    </div>
                                ))}
                                <button onClick={() => setCustodyItems(
                                    [...custodyItems, { expense_category: "", amount: "", notes: "" }])}
                                    className="text-xs text-emerald-700 hover:text-emerald-900 font-bold"
                                    data-testid="unified-add-custody-item">
                                    + إضافة بند
                                </button>
                            </div>
                        )}

                        {opType === "supplier_invoice" && (
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">رقم الفاتورة (اختياري):</label>
                                <input type="text" value={invoiceNo}
                                    onChange={e => setInvoiceNo(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm" />
                            </div>
                        )}

                        {opType === "salary_settle" && (
                            <label className="flex items-center gap-2 text-sm text-slate-700">
                                <input type="checkbox" checked={applyAdvances}
                                    onChange={e => setApplyAdvances(e.target.checked)}
                                    data-testid="unified-apply-advances" />
                                خصم السلف المفتوحة تلقائياً
                            </label>
                        )}

                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">التاريخ:</label>
                                <input type="date" value={paymentDate}
                                    onChange={e => setPaymentDate(e.target.value)}
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-bold text-slate-700 mb-1">ملاحظات:</label>
                                <input type="text" value={notes}
                                    onChange={e => setNotes(e.target.value)}
                                    placeholder="اختياري"
                                    className="w-full px-3 py-2 border border-slate-300 rounded text-sm" />
                            </div>
                        </div>

                        {/* Preview */}
                        {previewEntries && previewEntries.entries.length > 0 && (
                            <div className="bg-slate-50 border-2 border-slate-300 rounded-lg p-4 mt-4"
                                 data-testid="unified-preview">
                                <div className="text-sm font-bold text-slate-700 mb-2">
                                    📋 معاينة القيود المحاسبية:
                                </div>
                                <table className="w-full text-xs">
                                    <thead>
                                        <tr className="text-slate-500 border-b border-slate-200">
                                            <th className="text-right py-1">الجانب</th>
                                            <th className="text-right py-1">الحساب</th>
                                            <th className="text-left py-1">المبلغ</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {previewEntries.entries.map((e, i) => (
                                            <tr key={i} className="border-b border-slate-100">
                                                <td className={`py-1 font-bold ${e.side === "debit" ? "text-emerald-700" : "text-rose-700"}`}>
                                                    {e.side === "debit" ? "مدين" : "دائن"}
                                                </td>
                                                <td className="py-1">{e.acc}</td>
                                                <td className="text-left py-1 num font-bold">
                                                    {e.amt.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                                </td>
                                            </tr>
                                        ))}
                                        <tr className="font-bold border-t-2 border-slate-300">
                                            <td colSpan="2" className="py-1 text-left">الإجمالي:</td>
                                            <td className={`py-1 text-left num ${previewEntries.balanced ? "text-emerald-700" : "text-rose-700"}`}>
                                                {previewEntries.balanced ? "متوازن ✓" : "غير متوازن ✗"}
                                            </td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        )}

                        <div className="flex gap-2 pt-2">
                            <button onClick={submit} disabled={busy}
                                className="flex-1 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-bold rounded-lg disabled:opacity-50"
                                data-testid="unified-submit">
                                {busy ? "جاري الاعتماد..." : "اعتماد القيد"}
                            </button>
                            <button onClick={resetForm}
                                className="px-4 py-2.5 bg-slate-200 hover:bg-slate-300 text-slate-800 font-bold rounded-lg">
                                إعادة تعيين
                            </button>
                        </div>

                        {result && (
                            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 mt-3 text-xs"
                                 data-testid="unified-result">
                                <div className="font-bold text-emerald-900 mb-1">
                                    ✅ تم تسجيل القيد رقم: {result.txn_group_id?.slice(0, 8)}…
                                </div>
                                <div className="text-slate-700">
                                    عدد القيود: {result.entries?.length} ·
                                    إجمالي مدين: {result.debit_total} ·
                                    إجمالي دائن: {result.credit_total}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

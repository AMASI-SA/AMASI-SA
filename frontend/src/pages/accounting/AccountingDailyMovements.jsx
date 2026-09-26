import { useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    FileArrowUp,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    classifyAccountingOutgoingMovement,
    confirmAccountingDailyMovementProvider,
    createAccountingManualIncomingMovement,
    createAccountingManualOutgoingMovement,
    getAccountingDailyMovementContext,
    getAccountingDailyMovements,
    uploadAccountingDailyMovements,
} from "../../services/accountingModule";
import { formatMoney } from "./AccountingShared";

const PROVIDERS = {
    salla: "سلة",
    tamara: "تمارا",
    tabby: "تابي",
    emkan: "إمكان",
};

const STATUS = {
    provider_receipt_created: ["تم ربطه بمبلغ واصل", "bg-emerald-50 text-emerald-800 border-emerald-200"],
    needs_review: ["يحتاج منك", "bg-amber-50 text-amber-900 border-amber-200"],
    unclassified: ["بانتظار نوع حركة MZ2", "bg-slate-50 text-slate-700 border-slate-200"],
    pending_provider_receipt: ["جاري إنشاء مسودة", "bg-sky-50 text-sky-800 border-sky-200"],
    accounting_posted: ["مرحّل محاسبيًا", "bg-emerald-50 text-emerald-900 border-emerald-300"],
};

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) {
        if (Array.isArray(detail.errors)) {
            const rows = detail.errors.slice(0, 5).map((item) => `صف ${item.row_no}: ${item.code}`).join(" · ");
            return `${detail.message || detail.code} — ${rows}`;
        }
        return detail.code;
    }
    return fallback;
}

function Badge({ status }) {
    const [label, classes] = STATUS[status] || [status || "—", STATUS.unclassified[1]];
    return <span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-extrabold ${classes}`}>{label}</span>;
}

export default function AccountingDailyMovements({ accountingPermissions = [] }) {
    const [context, setContext] = useState(null);
    const [items, setItems] = useState([]);
    const [bankAccountId, setBankAccountId] = useState("");
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [busy, setBusy] = useState("");
    const [reasonById, setReasonById] = useState({});
    const [outgoingById, setOutgoingById] = useState({});
    const [manualDirection, setManualDirection] = useState("in");
    const [manualBankAccountId, setManualBankAccountId] = useState("");
    const [manualAmount, setManualAmount] = useState("");
    const [manualSender, setManualSender] = useState("");
    const [manualDate, setManualDate] = useState(() => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date()));
    const [manualReference, setManualReference] = useState("");
    const [manualNotes, setManualNotes] = useState("");
    const manualRequest = useRef(null);
    const canImport = accountingPermissions.includes("accounting.movements.import");
    const canPostExpense = accountingPermissions.includes("accounting.journals.manual_create");
    const canPostSupplierPayment = accountingPermissions.includes("accounting.settlements.post");

    async function refresh() {
        const [nextContext, nextRows] = await Promise.all([
            getAccountingDailyMovementContext(),
            getAccountingDailyMovements({ limit: 200 }),
        ]);
        setContext(nextContext);
        setItems(nextRows?.items || []);
        setBankAccountId((current) => current || nextContext?.banks?.[0]?.id || "");
        setManualBankAccountId((current) => current || nextContext?.banks?.find((bank) => bank.account_type === "bank")?.id || "");
    }

    useEffect(() => {
        refresh().catch((error) => toast.error(errorText(error, "تعذر تحميل الحركات اليومية")));
    }, []);

    const reviewCount = useMemo(
        () => items.filter((row) => row.status === "needs_review").length,
        [items],
    );
    const importedCount = useMemo(
        () => items.filter((row) => row.status === "provider_receipt_created").length,
        [items],
    );

    async function saveManual(event) {
        event.preventDefault();
        if (!canImport) return toast.error("لا تملك صلاحية إضافة حركة مالية");
        if (!manualBankAccountId) return toast.error("اختر البنك");
        if (!(Number(manualAmount) > 0)) return toast.error("أدخل مبلغ التحويل");
        if (!manualSender.trim()) {
            return toast.error(manualDirection === "in" ? "أدخل اسم المحوّل" : "أدخل اسم المستفيد");
        }
        const common = {
            bank_account_id: manualBankAccountId,
            amount: manualAmount,
            movement_date: manualDate,
            reference: manualReference.trim(),
            notes: manualNotes.trim(),
        };
        const facts = manualDirection === "in"
            ? { ...common, sender_name: manualSender.trim() }
            : { ...common, payee_name: manualSender.trim() };
        const fingerprint = JSON.stringify({ direction: manualDirection, ...facts });
        if (manualRequest.current?.fingerprint !== fingerprint) {
            manualRequest.current = { fingerprint, id: crypto.randomUUID() };
        }
        setBusy("manual");
        try {
            const createMovement = manualDirection === "in"
                ? createAccountingManualIncomingMovement
                : createAccountingManualOutgoingMovement;
            const result = await createMovement({
                ...facts,
                request_id: manualRequest.current.id,
            });
            if (result?.duplicate) {
                toast.info("هذه الحركة محفوظة مسبقًا؛ لم تتكرر.");
            } else if (manualDirection === "out") {
                toast.success("تم حفظ الحركة الخارجة كدليل MZ2. صنّفها أدناه كمصروف عام أو سداد مورد قبل الترحيل.");
            } else {
                toast.success("تم حفظ التحويل الوارد كحركة MZ2. سيبقى دليلًا حتى يُصنّف أو يُربط بالعملية المناسبة.");
            }
            setManualAmount("");
            setManualSender("");
            setManualReference("");
            setManualNotes("");
            manualRequest.current = null;
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ الحركة البنكية"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    async function upload(event) {
        event.preventDefault();
        if (!canImport) return toast.error("لا تملك صلاحية رفع كشف البنك");
        if (!bankAccountId) return toast.error("اختر البنك");
        if (!file) return toast.error("اختر ملف Excel");
        setBusy("upload");
        try {
            const result = await uploadAccountingDailyMovements({ bankAccountId, file });
            if (result.status === "duplicate") {
                toast.info("هذا الكشف مرفوع مسبقًا؛ لم يُكرر أي صف.");
            } else {
                toast.success(
                    `تم استيراد ${result.file?.row_count || 0} حركة · أنشأ ميزان ${result.provider_receipts_created || 0} مبلغًا واصلًا مؤكد المصدر`,
                    { duration: 7000 },
                );
            }
            setFile(null);
            setFileKey((value) => value + 1);
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر استيراد كشف البنك"), { duration: 9000 });
        } finally {
            setBusy("");
        }
    }

    async function confirm(row) {
        const provider = row.suggested_provider;
        const reason = String(reasonById[row.id] || "").trim();
        if (!provider) return;
        if (reason.length < 3) return toast.error("اكتب سبب تأكيد المزود");
        setBusy("confirm:" + row.id);
        try {
            await confirmAccountingDailyMovementProvider(row.id, provider, reason);
            toast.success("تم تأكيد المزود وتحويل الحركة إلى مسودة مبلغ واصل، بدون قيد مالي.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تأكيد الحركة"));
        } finally {
            setBusy("");
        }
    }

    async function classifyOutgoing(row) {
        const draft = outgoingById[row.id] || {};
        const action = draft.action || "";
        const reason = String(draft.reason || "").trim();
        if (!action) return toast.error("اختر نوع الصرف");
        if (action === "expense" && !canPostExpense) return toast.error("لا تملك صلاحية ترحيل المصروفات");
        if (action === "supplier_payment" && !canPostSupplierPayment) return toast.error("لا تملك صلاحية ترحيل سداد المورد");
        if (action === "expense" && !draft.expense_category) return toast.error("اختر فئة المصروف");
        if (action === "supplier_payment" && !draft.supplier_id) return toast.error("اختر المورد");
        if (reason.length < 3) return toast.error("اكتب سببًا واضحًا للترحيل");

        setBusy("outgoing:" + row.id);
        try {
            await classifyAccountingOutgoingMovement(row.id, {
                action,
                expense_category: action === "expense" ? draft.expense_category : "",
                supplier_id: action === "supplier_payment" ? draft.supplier_id : "",
                reason,
            });
            toast.success(action === "expense"
                ? "تم ترحيل المصروف من حركة البنك نفسها."
                : "تم سداد الذمة القائمة للمورد من حركة البنك نفسها.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر ترحيل الحركة الخارجة"), { duration: 8000 });
        } finally {
            setBusy("");
        }
    }

    function updateOutgoing(id, patch) {
        setOutgoingById((current) => ({
            ...current,
            [id]: { ...(current[id] || {}), ...patch },
        }));
    }

    return (
        <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5" dir="rtl" data-testid="accounting-daily-movements">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex items-center gap-2">
                        <Bank size={24} weight="duotone" className="text-emerald-800" />
                        <h2 className="text-xl font-black text-slate-950">كشف البنك والحركات اليومية — MZ2</h2>
                    </div>
                    <p className="mt-2 max-w-3xl text-xs font-semibold leading-6 text-slate-600">
                        رفع الكشف لا ينشئ قيدًا. يحفظ ميزان كل صف كدليل بنكي، ويحوّل التحويل الوارد إلى مسودة منصة فقط عندما تكون هوية المزود صريحة أو تؤكدها أنت.
                    </p>
                </div>
                <button type="button" onClick={() => refresh().catch(() => toast.error("تعذر التحديث"))} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs font-extrabold">
                    <ArrowClockwise size={17} /> تحديث
                </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <div className="text-[11px] font-bold text-slate-500">الحركات المحفوظة</div>
                    <div className="mt-1 text-2xl font-black">{items.length.toLocaleString("en-US")}</div>
                </div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                    <div className="text-[11px] font-bold text-emerald-700">تحويلات منصة مؤكدة</div>
                    <div className="mt-1 text-2xl font-black text-emerald-900">{importedCount.toLocaleString("en-US")}</div>
                </div>
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                    <div className="text-[11px] font-bold text-amber-700">تحتاج تأكيدك</div>
                    <div className="mt-1 text-2xl font-black text-amber-900">{reviewCount.toLocaleString("en-US")}</div>
                </div>
            </div>

            <form onSubmit={saveManual} className="space-y-4 rounded-2xl border border-emerald-200 bg-emerald-50/60 p-4" data-testid="manual-bank-movement-form">
                <div>
                    <h3 className="text-sm font-black text-emerald-950">إضافة حركة بنكية يدوية</h3>
                    <p className="mt-1 text-xs font-semibold leading-6 text-emerald-900">
                        سجّل الواقع فقط: داخل/خارج، البنك، المبلغ، الطرف والتاريخ. لا تختار مدين/دائن. إذا رُفع كشف البنك لاحقًا بنفس المرجع والمبلغ والتاريخ والاتجاه، يعتبره ميزان تأكيدًا لنفس الحركة ولا يكررها.
                    </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    <label className="text-xs font-extrabold text-slate-700">
                        اتجاه الحركة
                        <select value={manualDirection} onChange={(event) => { setManualDirection(event.target.value); manualRequest.current = null; }} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                            <option value="in">وارد إلى البنك</option>
                            <option value="out">خارج من البنك</option>
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        البنك
                        <select value={manualBankAccountId} onChange={(event) => setManualBankAccountId(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                            <option value="">اختر البنك</option>
                            {(context?.banks || []).filter((bank) => bank.account_type === "bank").map((bank) => (
                                <option key={bank.id} value={bank.id}>{bank.name}</option>
                            ))}
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        المبلغ
                        <input type="number" min="0.01" step="0.01" required value={manualAmount} onChange={(event) => setManualAmount(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-left font-mono" dir="ltr" />
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        {manualDirection === "in" ? "اسم المحوّل" : "المستفيد / الجهة المدفوع لها"}
                        <input
                            required
                            value={manualSender}
                            onChange={(event) => setManualSender(event.target.value)}
                            className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3"
                            placeholder={manualDirection === "in" ? "مثال: أحمد محمد / شركة تابي" : "مثال: مالك العقار / المورد"}
                        />
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        التاريخ
                        <input type="date" required value={manualDate} onChange={(event) => setManualDate(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3" />
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        مرجع التحويل <span className="font-semibold text-slate-400">اختياري</span>
                        <input value={manualReference} onChange={(event) => setManualReference(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono" dir="ltr" />
                    </label>
                    <label className="text-xs font-extrabold text-slate-700 xl:col-span-2">
                        ملاحظة <span className="font-semibold text-slate-400">اختيارية</span>
                        <input value={manualNotes} onChange={(event) => setManualNotes(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3" />
                    </label>
                </div>
                <div className="flex justify-end">
                    <button disabled={!canImport || !manualBankAccountId || !(Number(manualAmount) > 0) || !manualSender.trim() || busy === "manual"} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                        {busy === "manual" ? "جاري الحفظ…" : "حفظ الحركة"}
                    </button>
                </div>
            </form>

            <form onSubmit={upload} className="grid gap-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 lg:grid-cols-[1fr_1.5fr_auto] lg:items-end" data-testid="daily-movement-upload-form">
                <label className="text-xs font-extrabold text-slate-700">
                    البنك / الصندوق
                    <select value={bankAccountId} onChange={(event) => setBankAccountId(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                        <option value="">اختر الحساب</option>
                        {(context?.banks || []).map((bank) => (
                            <option key={bank.id} value={bank.id}>{bank.name} — {bank.account_type === "cash" ? "صندوق" : "بنك"}</option>
                        ))}
                    </select>
                </label>
                <label className="flex min-h-20 cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed border-slate-300 bg-white px-4 py-3">
                    <FileArrowUp size={26} weight="duotone" className="shrink-0 text-emerald-800" />
                    <div className="min-w-0">
                        <div className="truncate text-xs font-black text-slate-900">{file?.name || "اختر كشف Excel"}</div>
                        <div className="mt-1 text-[10px] font-semibold text-slate-500">الأعمدة: تاريخ + إيداع/سحب + بيان أو مرجع. عمود المنصة اختياري.</div>
                    </div>
                    <input key={fileKey} type="file" accept=".xlsx" className="sr-only" onChange={(event) => setFile(event.target.files?.[0] || null)} />
                </label>
                <button disabled={!canImport || !file || !bankAccountId || busy === "upload"} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy === "upload" ? "جاري الفحص…" : "رفع الكشف"}
                </button>
            </form>

            {!canImport && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">
                    لديك صلاحية مشاهدة الحركات فقط. رفع كشف البنك يحتاج صلاحية مستقلة.
                </div>
            )}

            <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs font-semibold leading-6 text-sky-900">
                إذا ظهر اسم تمارا/تابي/سلة/إمكان في نص البنك فقط، يعرضه ميزان كاقتراح ولا يعتمد عليه تلقائيًا. عمود «المنصة» الصريح في الملف يمكنه إنشاء مسودة مبلغ واصل تلقائيًا إذا كان البنك مربوطًا بالمزود.
            </div>

            <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs font-semibold leading-6 text-violet-950">
                للحركة الخارجة: «مصروف عام» يسجل المصروف مقابل البنك مباشرة. «سداد مورد قائم» يخفض ذمة مورد موجودة فقط ولا ينشئ فاتورة شراء أو مخزونًا جديدًا؛ إنشاء المشتريات والمخزون يبقى ضمن P03.
            </div>

            <div className="overflow-x-auto rounded-xl border border-slate-200">
                <table className="min-w-full text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="p-3 text-right">التاريخ</th>
                            <th className="p-3 text-right">الاتجاه</th>
                            <th className="p-3 text-right">المبلغ</th>
                            <th className="p-3 text-right">البيان / المرجع</th>
                            <th className="p-3 text-right">الحالة</th>
                            <th className="p-3 text-right">الإجراء</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.length === 0 ? (
                            <tr><td colSpan={6} className="p-8 text-center font-semibold text-slate-400">لا توجد حركات MZ2 محفوظة بعد.</td></tr>
                        ) : items.map((row) => (
                            <tr key={row.id} className="border-t border-slate-100 align-top">
                                <td className="p-3 font-mono">{row.movement_date}</td>
                                <td className="p-3">
                                    <span className={row.direction === "in" ? "font-extrabold text-emerald-700" : "font-extrabold text-rose-700"}>
                                        {row.direction === "in" ? "داخل" : "خارج"}
                                    </span>
                                </td>
                                <td className="p-3 font-mono font-black">{formatMoney(row.amount)}</td>
                                <td className="max-w-sm p-3">
                                    <div className="font-bold text-slate-800">{row.description || "—"}</div>
                                    {row.reference && <div className="mt-1 font-mono text-[10px] text-slate-400" dir="ltr">{row.reference}</div>}
                                    {row.suggested_provider && (
                                        <div className="mt-1 text-[10px] font-bold text-amber-700">اقتراح: {PROVIDERS[row.suggested_provider]}</div>
                                    )}
                                </td>
                                <td className="p-3"><Badge status={row.status} /></td>
                                <td className="min-w-64 p-3">
                                    {row.status === "accounting_posted" ? (
                                        <span className="inline-flex items-center gap-1 font-bold text-emerald-700"><CheckCircle size={16} weight="fill" /> تم ترحيل الحركة مرة واحدة</span>
                                    ) : row.direction === "out" && row.status === "unclassified" ? (
                                        <div className="space-y-2" data-testid={`outgoing-classify-${row.id}`}>
                                            <select
                                                value={outgoingById[row.id]?.action || ""}
                                                onChange={(event) => updateOutgoing(row.id, {
                                                    action: event.target.value,
                                                    expense_category: "",
                                                    supplier_id: "",
                                                })}
                                                className="min-h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-[11px] font-bold"
                                            >
                                                <option value="">اختر نوع الصرف</option>
                                                <option value="expense">مصروف عام</option>
                                                <option value="supplier_payment">سداد مورد قائم</option>
                                            </select>
                                            {outgoingById[row.id]?.action === "expense" && (
                                                <select
                                                    value={outgoingById[row.id]?.expense_category || ""}
                                                    onChange={(event) => updateOutgoing(row.id, { expense_category: event.target.value })}
                                                    className="min-h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-[11px]"
                                                >
                                                    <option value="">اختر فئة المصروف</option>
                                                    {(context?.expense_categories || []).map((category) => (
                                                        <option key={category.code} value={category.code}>{category.name}</option>
                                                    ))}
                                                </select>
                                            )}
                                            {outgoingById[row.id]?.action === "supplier_payment" && (
                                                <select
                                                    value={outgoingById[row.id]?.supplier_id || ""}
                                                    onChange={(event) => updateOutgoing(row.id, { supplier_id: event.target.value })}
                                                    className="min-h-9 w-full rounded-lg border border-slate-200 bg-white px-2 text-[11px]"
                                                >
                                                    <option value="">اختر المورد</option>
                                                    {(context?.suppliers || []).map((supplier) => (
                                                        <option key={supplier.id} value={supplier.id}>{supplier.name}</option>
                                                    ))}
                                                </select>
                                            )}
                                            <input
                                                value={outgoingById[row.id]?.reason || ""}
                                                onChange={(event) => updateOutgoing(row.id, { reason: event.target.value })}
                                                placeholder="سبب الترحيل / وصف المصروف"
                                                className="min-h-9 w-full rounded-lg border border-slate-200 px-2 text-[11px]"
                                            />
                                            <button
                                                type="button"
                                                onClick={() => classifyOutgoing(row)}
                                                disabled={
                                                    busy === "outgoing:" + row.id
                                                    || (outgoingById[row.id]?.action === "expense" && !canPostExpense)
                                                    || (outgoingById[row.id]?.action === "supplier_payment" && !canPostSupplierPayment)
                                                }
                                                className="min-h-9 rounded-lg bg-violet-800 px-3 text-[11px] font-extrabold text-white disabled:opacity-40"
                                            >
                                                {busy === "outgoing:" + row.id ? "جاري الترحيل…" : "اعتماد الصرف"}
                                            </button>
                                        </div>
                                    ) : row.status === "needs_review" && row.suggested_provider ? (
                                        <div className="space-y-2">
                                            <input
                                                value={reasonById[row.id] || ""}
                                                onChange={(event) => setReasonById((old) => ({ ...old, [row.id]: event.target.value }))}
                                                placeholder={`سبب تأكيد ${PROVIDERS[row.suggested_provider]}`}
                                                className="min-h-9 w-full rounded-lg border border-slate-200 px-2 text-[11px]"
                                            />
                                            <button type="button" onClick={() => confirm(row)} disabled={!canImport || busy === "confirm:" + row.id} className="min-h-9 rounded-lg bg-amber-700 px-3 text-[11px] font-extrabold text-white disabled:opacity-40">
                                                تأكيد المزود
                                            </button>
                                        </div>
                                    ) : row.status === "provider_receipt_created" ? (
                                        <span className="inline-flex items-center gap-1 font-bold text-emerald-700"><CheckCircle size={16} weight="fill" /> ينتظر كشف المنصة/المطابقة</span>
                                    ) : (
                                        <span className="text-[10px] font-semibold text-slate-500">
                                            ستظهر إجراءات الراتب/السلفة/العهدة في مسار الموظفين المخصص.
                                        </span>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div className="flex items-start gap-2 rounded-xl border border-amber-100 bg-amber-50 p-3 text-xs font-semibold leading-6 text-amber-900">
                <WarningCircle size={18} className="mt-0.5 shrink-0" />
                أي صف غير مصنف يبقى دليلًا فقط ولا يغيّر الرصيد أو دفتر الأستاذ. هذا مقصود حتى لا يخمّن ميزان نوع الحركة.
            </div>
        </section>
    );
}

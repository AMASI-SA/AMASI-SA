
import { useEffect, useRef, useState } from "react";
import {
    Bank,
    FileArrowUp,
    Package,
    UploadSimple,
    Wallet,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../../lib/api";
import {
    getAccountingDailyMovementContext,
    processAccountingCourierPending,
    processAccountingStoreDriverPending,
    recognizeAccountingReadyOrders,
    uploadAccountingDailyMovements,
    uploadAccountingOrderEvidence,
    uploadAccountingSettlementDraft,
} from "../../services/accountingModule";

const BASE = "/financial-provider-apps/accounting-module";
const PROVIDERS = {
    salla: "سلة",
    tamara: "تمارا",
    tabby: "تابي",
    emkan: "إمكان",
};

function todayRiyadh() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date());
}

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) return detail.code;
    return fallback;
}

function Modal({ title, subtitle, onClose, children, testid }) {
    return (
        <div
            className="fixed inset-0 z-[80] flex items-start justify-center overflow-y-auto bg-slate-950/45 p-3 backdrop-blur-sm sm:p-6"
            onMouseDown={onClose}
            data-testid={testid}
        >
            <div
                className="my-4 w-full max-w-4xl overflow-hidden rounded-2xl bg-white shadow-2xl"
                onMouseDown={(event) => event.stopPropagation()}
                dir="rtl"
            >
                <div className="sticky top-0 z-20 flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-5 py-4">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">{title}</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">{subtitle}</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-200 p-2 text-slate-500" aria-label="إغلاق">
                        <X size={20} />
                    </button>
                </div>
                <div className="max-h-[82vh] overflow-y-auto p-4 sm:p-5">{children}</div>
            </div>
        </div>
    );
}

function ActionCard({ title, detail, Icon, badge, onClick }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="group flex min-h-32 w-full items-start justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-5 text-right shadow-sm transition hover:border-emerald-300 hover:shadow-md"
        >
            <div>
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-black text-slate-950">{title}</span>
                    {badge && <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-extrabold text-emerald-800">{badge}</span>}
                </div>
                <p className="mt-2 text-xs font-semibold leading-6 text-slate-500">{detail}</p>
            </div>
            <span className="shrink-0 rounded-2xl bg-emerald-50 p-3 text-emerald-800">
                <Icon size={26} weight="duotone" />
            </span>
        </button>
    );
}

function FileDrop({ file, fileKey, accept = ".xlsx", title, subtitle, onChange, Icon = FileArrowUp }) {
    return (
        <label className="flex min-h-40 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center hover:border-emerald-400 hover:bg-emerald-50">
            <Icon size={34} weight="duotone" className="text-emerald-800" />
            <span className="mt-3 text-sm font-black text-slate-900">{file?.name || title}</span>
            <span className="mt-1 text-xs font-semibold text-slate-500">{subtitle}</span>
            <input key={fileKey} type="file" accept={accept} className="sr-only" onChange={(event) => onChange(event.target.files?.[0] || null)} />
        </label>
    );
}

function OrdersForm({ permissions, onSaved }) {
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [busy, setBusy] = useState(false);
    const canPost = permissions.includes("accounting.receivables.post");

    async function submit(event) {
        event.preventDefault();
        if (!canPost) return toast.error("لا تملك صلاحية رفع أدلة الطلبات");
        if (!file) return toast.error("اختر ملف طلبات سلة");
        setBusy(true);
        try {
            const imported = await uploadAccountingOrderEvidence(file);
            if (imported?.status === "duplicate") {
                toast.info("هذا الملف مرفوع مسبقًا؛ لم تتكرر الطلبات أو القيود.");
            } else {
                const recognition = await recognizeAccountingReadyOrders({ limit: 500, dryRun: false });
                let driverShipping = null;
                let courierShipping = null;
                if (permissions.includes("accounting.settlements.post")) {
                    [driverShipping, courierShipping] = await Promise.all([
                        processAccountingStoreDriverPending({ limit: 500, dryRun: false }),
                        processAccountingCourierPending({ limit: 500, dryRun: false }),
                    ]);
                }
                const shippingPosted =
                    Number(driverShipping?.posted_count || 0)
                    + Number(courierShipping?.posted_count || 0);
                const message =
                    "تم حفظ " + Number(imported?.file?.row_count || 0).toLocaleString("en-US")
                    + " طلبًا · رُحّل " + Number(recognition?.posted_count || 0).toLocaleString("en-US") + " بيعًا آمنًا"
                    + (Number(recognition?.blocked_count || 0) ? " · " + Number(recognition.blocked_count).toLocaleString("en-US") + " بانتظار دليل" : "")
                    + (shippingPosted ? " · " + shippingPosted.toLocaleString("en-US") + " شحن/COD" : "");
                toast.success(message, { duration: 9000 });
            }
            setFile(null);
            setFileKey((value) => value + 1);
            await onSaved();
        } catch (error) {
            toast.error(errorMessage(error, "تعذر رفع طلبات سلة"), { duration: 9000 });
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={submit} className="space-y-4" data-testid="daily-salla-orders-upload">
            <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-xs font-semibold leading-6 text-emerald-900">
                ارفع الملف الأصلي من سلة. ميزان يحفظ الدليل أولًا، يرحّل المبيعات الآمنة فقط، ويترك التعارضات أو الأدلة الناقصة للمراجعة. COD لا يُثبت من ملف سلة وحده.
            </div>
            <FileDrop
                file={file}
                fileKey={fileKey}
                title="اختر ملف طلبات سلة"
                subtitle="XLSX كما نزل من سلة، بدون تعديل"
                onChange={setFile}
                Icon={Package}
            />
            <div className="rounded-xl border border-sky-100 bg-sky-50 p-3 text-xs font-semibold leading-6 text-sky-900">
                مدى/البطاقات → ذمة سلة. تمارا/تابي/إمكان تحتاج أيضًا دليل المزود. التحويل البنكي ينتظر البنك. الاسترداد يبقى مسارًا مستقلًا.
            </div>
            <div className="flex justify-end">
                <button disabled={busy || !file || !canPost} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري القراءة والترحيل الآمن…" : "رفع ومعالجة تلقائية"}
                </button>
            </div>
        </form>
    );
}

function BankForm({ permissions, onSaved }) {
    const [context, setContext] = useState(null);
    const [bankId, setBankId] = useState("");
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [busy, setBusy] = useState(false);
    const canImport = permissions.includes("accounting.movements.import");

    useEffect(() => {
        getAccountingDailyMovementContext()
            .then((data) => {
                setContext(data);
                setBankId((current) => current || data?.banks?.[0]?.id || "");
            })
            .catch(() => toast.error("تعذر تحميل حسابات البنك"));
    }, []);

    async function submit(event) {
        event.preventDefault();
        if (!canImport) return toast.error("لا تملك صلاحية رفع كشف البنك");
        if (!bankId) return toast.error("اختر البنك");
        if (!file) return toast.error("اختر كشف Excel");
        setBusy(true);
        try {
            const result = await uploadAccountingDailyMovements({ bankAccountId: bankId, file });
            toast.success(
                result?.status === "duplicate"
                    ? "هذا الكشف مرفوع مسبقًا؛ لم تتكرر أي حركة."
                    : "تم حفظ " + Number(result?.file?.row_count || 0).toLocaleString("en-US") + " حركة · "
                        + Number(result?.provider_receipts_created || 0).toLocaleString("en-US") + " تحويلات منصة مؤكدة",
                { duration: 8000 },
            );
            setFile(null);
            setFileKey((value) => value + 1);
            await onSaved();
        } catch (error) {
            toast.error(errorMessage(error, "تعذر رفع كشف البنك"), { duration: 9000 });
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={submit} className="space-y-4" data-testid="daily-bank-statement-upload">
            <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-xs font-semibold leading-6 text-emerald-900">
                كشف البنك هو دليل الحركة النقدية. لا يعيد ميزان طلب المبلغ عند صرف راتب أو تسوية شحن لاحقًا.
            </div>
            <label className="block text-xs font-extrabold text-slate-700">
                البنك أو الصندوق
                <select value={bankId} onChange={(event) => setBankId(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                    <option value="">اختر الحساب</option>
                    {(context?.banks || []).map((bank) => <option key={bank.id} value={bank.id}>{bank.name || bank.id}</option>)}
                </select>
            </label>
            <FileDrop
                file={file}
                fileKey={fileKey}
                title="اختر كشف البنك"
                subtitle="XLSX: تاريخ + إيداع/سحب + بيان أو مرجع"
                onChange={setFile}
            />
            <div className="flex justify-end">
                <button disabled={busy || !file || !bankId || !canImport} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري الفحص…" : "رفع كشف البنك"}
                </button>
            </div>
        </form>
    );
}

function SettlementForm({ context, permissions, onSaved }) {
    const [provider, setProvider] = useState("salla");
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [busy, setBusy] = useState(false);
    const canCreate = context?.can_create_draft === true || permissions.includes("accounting.drafts.create");
    const binding = (context?.bindings || []).find((item) => item.provider === provider);

    async function submit(event) {
        event.preventDefault();
        if (!file) return toast.error("اختر ملف التسوية");
        if (!binding?.bank_account_id) return toast.error("لم يُعتمد بنك لهذه المنصة");
        setBusy(true);
        try {
            const result = await uploadAccountingSettlementDraft({ provider, bankAccountId: binding.bank_account_id, file });
            if (result?.draft?.duplicate) toast.info("هذا الملف موجود مسبقًا؛ لم يُكرر.");
            else if (result?.draft?.status === "needs_review") toast.warning("تم الرفع؛ توجد نقطة تحتاج قرارك.");
            else toast.success("تم رفع الملف وبدأت المطابقة.");
            setFile(null);
            setFileKey((value) => value + 1);
            await onSaved();
        } catch (error) {
            toast.error(errorMessage(error, "تعذر رفع ملف التسوية"));
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={submit} className="space-y-4" data-testid="daily-settlement-upload">
            <FileDrop
                file={file}
                fileKey={fileKey}
                accept=".xlsx,.xls"
                title="اختر ملف التسوية الأصلي"
                subtitle="سلة أو تمارا أو تابي أو إمكان"
                onChange={setFile}
                Icon={UploadSimple}
            />
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-xs font-extrabold text-slate-700">
                    المنصة
                    <select value={provider} onChange={(event) => setProvider(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                        {Object.entries(PROVIDERS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                </label>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div className="text-[11px] font-bold text-slate-500">البنك المرتبط</div>
                    <div className="mt-1 text-sm font-black text-slate-900">{binding?.bank_account_name || "غير مربوط"}</div>
                </div>
            </div>
            <div className="flex justify-end">
                <button disabled={busy || !file || !canCreate || !binding?.bank_account_id} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري المطابقة…" : "رفع وبدء المطابقة"}
                </button>
            </div>
        </form>
    );
}

function ReceiptForm({ context, permissions, onSaved }) {
    const [provider, setProvider] = useState("tabby");
    const [amount, setAmount] = useState("");
    const [date, setDate] = useState(todayRiyadh);
    const [message, setMessage] = useState("");
    const [busy, setBusy] = useState(false);
    const requestRef = useRef(null);
    const canCreate = permissions.includes("accounting.receipts.create");
    const binding = (context?.bindings || []).find((item) => item.provider === provider);

    async function submit(event) {
        event.preventDefault();
        if (!canCreate) return toast.error("لا تملك صلاحية تسجيل مبلغ واصل");
        if (!binding?.bank_account_id) return toast.error("لم يُعتمد البنك لهذه المنصة");
        const facts = { provider, amount, bank_message: message, received_on: date };
        const fingerprint = JSON.stringify(facts);
        if (requestRef.current?.fingerprint !== fingerprint) {
            requestRef.current = { fingerprint, id: crypto.randomUUID() };
        }
        setBusy(true);
        try {
            await api.post(BASE + "/bank-receipts", { ...facts, request_id: requestRef.current.id });
            toast.success("تم حفظ المبلغ كدليل؛ المطابقة هي التي تنشئ الأثر المحاسبي.");
            setAmount("");
            setMessage("");
            requestRef.current = null;
            await onSaved();
        } catch (error) {
            toast.error(errorMessage(error, "تعذر حفظ المبلغ"));
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={submit} className="space-y-4" data-testid="daily-provider-receipt-form">
            <div className="rounded-xl border border-amber-100 bg-amber-50 p-4 text-xs font-semibold leading-6 text-amber-900">
                إدخال احتياطي فقط عند غياب كشف البنك. لا يوجد مدين/دائن ولا اختيار حساب قيد.
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-xs font-extrabold text-slate-700">
                    المنصة
                    <select value={provider} onChange={(event) => setProvider(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                        {Object.entries(PROVIDERS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                </label>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div className="text-[11px] font-bold text-slate-500">البنك المرتبط</div>
                    <div className="mt-1 text-sm font-black text-slate-900">{binding?.bank_account_name || "غير مربوط"}</div>
                </div>
                <label className="text-xs font-extrabold text-slate-700">
                    المبلغ
                    <input required type="number" min="0.01" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3 text-left font-mono" dir="ltr" />
                </label>
                <label className="text-xs font-extrabold text-slate-700">
                    تاريخ الوصول
                    <input required type="date" value={date} onChange={(event) => setDate(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3" />
                </label>
            </div>
            <label className="block text-xs font-extrabold text-slate-700">
                رسالة البنك أو المرجع
                <textarea required maxLength={4000} value={message} onChange={(event) => setMessage(event.target.value)} className="mt-1 min-h-24 w-full rounded-xl border border-slate-200 p-3 text-sm" />
            </label>
            <div className="flex justify-end">
                <button disabled={busy || !canCreate || !binding?.bank_account_id} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري الحفظ…" : "حفظ الدليل"}
                </button>
            </div>
        </form>
    );
}

export default function AccountingDailyAutomationActions({ settlementContext, permissions = [], onSaved }) {
    const [active, setActive] = useState("");

    return (
        <section data-testid="daily-accounting-automation-actions">
            <div>
                <h2 className="text-lg font-black text-slate-950">ماذا وصل اليوم؟</h2>
                <p className="mt-1 text-xs font-semibold text-slate-500">ارفع المصدر فقط؛ ميزان يتولى التصنيف والمطابقة والترحيل الآمن.</p>
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <ActionCard title="رفع طلبات سلة" detail="يحفظ الدليل ويرحّل المبيعات الآمنة تلقائيًا." Icon={Package} badge="تلقائي" onClick={() => setActive("orders")} />
                <ActionCard title="رفع كشف البنك" detail="يحفظ الحركات ويمنع إعادة إدخال المبلغ لاحقًا." Icon={Bank} badge="تلقائي" onClick={() => setActive("bank")} />
                <ActionCard title="رفع ملف تسوية" detail="سلة/تمارا/تابي/إمكان مع البنك المرتبط." Icon={UploadSimple} onClick={() => setActive("settlement")} />
                <ActionCard title="إضافة مبلغ واصل" detail="احتياطي فقط إذا لم يتوفر كشف بنك." Icon={Wallet} badge="احتياطي" onClick={() => setActive("receipt")} />
            </div>

            {active === "orders" && (
                <Modal title="رفع طلبات سلة" subtitle="Evidence-first: لا يُرحّل إلا ما اكتملت أدلته." onClose={() => setActive("")} testid="daily-orders-modal">
                    <OrdersForm permissions={permissions} onSaved={onSaved} />
                </Modal>
            )}
            {active === "bank" && (
                <Modal title="رفع كشف البنك" subtitle="الحركة البنكية هي الحقيقة النقدية." onClose={() => setActive("")} testid="daily-bank-modal">
                    <BankForm permissions={permissions} onSaved={onSaved} />
                </Modal>
            )}
            {active === "settlement" && (
                <Modal title="رفع ملف تسوية" subtitle="الملف الأصلي + البنك المرتبط؛ بدون تكوين قيد يدوي." onClose={() => setActive("")} testid="daily-settlement-modal">
                    <SettlementForm context={settlementContext} permissions={permissions} onSaved={onSaved} />
                </Modal>
            )}
            {active === "receipt" && (
                <Modal title="إضافة مبلغ واصل" subtitle="بديل احتياطي عندما لا يتوفر كشف البنك." onClose={() => setActive("")} testid="daily-receipt-modal">
                    <ReceiptForm context={settlementContext} permissions={permissions} onSaved={onSaved} />
                </Modal>
            )}
        </section>
    );
}

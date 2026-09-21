import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    ClipboardText,
    FileArrowUp,
    Receipt,
    GearSix,
    ShoppingCart,
    UploadSimple,
    UserGear,
    UsersThree,
    Wallet,
    WarningCircle,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../../lib/api";
import {
    getAccountingOrderRecognitionQueue,
    getAccountingSettlementContext,
    getAccountingSettlementDrafts,
    recognizeAccountingReadyOrders,
    uploadAccountingOrderEvidence,
    uploadAccountingSettlementDraft,
} from "../../services/accountingModule";
import AccountingDailyMovements from "./AccountingDailyMovements";
import AccountingBankTransferReceipts from "./AccountingBankTransferReceipts";
import AccountingPayroll from "./AccountingPayroll";
import AccountingPeriods from "./AccountingPeriods";
import AccountingWriteControl from "./AccountingWriteControl";
import { formatMoney, SummaryCard } from "./AccountingShared";
import { ACCOUNTING_PAGES } from "./accountingPages";

const BASE = "/financial-provider-apps/accounting-module";
const PROVIDERS = { salla: "سلة", tamara: "تمارا", tabby: "تابي", emkan: "إمكان" };
const REVIEW_STATUSES = new Set(["needs_review", "ready_for_review", "reviewed", "rejected"]);
const STATUS_LABELS = {
    draft: "مسودة",
    needs_review: "تحتاج منك",
    ready_for_review: "جاهزة للمراجعة",
    reviewed: "جاهزة للترحيل",
    posting: "جاري الترحيل",
    posted: "تم الترحيل",
    rejected: "تحتاج معالجة",
};

const ORDER_WAITING_LABELS = {
    provider_payment_evidence_missing: "بانتظار تأكيد الدفع من المزود",
    provider_payment_identity_not_unique: "مرجع الدفع يحتاج مراجعة",
    sales_tax_not_configured_for_date: "ضريبة المبيعات غير مضبوطة لهذا التاريخ",
    accounts_require_approved_opening: "الحساب يحتاج رصيدًا افتتاحيًا معتمدًا",
    existing_journal_requires_review: "يوجد قيد سابق للطلب ويحتاج مراجعة",
    fulfilment_not_proven: "التوصيل غير مثبت بعد",
};

function dailyFriendlyReference(value, fallback) {
    const text = String(value || "").trim();
    if (!text) return fallback;
    const technical =
        text.length > 28
        || text.includes(":")
        || /^SYN[-_:]/i.test(text)
        || /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(text);
    return technical ? fallback : text;
}

function todayRiyadh() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date());
}

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) return detail.code;
    if (Array.isArray(detail?.reasons)) return detail.reasons.map((item) => item.message || item.code).join(" · ");
    return fallback;
}

function guessProvider(filename) {
    const value = String(filename || "").toLowerCase();
    if (value.includes("tamara")) return "tamara";
    if (value.includes("tabby")) return "tabby";
    if (value.includes("emkan") || value.includes("إمكان")) return "emkan";
    if (value.includes("salla") || value.includes("سلة")) return "salla";
    return "";
}

function ActionCard({ title, detail, Icon, onClick, badge = "" }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="group flex min-h-32 w-full items-start justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-5 text-right shadow-sm transition hover:border-emerald-300 hover:shadow-md"
        >
            <div>
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-base font-black text-slate-950">{title}</span>
                    {badge && <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-extrabold text-slate-600">{badge}</span>}
                </div>
                <p className="mt-2 text-xs font-semibold leading-6 text-slate-500">{detail}</p>
            </div>
            <span className="shrink-0 rounded-2xl bg-emerald-50 p-3 text-emerald-800 transition group-hover:bg-emerald-100">
                <Icon size={26} weight="duotone" />
            </span>
        </button>
    );
}

function Modal({ title, subtitle, onClose, children, testid }) {
    return (
        <div className="fixed inset-0 z-[80] flex items-start justify-center overflow-y-auto bg-slate-950/45 p-3 backdrop-blur-sm sm:p-6" onMouseDown={onClose} data-testid={testid}>
            <div className="my-4 w-full max-w-6xl overflow-hidden rounded-2xl bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()} dir="rtl">
                <div className="sticky top-0 z-20 flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-5 py-4">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">{title}</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">{subtitle}</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border border-slate-200 p-2 text-slate-500 hover:bg-slate-50" aria-label="إغلاق">
                        <X size={20} />
                    </button>
                </div>
                <div className="max-h-[82vh] overflow-y-auto p-4 sm:p-5">{children}</div>
            </div>
        </div>
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

    async function save(event) {
        event.preventDefault();
        if (!canCreate) return toast.error("لا تملك صلاحية تسجيل حركة مالية");
        if (!binding?.bank_account_id) return toast.error("لم يُعتمد البنك الحالي لهذه المنصة");
        const facts = { provider, amount, bank_message: message, received_on: date };
        const fingerprint = JSON.stringify(facts);
        if (requestRef.current?.fingerprint !== fingerprint) {
            requestRef.current = { fingerprint, id: crypto.randomUUID() };
        }
        setBusy(true);
        try {
            await api.post(BASE + "/bank-receipts", { ...facts, request_id: requestRef.current.id });
            toast.success("تم حفظ المبلغ الواصل. المطابقة والترحيل ينتظران دليل التسوية.");
            setAmount("");
            setMessage("");
            requestRef.current = null;
            await onSaved();
        } catch (error) {
            toast.error(errorMessage(error, "تعذر حفظ الحركة"));
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={save} className="mx-auto max-w-2xl space-y-4" data-testid="daily-accounting-receipt-form">
            <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-xs font-semibold leading-6 text-emerald-900">
                أدخل ما ظهر فعليًا في البنك. لا تختار مدين أو دائن؛ ميزان يطابقه بالدليل ثم ينشئ القيد عند اكتماله.
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-xs font-extrabold text-slate-700">
                    المنصة
                    <select value={provider} onChange={(event) => setProvider(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                        {Object.entries(PROVIDERS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                </label>
                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div className="text-[11px] font-bold text-slate-500">البنك</div>
                    <div className="mt-1 text-sm font-black text-slate-900">{binding?.bank_account_name || "لم يتم ربط بنك لهذه المنصة"}</div>
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
                <textarea required maxLength={4000} value={message} onChange={(event) => setMessage(event.target.value)} className="mt-1 min-h-24 w-full rounded-xl border border-slate-200 p-3 text-sm" placeholder="الصق رسالة الإيداع أو المرجع كما ظهر في البنك" />
            </label>
            <div className="flex justify-end">
                <button disabled={busy || !canCreate || !binding?.bank_account_id} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري الحفظ…" : "حفظ الحركة"}
                </button>
            </div>
        </form>
    );
}

function SettlementUploadForm({ context, permissions, onSaved }) {
    const [provider, setProvider] = useState("salla");
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [busy, setBusy] = useState(false);
    const canCreate = context?.can_create_draft === true || permissions.includes("accounting.drafts.create");
    const binding = (context?.bindings || []).find((item) => item.provider === provider);

    function onFile(next) {
        setFile(next);
        const guessed = guessProvider(next?.name);
        if (guessed) setProvider(guessed);
    }

    async function upload(event) {
        event.preventDefault();
        if (!file) return toast.error("اختر ملف التسوية");
        if (!binding?.bank_account_id) return toast.error("لم يُعتمد بنك لهذه المنصة");
        setBusy(true);
        try {
            const result = await uploadAccountingSettlementDraft({ provider, bankAccountId: binding.bank_account_id, file });
            const draft = result?.draft;
            if (draft?.status === "needs_review") toast.warning("تم رفع الملف، ويوجد استثناء يحتاج منك.");
            else if (draft?.duplicate) toast.info("هذا الملف موجود مسبقًا؛ لم يُكرر.");
            else toast.success("تم رفع ملف التسوية وبدأت المطابقة تلقائيًا.");
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
        <form onSubmit={upload} className="mx-auto max-w-2xl space-y-4" data-testid="daily-accounting-settlement-upload">
            <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-xs font-semibold leading-6 text-emerald-900">
                ارفع الملف الأصلي من المنصة. ميزان يقرأ الرسوم والضريبة والمبلغ ويطابقها مع البنك؛ الاستثناء فقط يظهر لك.
            </div>
            <label className="flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center">
                <FileArrowUp size={34} weight="duotone" className="text-emerald-800" />
                <span className="mt-3 text-sm font-black text-slate-900">{file?.name || "اختر ملف التسوية"}</span>
                <input key={fileKey} type="file" accept=".xlsx,.xls" className="sr-only" onChange={(event) => onFile(event.target.files?.[0] || null)} />
            </label>
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
                    {busy ? "جاري القراءة والمطابقة…" : "رفع وبدء المطابقة"}
                </button>
            </div>
        </form>
    );
}

function SallaOrdersUploadForm({ permissions, onSaved }) {
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null);
    const canPost = permissions.includes("accounting.receivables.post");

    async function upload(event) {
        event.preventDefault();
        if (!file) return toast.error("اختر ملف طلبات سلة");
        if (!canPost) return toast.error("لا تملك صلاحية معالجة ذمم الطلبات");
        setBusy(true);
        try {
            const imported = await uploadAccountingOrderEvidence(file);
            const fileId = imported?.file?.id || "";
            const limit = Math.min(Math.max(Number(imported?.file?.row_count || 100), 1), 500);
            const recognized = await recognizeAccountingReadyOrders({
                limit,
                dryRun: false,
                fileId,
            });
            setResult({ imported, recognized });
            const posted = Number(recognized?.posted_count || 0);
            const waiting = Number(recognized?.blocked_count || 0);
            toast.success(`تمت معالجة الملف: ${posted} طلبًا آمنًا رُحّل تلقائيًا، و${waiting} ينتظر دليلًا أو مراجعة.`, { duration: 8000 });
            setFile(null);
            setFileKey((value) => value + 1);
            await onSaved();
        } catch (error) {
            toast.error(errorMessage(error, "تعذر معالجة ملف طلبات سلة"), { duration: 9000 });
        } finally {
            setBusy(false);
        }
    }

    return (
        <form onSubmit={upload} className="mx-auto max-w-3xl space-y-4" data-testid="daily-accounting-salla-orders-upload">
            <div className="rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-xs font-semibold leading-6 text-emerald-900">
                ارفع ملف الطلبات كما نزل من سلة. ميزان يحفظه كدليل، ثم يرحّل تلقائيًا فقط الطلبات المكتملة الأدلة. COD والتحويل البنكي والتعارضات تبقى معلقة ولا يُخمّن النظام نتيجتها.
            </div>
            <label className="flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center">
                <ShoppingCart size={34} weight="duotone" className="text-emerald-800" />
                <span className="mt-3 text-sm font-black text-slate-900">{file?.name || "اختر ملف طلبات سلة Excel"}</span>
                <span className="mt-1 text-xs font-semibold text-slate-500">الملف الأصلي؛ لا تعدّل الأعمدة أو مراجع الدفع.</span>
                <input key={fileKey} type="file" accept=".xlsx" className="sr-only" onChange={(event) => setFile(event.target.files?.[0] || null)} />
            </label>
            <div className="flex justify-end">
                <button disabled={busy || !file || !canPost} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري حفظ الأدلة وترحيل الآمن…" : "رفع ومعالجة تلقائيًا"}
                </button>
            </div>
            {result && (
                <div className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:grid-cols-4" data-testid="salla-order-upload-result">
                    <div><div className="text-[10px] font-bold text-slate-500">صفوف الملف</div><div className="mt-1 text-lg font-black">{Number(result.imported?.file?.row_count || 0).toLocaleString("en-US")}</div></div>
                    <div><div className="text-[10px] font-bold text-emerald-700">رُحّل تلقائيًا</div><div className="mt-1 text-lg font-black text-emerald-900">{Number(result.recognized?.posted_count || 0).toLocaleString("en-US")}</div></div>
                    <div><div className="text-[10px] font-bold text-amber-700">ينتظر دليلًا/مراجعة</div><div className="mt-1 text-lg font-black text-amber-900">{Number(result.recognized?.blocked_count || 0).toLocaleString("en-US")}</div></div>
                    <div><div className="text-[10px] font-bold text-slate-500">تعارضات الملف</div><div className="mt-1 text-lg font-black">{Number(result.imported?.file?.conflict_count || 0).toLocaleString("en-US")}</div></div>
                </div>
            )}
        </form>
    );
}

function ExceptionList({ status, drafts, receipts, orderQueue, totalReviewCount = 0 }) {
    const taskItems = (status?.tasks || []).slice(0, 6).map((task) => {
        const page = ACCOUNTING_PAGES.find((row) => row.id === task.page) || ACCOUNTING_PAGES[0];
        return { id: "task:" + task.id, title: task.title, detail: task.detail, to: page.to, informational: false };
    });
    const orderItems = (orderQueue?.items || []).filter((item) => item.state === "waiting").slice(0, 4).map((item) => ({
        id: "order:" + item.evidence_id,
        title: "طلب " + item.order_number + " — ينتظر دليلًا",
        detail: (item.reasons || []).map((reason) => ORDER_WAITING_LABELS[reason] || "يحتاج مراجعة محاسبية").join(" · "),
        to: "/integrations-v2?workspace=financial&page=home",
        informational: false,
    }));
    const draftItems = drafts.filter((draft) => REVIEW_STATUSES.has(draft.status)).slice(0, 4).map((draft) => ({
        id: "settlement:" + draft.id,
        title: (PROVIDERS[draft.provider] || draft.provider || "تسوية") + " — " + (STATUS_LABELS[draft.status] || draft.status),
        detail: dailyFriendlyReference(draft.statement_reference, "تسوية تحتاج متابعة"),
        to: "/integrations-v2?workspace=financial&page=settlements",
        informational: false,
    }));
    const receiptItems = receipts.filter((item) => item.status === "waiting_statement").slice(0, 3).map((item) => ({
        id: "receipt:" + item.id,
        title: "مبلغ واصل من " + (PROVIDERS[item.provider] || item.provider),
        detail: formatMoney(item.amount) + " — ينتظر ملف المنصة للمطابقة",
        to: "/integrations-v2?workspace=financial&page=financial-movements",
        informational: true,
    }));
    const items = [...taskItems, ...orderItems, ...draftItems, ...receiptItems].slice(0, 10);

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="daily-accounting-exceptions">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-black text-slate-950">يحتاج منك</h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">
                        العمليات المكتملة تُعالج تلقائيًا؛ تظهر هنا الاستثناءات فقط.
                        {totalReviewCount > items.length && (
                            <span className="mt-1 block font-extrabold text-amber-800" data-testid="daily-accounting-exceptions-limit-note">
                                القائمة مختصرة إلى {items.length.toLocaleString("en-US")} عناصر ظاهرة كحد أقصى؛ إجمالي ما يحتاج قرارك {totalReviewCount.toLocaleString("en-US")}.
                            </span>
                        )}
                    </p>
                </div>
                <span className="rounded-full bg-amber-100 px-3 py-1 font-mono text-xs font-black text-amber-900" dir="ltr">
                    {totalReviewCount.toLocaleString("en-US")}
                </span>
            </div>
            <div className="mt-4 space-y-2">
                {items.length === 0 ? (
                    <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm font-extrabold text-emerald-800">
                        <CheckCircle size={21} weight="fill" /> لا يوجد شيء يحتاج منك الآن.
                    </div>
                ) : items.map((item) => (
                    <Link key={item.id} to={item.to} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 transition hover:border-amber-300 hover:bg-amber-50">
                        <div>
                            <div className="text-sm font-black text-slate-900">{item.title}</div>
                            <div className="mt-1 text-xs font-semibold leading-5 text-slate-500">{item.detail}</div>
                        </div>
                        <span className={"rounded-full px-2 py-1 text-[10px] font-extrabold " + (item.informational ? "bg-sky-50 text-sky-700" : "bg-amber-100 text-amber-900")}>
                            {item.informational ? "للعلم" : "راجع"}
                        </span>
                    </Link>
                ))}
            </div>
        </section>
    );
}

function RecentActivity({ drafts, receipts }) {
    const events = [
        ...drafts.map((row) => ({
            id: "draft:" + row.id,
            at: row.updated_at || row.created_at || "",
            title: (PROVIDERS[row.provider] || row.provider || "تسوية") + " — " + (STATUS_LABELS[row.status] || row.status),
            detail: dailyFriendlyReference(row.statement_reference, dailyFriendlyReference(row.source_snapshot?.filename, "ملف تسوية")),
            tone: row.status === "posted" ? "ok" : REVIEW_STATUSES.has(row.status) ? "warn" : "neutral",
        })),
        ...receipts.map((row) => ({
            id: "receipt:" + row.id,
            at: row.created_at || row.received_on || "",
            title: "مبلغ واصل — " + (PROVIDERS[row.provider] || row.provider),
            detail: formatMoney(row.amount) + " · " + (row.status === "posted" ? "تمت تسويته" : row.status === "linked" ? "تمت مطابقته" : "بانتظار كشف المنصة"),
            tone: row.status === "posted" ? "ok" : "neutral",
        })),
    ].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 8);

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="daily-accounting-recent">
            <h2 className="text-lg font-black text-slate-950">آخر العمليات</h2>
            <p className="mt-1 text-xs font-semibold text-slate-500">مختصر بدون المراجع التقنية أو تفاصيل دفتر الأستاذ؛ التفاصيل الكاملة تبقى في الصفحات المتخصصة.</p>
            <div className="mt-4 divide-y divide-slate-100">
                {events.length === 0 ? (
                    <div className="py-6 text-center text-xs font-semibold text-slate-400">لا توجد عمليات حديثة.</div>
                ) : events.map((event) => (
                    <div key={event.id} className="flex items-start gap-3 py-3">
                        <span className={"mt-1 h-2.5 w-2.5 shrink-0 rounded-full " + (event.tone === "ok" ? "bg-emerald-500" : event.tone === "warn" ? "bg-amber-500" : "bg-slate-300")} />
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-extrabold text-slate-900">{event.title}</div>
                            <div className="mt-1 text-xs font-semibold text-slate-500">{event.detail}</div>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}

export default function AccountingDailyWorkspace({ status, user, accountingPermissions = [], canManagePermissions = false, onOpenPermissions }) {
    const [context, setContext] = useState(null);
    const [drafts, setDrafts] = useState([]);
    const [receipts, setReceipts] = useState([]);
    const [orderQueue, setOrderQueue] = useState({ items: [], waiting_count: 0 });
    const [loading, setLoading] = useState(true);
    const [activeAction, setActiveAction] = useState("");
    const [advancedOpen, setAdvancedOpen] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        const results = await Promise.allSettled([
            getAccountingSettlementContext(),
            getAccountingSettlementDrafts({ limit: 30 }),
            api.get(BASE + "/bank-receipts"),
            getAccountingOrderRecognitionQueue({ limit: 50 }),
        ]);
        if (results[0].status === "fulfilled") setContext(results[0].value);
        if (results[1].status === "fulfilled") setDrafts(results[1].value?.items || []);
        if (results[2].status === "fulfilled") setReceipts(results[2].value?.data?.items || []);
        if (results[3].status === "fulfilled") setOrderQueue(results[3].value || { items: [], waiting_count: 0 });
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const today = todayRiyadh();
    const receivedToday = useMemo(
        () => receipts.filter((row) => row.received_on === today).reduce((sum, row) => sum + Number(row.amount || 0), 0),
        [receipts, today],
    );
    const settlementsNeedReview = useMemo(
        () => drafts.filter((draft) => REVIEW_STATUSES.has(draft.status)).length,
        [drafts],
    );
    const orderWaiting = Number(orderQueue?.waiting_count || 0);
    const reviewCount = Number(status?.review_count || 0);
    const safeActive = status?.cutover?.safe_active === true;
    const pending = Math.max(reviewCount, settlementsNeedReview + orderWaiting);
    const stateLabel = safeActive
        ? (pending > 0 ? "محدثة · " + pending + " تحتاج منك" : "محدثة · لا توجد مراجعات")
        : "قيد التجهيز قبل التفعيل";

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-home-page">
            <header className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6" data-testid="daily-accounting-header">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="flex flex-wrap items-center gap-2">
                            <span className={"rounded-full px-3 py-1 text-xs font-extrabold " + (safeActive ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900")}>
                                {safeActive ? "المحاسبة تعمل تلقائيًا" : "Preview / تجهيز"}
                            </span>
                            <span className="text-xs font-bold text-slate-500">{stateLabel}</span>
                        </div>
                        <h1 className="mt-3 text-2xl font-black text-slate-950 sm:text-3xl">المحاسبة اليومية</h1>
                        <p className="mt-2 max-w-3xl text-sm font-semibold leading-7 text-slate-600">
                            ارفع أدلة اليوم فقط. ميزان يتولى الطلبات، الضريبة، الذمم، البنك والتسويات تلقائيًا ويعرض لك الحالات التي لا يستطيع حسمها بأمان.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {canManagePermissions && (
                            <button type="button" onClick={onOpenPermissions} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700">
                                <UserGear size={18} /> الصلاحيات
                            </button>
                        )}
                        <button type="button" onClick={load} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 disabled:opacity-50">
                            <ArrowClockwise size={18} className={loading ? "animate-spin" : ""} /> تحديث
                        </button>
                    </div>
                </div>
            </header>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SummaryCard label="وصل البنك اليوم" value={formatMoney(receivedToday)} hint="من أدلة البنك المسجلة" Icon={Bank} tone="emerald" testid="daily-summary-received" />
                <SummaryCard label="طلبات تنتظر دليلًا" value={orderWaiting.toLocaleString("en-US")} hint="الدليل المكتمل يرحل تلقائيًا" Icon={ShoppingCart} tone={orderWaiting ? "amber" : "emerald"} testid="daily-summary-orders" />
                <SummaryCard label="أرصدة البنوك" value={formatMoney(status?.balance_visibility?.banks)} hint={safeActive ? "من قيود ميزان 2 الموثقة" : "تظهر بعد اكتمال التفعيل"} Icon={Wallet} tone="sky" testid="daily-summary-banks" />
                <SummaryCard label="يحتاج قرارك" value={pending.toLocaleString("en-US")} hint="أدلة ناقصة أو تعارضات فقط" Icon={ClipboardText} tone={pending ? "rose" : "emerald"} testid="daily-summary-review" />
            </div>

            <section data-testid="daily-accounting-actions">
                <div className="mb-3">
                    <h2 className="text-lg font-black text-slate-950">إدخالات اليوم</h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">كل ما تحتاجه للتشغيل اليومي من شاشة واحدة.</p>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    <ActionCard title="رفع طلبات سلة" detail="يحفظ الدليل ثم يرحل المبيعات الآمنة والضريبة والذمم تلقائيًا." Icon={ShoppingCart} badge="تلقائي" onClick={() => setActiveAction("orders")} />
                    <ActionCard title="رفع كشف البنك" detail="يقرأ الحركات ويحفظها كدليل؛ لا يخمّن نوع الحركة غير الواضحة." Icon={Bank} badge="MZ2" onClick={() => setActiveAction("bank")} />
                    <ActionCard title="مراجعة إيصالات التحويل" detail="يعرض بنك الطلب ومبلغه؛ راجع الإيصال والحركة التي وصلت للبنك ثم اعتمد." Icon={Receipt} onClick={() => setActiveAction("bank-transfer-review")} />
                    <ActionCard title="رفع ملف تسوية" detail="سلة/تمارا/تابي/إمكان؛ المطابقة والترحيل عند اكتمال الدليل." Icon={UploadSimple} onClick={() => setActiveAction("settlement")} />
                    <ActionCard title="مبلغ واصل يدويًا" detail="للتحويل المنفرد عندما لا ترفع كشف البنك الكامل." Icon={Wallet} onClick={() => setActiveAction("movement")} />
                    <ActionCard title="الرواتب والسلف" detail="استحقاق الرواتب وربط الصرف والسلف والعهد بحركات البنك." Icon={UsersThree} onClick={() => setActiveAction("payroll")} />
                </div>
            </section>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,.9fr)]">
                <ExceptionList status={status} drafts={drafts} receipts={receipts} orderQueue={orderQueue} totalReviewCount={pending} />
                <RecentActivity drafts={drafts} receipts={receipts} />
            </div>

            <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4" data-testid="daily-accounting-advanced">
                <button type="button" onClick={() => setAdvancedOpen((value) => !value)} className="flex w-full items-center justify-between gap-3 text-right">
                    <div>
                        <div className="flex items-center gap-2 text-sm font-black text-slate-800"><GearSix size={20} /> التفاصيل والإعدادات المتقدمة</div>
                        <p className="mt-1 text-xs font-semibold text-slate-500">للإعداد والتدقيق: الشحن/COD، الأرصدة الافتتاحية، القيود، الفترات وإيقاف الكتابات.</p>
                    </div>
                    <span className="text-xs font-extrabold text-slate-500">{advancedOpen ? "إخفاء" : "عرض"}</span>
                </button>
                {advancedOpen && (
                    <div className="mt-4 space-y-4 border-t border-slate-200 pt-4">
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                            {[
                                ["shipping-cod", "الشحن وCOD"],
                                ["settlements", "التسويات التفصيلية"],
                                ["financial-movements", "الحركات المالية"],
                                ["journals-reports", "القيود والتقارير"],
                                ["opening-balances", "الأرصدة الافتتاحية"],
                            ].map(([id, label]) => {
                                const page = ACCOUNTING_PAGES.find((item) => item.id === id);
                                return <Link key={id} to={page.to} className="rounded-xl border border-slate-200 bg-white p-3 text-xs font-extrabold text-slate-700 hover:border-emerald-300">{label}</Link>;
                            })}
                        </div>
                        {(user?.is_owner === true || String(user?.role || "").toLowerCase() === "owner") && (
                            <div className="grid gap-4 xl:grid-cols-2">
                                <AccountingWriteControl />
                                <AccountingPeriods />
                            </div>
                        )}
                    </div>
                )}
            </section>

            {activeAction === "orders" && (
                <Modal title="رفع طلبات سلة" subtitle="ملف واحد؛ ميزان يحفظ الأدلة ويرحل فقط الطلبات الآمنة من نفس الملف." onClose={() => setActiveAction("")} testid="daily-accounting-orders-modal">
                    <SallaOrdersUploadForm permissions={accountingPermissions} onSaved={load} />
                </Modal>
            )}
            {activeAction === "bank" && (
                <Modal title="رفع كشف البنك" subtitle="كل صف يبقى دليلًا؛ الحالات الواضحة فقط تنتقل للمسار المناسب." onClose={() => setActiveAction("")} testid="daily-accounting-bank-modal">
                    <AccountingDailyMovements accountingPermissions={accountingPermissions} />
                </Modal>
            )}
            {activeAction === "bank-transfer-review" && (
                <Modal title="مراجعة إيصالات التحويل البنكي" subtitle="البنك والمبلغ من الطلب؛ راجع الإيصال والحركة البنكية الفعلية ثم اعتمد فقط." onClose={() => setActiveAction("")} testid="daily-accounting-bank-transfer-review-modal">
                    <AccountingBankTransferReceipts accountingPermissions={accountingPermissions} />
                </Modal>
            )}
            {activeAction === "movement" && (
                <Modal title="مبلغ واصل يدويًا" subtitle="أدخل الواقع البنكي فقط، واترك القيد والمطابقة لميزان 2." onClose={() => setActiveAction("")} testid="daily-accounting-movement-modal">
                    <ReceiptForm context={context} permissions={accountingPermissions} onSaved={load} />
                </Modal>
            )}
            {activeAction === "settlement" && (
                <Modal title="رفع ملف تسوية" subtitle="الملف الأصلي يكفي؛ لا تحتاج إدخال مكونات القيد يدويًا." onClose={() => setActiveAction("")} testid="daily-accounting-settlement-modal">
                    <SettlementUploadForm context={context} permissions={accountingPermissions} onSaved={load} />
                </Modal>
            )}
            {activeAction === "payroll" && (
                <Modal title="الرواتب والسلف والعهد" subtitle="استحقاق الراتب مستقل، والدفع الفعلي مرتبط بكشف البنك." onClose={() => setActiveAction("")} testid="daily-accounting-payroll-modal">
                    <AccountingPayroll accountingPermissions={accountingPermissions} />
                </Modal>
            )}
        </div>
    );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    ClipboardText,
    FileArrowUp,
    GearSix,
    UploadSimple,
    UserGear,
    Wallet,
    WarningCircle,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../../lib/api";
import {
    getAccountingSettlementContext,
    getAccountingSettlementDrafts,
    uploadAccountingSettlementDraft,
} from "../../services/accountingModule";
import UnifiedEntryScreen from "../UnifiedEntryScreen";
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

function todayRiyadh() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date());
}

function errorMessage(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
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

function ActionCard({ title, detail, Icon, onClick }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="group flex min-h-32 w-full items-start justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-5 text-right shadow-sm transition hover:border-emerald-300 hover:shadow-md"
        >
            <div>
                <div className="text-base font-black text-slate-950">{title}</div>
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
            <div className="my-4 w-full max-w-5xl overflow-hidden rounded-2xl bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()} dir="rtl">
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
            toast.success("تم حفظ الحركة. سيطابقها ميزان مع ملف التسوية عند توفره.");
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
                أدخل ما ظهر فعليًا في البنك. لا تحتاج اختيار مدين أو دائن أو حساب محاسبي؛ ميزان يتولى القيد بعد اكتمال المطابقة.
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
            {!binding?.bank_account_id && (
                <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">
                    يلزم ربط بنك المنصة مرة واحدة من الإعدادات المتقدمة قبل الحفظ.
                </p>
            )}
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
            if (draft?.status === "needs_review") toast.warning("تم رفع الملف. يوجد عنصر يحتاج منك قبل الإكمال.");
            else if (draft?.duplicate) toast.info("هذا الملف موجود مسبقًا؛ لم يُكرر.");
            else toast.success("تم رفع الملف وبدأت المطابقة تلقائيًا.");
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
                ارفع الملف كما نزل من المنصة. ميزان يقرأه ويطابق الطلبات والمبلغ البنكي ويحسب الرسوم والضريبة. ستظهر لك فقط الحالات التي تحتاج قرارًا.
            </div>
            <label className="flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-6 text-center transition hover:border-emerald-400 hover:bg-emerald-50">
                <FileArrowUp size={34} weight="duotone" className="text-emerald-800" />
                <span className="mt-3 text-sm font-black text-slate-900">{file?.name || "اختر ملف التسوية"}</span>
                <span className="mt-1 text-xs font-semibold text-slate-500">ملف Excel الأصلي من سلة أو تمارا أو تابي أو إمكان</span>
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
            {!binding?.bank_account_id && (
                <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">
                    اربط بنك المنصة مرة واحدة من الإعدادات المتقدمة، وبعدها لن تحتاج اختياره عند كل ملف.
                </p>
            )}
            <div className="flex justify-end">
                <button disabled={busy || !file || !canCreate || !binding?.bank_account_id} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                    {busy ? "جاري القراءة والمطابقة…" : "رفع وبدء المطابقة"}
                </button>
            </div>
        </form>
    );
}

function ExceptionList({ status, drafts, receipts }) {
    const taskItems = (status?.tasks || []).slice(0, 8).map((task) => {
        const page = ACCOUNTING_PAGES.find((row) => row.id === task.page) || ACCOUNTING_PAGES[0];
        return { id: "task:" + task.id, title: task.title, detail: task.detail, to: page.to, informational: false };
    });
    const draftItems = drafts.filter((draft) => REVIEW_STATUSES.has(draft.status)).slice(0, 6).map((draft) => ({
        id: "settlement:" + draft.id,
        title: (PROVIDERS[draft.provider] || draft.provider || "تسوية") + " — " + (STATUS_LABELS[draft.status] || draft.status),
        detail: draft.statement_reference || draft.source_snapshot?.filename || "تسوية تحتاج متابعة",
        to: "/integrations-v2?workspace=financial&page=settlements",
        informational: false,
    }));
    const receiptItems = receipts.filter((item) => item.status === "waiting_statement").slice(0, 4).map((item) => ({
        id: "receipt:" + item.id,
        title: "مبلغ واصل من " + (PROVIDERS[item.provider] || item.provider),
        detail: formatMoney(item.amount) + " — ينتظر ملف المنصة للمطابقة",
        to: "/integrations-v2?workspace=financial&page=financial-movements",
        informational: true,
    }));
    const items = [...taskItems, ...draftItems, ...receiptItems].slice(0, 10);

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="daily-accounting-exceptions">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-black text-slate-950">يحتاج منك</h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">العمليات السليمة تختفي من قائمة العمل؛ تظهر الاستثناءات فقط.</p>
                </div>
                <span className="rounded-full bg-amber-100 px-3 py-1 font-mono text-xs font-black text-amber-900" dir="ltr">
                    {items.filter((item) => !item.informational).length}
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

function RecentActivity({ drafts, receipts, movements }) {
    const events = [
        ...drafts.map((row) => ({
            id: "draft:" + row.id,
            at: row.updated_at || row.created_at || "",
            title: (PROVIDERS[row.provider] || row.provider || "تسوية") + " — " + (STATUS_LABELS[row.status] || row.status),
            detail: row.statement_reference || row.source_snapshot?.filename || "ملف تسوية",
            tone: row.status === "posted" ? "ok" : REVIEW_STATUSES.has(row.status) ? "warn" : "neutral",
        })),
        ...receipts.map((row) => ({
            id: "receipt:" + row.id,
            at: row.created_at || row.received_on || "",
            title: "مبلغ واصل — " + (PROVIDERS[row.provider] || row.provider),
            detail: formatMoney(row.amount) + " · " + (row.status === "posted" ? "تمت تسويته" : row.status === "linked" ? "تمت مطابقته" : "بانتظار كشف المنصة"),
            tone: row.status === "posted" ? "ok" : "neutral",
        })),
        ...movements.map((row) => ({
            id: "movement:" + row.id,
            at: row.created_at || row.doc_date || "",
            title: row.description || row.notes || "حركة مالية",
            detail: formatMoney(row.total_amount || row.amount),
            tone: row.posting_status === "posted_failed" ? "warn" : row.posting_status === "posted_to_gl" ? "ok" : "neutral",
        })),
    ].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 8);

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="daily-accounting-recent">
            <h2 className="text-lg font-black text-slate-950">آخر العمليات</h2>
            <p className="mt-1 text-xs font-semibold text-slate-500">مختصر لما فعله النظام مؤخرًا، بدون تفاصيل دفتر الأستاذ.</p>
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
    const [movements, setMovements] = useState([]);
    const [loading, setLoading] = useState(true);
    const [activeAction, setActiveAction] = useState("");
    const [advancedOpen, setAdvancedOpen] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        const results = await Promise.allSettled([
            getAccountingSettlementContext(),
            getAccountingSettlementDrafts({ limit: 30 }),
            api.get(BASE + "/bank-receipts"),
            api.get("/financial-movements", { params: { limit: 20 } }),
        ]);
        if (results[0].status === "fulfilled") setContext(results[0].value);
        if (results[1].status === "fulfilled") setDrafts(results[1].value?.items || []);
        if (results[2].status === "fulfilled") setReceipts(results[2].value?.data?.items || []);
        if (results[3].status === "fulfilled") setMovements(results[3].value?.data?.items || []);
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
    const reviewCount = Number(status?.review_count || 0);
    const safeActive = status?.cutover?.safe_active === true;
    const pending = Math.max(reviewCount, settlementsNeedReview);
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
                            سجّل الشيء الذي حدث فقط. ميزان يتولى التصنيف والمطابقة والقيد والضريبة في الخلفية، ويطلب منك القرار عند وجود استثناء حقيقي.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {canManagePermissions && (
                            <button type="button" onClick={onOpenPermissions} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 hover:bg-slate-50">
                                <UserGear size={18} /> الصلاحيات
                            </button>
                        )}
                        <button type="button" onClick={load} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                            <ArrowClockwise size={18} className={loading ? "animate-spin" : ""} /> تحديث
                        </button>
                    </div>
                </div>
            </header>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SummaryCard label="وصل البنك اليوم" value={formatMoney(receivedToday)} hint="من الحركات التي أدخلتها اليوم" Icon={Bank} tone="emerald" testid="daily-summary-received" />
                <SummaryCard label="تسويات تحتاج منك" value={settlementsNeedReview.toLocaleString("en-US")} hint="العمليات السليمة لا تظهر هنا" Icon={UploadSimple} tone={settlementsNeedReview ? "amber" : "emerald"} testid="daily-summary-settlements" />
                <SummaryCard label="أرصدة البنوك" value={formatMoney(status?.balance_visibility?.banks)} hint={safeActive ? "من قيود ميزان 2 الموثقة" : "تظهر بعد اكتمال التفعيل"} Icon={Wallet} tone="sky" testid="daily-summary-banks" />
                <SummaryCard label="يحتاج قرارك" value={pending.toLocaleString("en-US")} hint="أدلة ناقصة أو تعارضات فقط" Icon={ClipboardText} tone={pending ? "rose" : "emerald"} testid="daily-summary-review" />
            </div>

            <section data-testid="daily-accounting-actions">
                <div className="mb-3">
                    <h2 className="text-lg font-black text-slate-950">ماذا حدث اليوم؟</h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">ثلاثة مداخل أساسية بدل التنقل بين صفحات المحاسبة.</p>
                </div>
                <div className="grid gap-3 lg:grid-cols-3">
                    <ActionCard title="إضافة حركة مالية" detail="مصروف، فاتورة مورد، تحويل، راتب أو أي حركة تشغيلية. يفتح النموذج داخل نفس الشاشة." Icon={Wallet} onClick={() => setActiveAction("movement")} />
                    <ActionCard title="مبلغ وصل إلى البنك" detail="أدخل المبلغ والرسالة فقط؛ البنك مرتبط مسبقًا والمنصة تُطابق لاحقًا مع كشفها." Icon={Bank} onClick={() => setActiveAction("receipt")} />
                    <ActionCard title="رفع ملف تسوية" detail="ارفع ملف سلة أو تمارا أو تابي أو إمكان، ويبدأ ميزان القراءة والمطابقة تلقائيًا." Icon={UploadSimple} onClick={() => setActiveAction("settlement")} />
                </div>
                <div className="mt-3 flex items-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-600">
                    <WarningCircle size={18} className="shrink-0 text-slate-500" />
                    استيراد كشف البنك الجماعي لم يُربط بعد بمسار MZ2؛ لن نعرض زرًا وهميًا قبل بناء استيراد آمن ومطابقة قابلة للتدقيق.
                </div>
            </section>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,.9fr)]">
                <ExceptionList status={status} drafts={drafts} receipts={receipts} />
                <RecentActivity drafts={drafts} receipts={receipts} movements={movements} />
            </div>

            <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4" data-testid="daily-accounting-advanced">
                <button type="button" onClick={() => setAdvancedOpen((value) => !value)} className="flex w-full items-center justify-between gap-3 text-right">
                    <div>
                        <div className="flex items-center gap-2 text-sm font-black text-slate-800"><GearSix size={20} /> التفاصيل والإعدادات المتقدمة</div>
                        <p className="mt-1 text-xs font-semibold text-slate-500">للمحاسب أو المالك فقط: التسويات التفصيلية، القيود والتقارير، الفترات، الإيقاف وإعادة المعالجة.</p>
                    </div>
                    <span className="text-xs font-extrabold text-slate-500">{advancedOpen ? "إخفاء" : "عرض"}</span>
                </button>
                {advancedOpen && (
                    <div className="mt-4 space-y-4 border-t border-slate-200 pt-4">
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                            {[
                                ["settlements", "التسويات التفصيلية"],
                                ["financial-movements", "الحركات والاستردادات"],
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

            {activeAction === "movement" && (
                <Modal title="إضافة حركة مالية" subtitle="النموذج الحالي يظهر داخل نفس مساحة المحاسبة؛ سنبسّط حقوله في المرحلة التالية." onClose={() => setActiveAction("")} testid="daily-accounting-movement-modal">
                    <UnifiedEntryScreen />
                </Modal>
            )}
            {activeAction === "receipt" && (
                <Modal title="مبلغ وصل إلى البنك" subtitle="أدخل الواقع البنكي فقط، واترك القيد والمطابقة لميزان." onClose={() => setActiveAction("")} testid="daily-accounting-receipt-modal">
                    <ReceiptForm context={context} permissions={accountingPermissions} onSaved={load} />
                </Modal>
            )}
            {activeAction === "settlement" && (
                <Modal title="رفع ملف تسوية" subtitle="الملف الأصلي يكفي؛ لا تحتاج إدخال مكونات القيد يدويًا." onClose={() => setActiveAction("")} testid="daily-accounting-settlement-modal">
                    <SettlementUploadForm context={context} permissions={accountingPermissions} onSaved={load} />
                </Modal>
            )}
        </div>
    );
}

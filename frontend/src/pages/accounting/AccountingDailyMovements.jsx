import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    FileArrowUp,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    confirmAccountingDailyMovementProvider,
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
    const canImport = accountingPermissions.includes("accounting.movements.import");

    async function refresh() {
        const [nextContext, nextRows] = await Promise.all([
            getAccountingDailyMovementContext(),
            getAccountingDailyMovements({ limit: 200 }),
        ]);
        setContext(nextContext);
        setItems(nextRows?.items || []);
        setBankAccountId((current) => current || nextContext?.banks?.[0]?.id || "");
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
                            <tr><td colSpan={6} className="p-8 text-center font-semibold text-slate-400">لم يُرفع كشف بنك MZ2 بعد.</td></tr>
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
                                <td className="min-w-56 p-3">
                                    {row.status === "needs_review" && row.suggested_provider ? (
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
                                            ستظهر إجراءات المصروف/راتب/سلفة وغيرها عند اكتمال مسارات MZ2 الأصلية.
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

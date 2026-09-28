
import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    FileImage,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    approveAccountingBankTransferReview,
    getAccountingBankTransferReviews,
} from "../../services/accountingModule";
import { formatMoney } from "./AccountingShared";

const REASON_LABELS = {
    order_evidence_conflict: "يوجد تعارض في دليل الطلب.",
    order_is_not_bank_transfer: "طريقة الدفع ليست تحويلًا بنكيًا.",
    bank_transfer_refund_requires_review: "الطلب يحتوي استردادًا ويحتاج مسار مراجعة مستقل.",
    fulfilment_timestamp_required: "ينتظر إثبات التسليم قبل إثبات البيع.",
    customer_transfer_receipt_missing: "صورة إيصال التحويل غير موجودة في طلب سلة.",
    receiving_bank_name_missing: "اسم البنك المستلم غير موجود في طلب سلة.",
    matching_bank_movement_missing: "لم تصل بعد حركة بنك مطابقة للمبلغ.",
    bank_transfer_after_delivery_requires_receivable_workflow: "التحويل البنكي وصل بعد تاريخ التسليم؛ يحتاج معالجة ذمم مستقلة قبل الترحيل.",
};

function reasonText(code) {
    return REASON_LABELS[code] || code;
}

function ReviewCard({ row, canApprove, onApproved }) {
    const [movementId, setMovementId] = useState("");
    const [busy, setBusy] = useState(false);
    const selected = useMemo(
        () => (row.bank_movements || []).find((item) => item.id === movementId),
        [row.bank_movements, movementId],
    );

    async function approve() {
        if (!movementId) return toast.error("اختر التحويل الذي وصل فعليًا إلى البنك");
        setBusy(true);
        try {
            await approveAccountingBankTransferReview(row.evidence_id, movementId);
            toast.success("تمت الموافقة: ربط الإيصال بالتحويل وترحيل الأثر المحاسبي مرة واحدة.");
            await onApproved();
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(
                typeof detail === "string"
                    ? detail
                    : detail?.message || detail?.code || "تعذر اعتماد التحويل",
                { duration: 9000 },
            );
        } finally {
            setBusy(false);
        }
    }

    if (row.state === "approved") {
        return (
            <article className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4" data-testid={"bank-transfer-approved-" + row.order_number}>
                <div className="flex items-center gap-2 text-sm font-black text-emerald-900">
                    <CheckCircle size={21} weight="fill" /> الطلب {row.order_number} — تمت المراجعة والترحيل
                </div>
                <div className="mt-2 grid gap-2 text-xs text-emerald-900 sm:grid-cols-3">
                    <div>المبلغ: <strong>{formatMoney(row.order_amount)}</strong></div>
                    <div>البنك في الإيصال: <strong>{row.receipt_bank_name || "—"}</strong></div>
                    <div>مرجع البنك: <strong>{row.review?.bank_reference || "—"}</strong></div>
                </div>
            </article>
        );
    }

    return (
        <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white" data-testid={"bank-transfer-review-" + row.order_number}>
            <div className="border-b border-slate-100 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="text-base font-black text-slate-950">طلب {row.order_number}</h3>
                        <p className="mt-1 text-xs font-semibold text-slate-500">راجع الإيصال أولًا، ثم اختر التحويل الذي وصل فعلًا إلى البنك.</p>
                    </div>
                    <span className={"rounded-full px-3 py-1 text-xs font-extrabold " + (row.state === "ready_for_review" ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-700")}>
                        {row.state === "ready_for_review" ? "جاهز للمراجعة" : "بانتظار دليل"}
                    </span>
                </div>
            </div>

            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,.9fr)_minmax(0,1.1fr)]">
                <div className="space-y-3">
                    <div className="grid gap-2 sm:grid-cols-2">
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                            <div className="text-[11px] font-bold text-slate-500">المبلغ الظاهر للمراجعة</div>
                            <div className="mt-1 font-mono text-xl font-black text-slate-950" dir="ltr">{formatMoney(row.order_amount)}</div>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                            <div className="text-[11px] font-bold text-slate-500">اسم البنك في طلب سلة</div>
                            <div className="mt-1 text-sm font-black text-slate-950">{row.receipt_bank_name || "غير متوفر"}</div>
                        </div>
                    </div>

                    {row.receipt_url ? (
                        <div className="rounded-xl border border-slate-200 p-3">
                            <div className="mb-2 flex items-center gap-2 text-xs font-black text-slate-700">
                                <FileImage size={18} /> إيصال التحويل المرفوع
                            </div>
                            <a href={row.receipt_url} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
                                <img src={row.receipt_url} alt={"إيصال تحويل الطلب " + row.order_number} className="max-h-80 w-full object-contain" />
                            </a>
                            <a href={row.receipt_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs font-extrabold text-emerald-800 underline">
                                فتح الإيصال بحجم كامل
                            </a>
                        </div>
                    ) : (
                        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">
                            <WarningCircle size={18} className="shrink-0" /> لا توجد صورة إيصال متاحة لهذا الطلب.
                        </div>
                    )}
                </div>

                <div className="space-y-3">
                    <div className="rounded-xl border border-sky-200 bg-sky-50 p-3">
                        <div className="flex items-center gap-2 text-xs font-black text-sky-900">
                            <Bank size={18} /> التحويل الذي وصل إلى البنك
                        </div>
                        <p className="mt-1 text-[11px] font-semibold leading-5 text-sky-800">
                            لا يختار ميزان التحويل تلقائيًا بالمبلغ فقط. أنت تحدد الحركة التي قارنتها بالإيصال.
                        </p>
                    </div>

                    {row.state === "ready_for_review" ? (
                        <>
                            <label className="block text-xs font-extrabold text-slate-700">
                                اختر الحركة البنكية الفعلية
                                <select
                                    value={movementId}
                                    onChange={(event) => setMovementId(event.target.value)}
                                    className="mt-1 min-h-12 w-full rounded-xl border border-slate-200 bg-white px-3"
                                    data-testid={"bank-transfer-movement-" + row.order_number}
                                >
                                    <option value="">اختر التحويل</option>
                                    {(row.bank_movements || []).map((movement) => (
                                        <option key={movement.id} value={movement.id}>
                                            {movement.movement_date} · {movement.bank_account_name || movement.bank_account_id} · {formatMoney(movement.amount)} · {movement.reference || movement.description || "بدون مرجع"}
                                        </option>
                                    ))}
                                </select>
                            </label>

                            {selected && (
                                <div className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs sm:grid-cols-2">
                                    <div>البنك الفعلي: <strong>{selected.bank_account_name || selected.bank_account_id}</strong></div>
                                    <div>المبلغ الفعلي: <strong>{formatMoney(selected.amount)}</strong></div>
                                    <div>تاريخ الوصول: <strong>{selected.movement_date}</strong></div>
                                    <div>المرجع: <strong>{selected.reference || "غير متوفر"}</strong></div>
                                </div>
                            )}

                            <button
                                type="button"
                                onClick={approve}
                                disabled={!canApprove || !movementId || busy}
                                className="min-h-12 w-full rounded-xl bg-emerald-800 px-4 text-sm font-black text-white disabled:opacity-40"
                                data-testid={"approve-bank-transfer-" + row.order_number}
                            >
                                {busy ? "جاري الاعتماد…" : "موافق — الإيصال والتحويل متطابقان"}
                            </button>
                            <p className="text-[11px] font-semibold leading-5 text-slate-500">
                                بعد الموافقة يستهلك ميزان حركة البنك مرة واحدة، ويثبت التحويل المقدم ثم البيع عند التسليم حسب الضريبة المعتمدة في ميزان 2.
                            </p>
                        </>
                    ) : (
                        <div className="space-y-2">
                            {(row.reasons || []).map((reason) => (
                                <div key={reason} className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-900">
                                    <WarningCircle size={17} className="mt-0.5 shrink-0" /> {reasonText(reason)}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </article>
    );
}

export default function AccountingBankTransferReviews({ accountingPermissions = [] }) {
    const [data, setData] = useState({ items: [], ready_count: 0, waiting_count: 0, approved_count: 0 });
    const [loading, setLoading] = useState(true);
    const canApprove = accountingPermissions.includes("accounting.receivables.post");

    async function refresh() {
        setLoading(true);
        try {
            setData(await getAccountingBankTransferReviews(200));
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(typeof detail === "string" ? detail : detail?.message || "تعذر تحميل مراجعات التحويل البنكي");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { refresh(); }, []);

    return (
        <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5" data-testid="accounting-bank-transfer-reviews">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-xl font-black text-slate-950">مراجعة إيصالات التحويل البنكي</h2>
                    <p className="mt-1 text-xs font-semibold leading-6 text-slate-500">
                        الإيصال يعرض المبلغ واسم البنك. المراجع يقارن الإيصال بالتحويل الذي وصل فعليًا للبنك، ثم يضغط موافق.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-black text-amber-900">{data.ready_count || 0} جاهز</span>
                    <button type="button" onClick={refresh} disabled={loading} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-extrabold disabled:opacity-40">
                        <ArrowClockwise size={16} className={loading ? "animate-spin" : ""} /> تحديث
                    </button>
                </div>
            </div>

            <div className="space-y-4">
                {(data.items || []).length === 0 ? (
                    <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm font-extrabold text-emerald-800">
                        <CheckCircle size={20} weight="fill" /> لا توجد تحويلات بنكية تحتاج مراجعة الآن.
                    </div>
                ) : (data.items || []).map((row) => (
                    <ReviewCard key={row.evidence_id} row={row} canApprove={canApprove} onApproved={refresh} />
                ))}
            </div>
        </section>
    );
}

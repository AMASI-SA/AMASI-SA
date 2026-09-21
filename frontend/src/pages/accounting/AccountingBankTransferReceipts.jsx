import { useEffect, useMemo, useState } from "react";
import {
    Bank,
    CheckCircle,
    FileArrowUp,
    Receipt,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    approveAccountingBankTransferReceipt,
    getAccountingBankTransferCandidates,
    getAccountingBankTransferReceiptFile,
    getAccountingBankTransferReviews,
    uploadAccountingBankTransferReceipt,
} from "../../services/accountingModule";
import { formatMoney, LoadingBlock } from "./AccountingShared";

const STATE_LABELS = {
    waiting_receipt: "بانتظار رفع الإيصال",
    pending_approval: "بانتظار مراجعة الإيصال والبنك",
    confirmed_waiting_delivery: "وصل المبلغ · بانتظار التوصيل",
    recognized: "مكتمل",
    needs_bank_mapping: "ربط البنك مطلوب مرة واحدة",
    needs_review: "يحتاج مراجعة",
};

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) return detail.code;
    return fallback;
}

function ReceiptPreview({ reviewId, filename }) {
    const [url, setUrl] = useState("");
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        let objectUrl = "";
        setLoading(true);
        getAccountingBankTransferReceiptFile(reviewId)
            .then((blob) => {
                if (!active) return;
                objectUrl = URL.createObjectURL(blob);
                setUrl(objectUrl);
            })
            .catch(() => {
                if (active) setUrl("");
            })
            .finally(() => {
                if (active) setLoading(false);
            });
        return () => {
            active = false;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
    }, [reviewId]);

    if (loading) return <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-xs font-bold text-slate-500">جاري تحميل الإيصال…</div>;
    if (!url) return <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs font-bold text-rose-800">تعذر عرض الإيصال.</div>;

    const isPdf = String(filename || "").toLowerCase().endsWith(".pdf");
    return (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
            {isPdf ? (
                <iframe title="إيصال التحويل" src={url} className="h-96 w-full bg-white" />
            ) : (
                <img src={url} alt="إيصال التحويل البنكي" className="max-h-96 w-full object-contain bg-white" />
            )}
        </div>
    );
}

function ReviewCard({ item, permissions, onDone }) {
    const [file, setFile] = useState(null);
    const [fileKey, setFileKey] = useState(0);
    const [notes, setNotes] = useState("");
    const [candidates, setCandidates] = useState(null);
    const [selectedMovement, setSelectedMovement] = useState("");
    const [busy, setBusy] = useState("");
    const canUpload = permissions.includes("accounting.movements.import");
    const canApprove = permissions.includes("accounting.receivables.post");
    const review = item.review;

    async function loadCandidates() {
        if (!review?.id) return;
        try {
            const result = await getAccountingBankTransferCandidates(review.id, { limit: 30 });
            setCandidates(result);
            const exact = (result?.items || []).filter((row) => row.amount_matches);
            if (exact.length === 1) setSelectedMovement(exact[0].id);
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل الحركات البنكية"));
        }
    }

    useEffect(() => {
        if (review?.id && review.status === "pending_approval") loadCandidates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [review?.id, review?.status]);

    async function upload(event) {
        event.preventDefault();
        if (!canUpload) return toast.error("لا تملك صلاحية رفع الإيصال");
        if (!file) return toast.error("اختر صورة أو PDF الإيصال");
        setBusy("upload");
        try {
            await uploadAccountingBankTransferReceipt(item.evidence_id, file, notes.trim());
            toast.success("تم حفظ الإيصال. راجع الآن الحركة التي وصلت للبنك ثم اعتمد.");
            setFile(null);
            setFileKey((value) => value + 1);
            setNotes("");
            await onDone();
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ الإيصال"));
        } finally {
            setBusy("");
        }
    }

    async function approve() {
        if (!canApprove) return toast.error("لا تملك صلاحية اعتماد التحويل");
        if (!selectedMovement) return toast.error("اختر الحركة التي وصلت فعليًا في البنك");
        setBusy("approve");
        try {
            const result = await approveAccountingBankTransferReceipt(review.id, selectedMovement);
            if (result.status === "recognized") {
                toast.success("تم اعتماد وصول التحويل وإثبات البيع.");
            } else {
                toast.success("تم اعتماد وصول التحويل. سيحوّل ميزان الدفعة إلى مبيعات عند التوصيل.");
            }
            await onDone();
        } catch (error) {
            toast.error(errorText(error, "تعذر اعتماد التحويل"), { duration: 9000 });
        } finally {
            setBusy("");
        }
    }

    const exactCandidates = useMemo(
        () => (candidates?.items || []).filter((row) => row.amount_matches),
        [candidates],
    );

    return (
        <article className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid={"bank-transfer-review-" + item.order_number}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="text-xs font-bold text-slate-500">طلب سلة</div>
                    <div className="mt-1 text-lg font-black text-slate-950" dir="ltr">#{item.order_number}</div>
                </div>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-extrabold text-slate-700">
                    {STATE_LABELS[item.state] || item.state}
                </span>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl bg-emerald-50 p-3">
                    <div className="text-[10px] font-bold text-emerald-700">مبلغ الطلب</div>
                    <div className="mt-1 text-lg font-black text-emerald-950">{formatMoney(item.expected_amount)}</div>
                </div>
                <div className="rounded-xl bg-sky-50 p-3">
                    <div className="text-[10px] font-bold text-sky-700">البنك الذي اختاره العميل</div>
                    <div className="mt-1 text-sm font-black text-sky-950">{item.selected_bank || "غير ظاهر"}</div>
                </div>
                <div className="rounded-xl bg-slate-50 p-3">
                    <div className="text-[10px] font-bold text-slate-500">حساب ميزان المطابق</div>
                    <div className="mt-1 text-sm font-black text-slate-900">{item.bank_resolution?.bank_account_name || "يحتاج ربط البنك مرة واحدة"}</div>
                </div>
            </div>

            {item.state === "needs_bank_mapping" && (
                <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900">
                    <WarningCircle size={18} className="mt-0.5 shrink-0" />
                    اسم البنك مأخوذ من الطلب، لكنه غير مربوط بحساب واحد واضح في ميزان. اربطه مرة واحدة من الإعدادات؛ لن تختار البنك لكل طلب.
                </div>
            )}

            {!review && item.state === "waiting_receipt" && (
                <form onSubmit={upload} className="space-y-3">
                    <label className="flex min-h-32 cursor-pointer items-center justify-center gap-3 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 p-4 text-center">
                        <FileArrowUp size={28} weight="duotone" className="text-emerald-800" />
                        <div>
                            <div className="text-sm font-black text-slate-900">{file?.name || "ارفع إيصال العميل"}</div>
                            <div className="mt-1 text-[10px] font-semibold text-slate-500">صورة أو PDF. البنك والمبلغ لا تُكتب من جديد.</div>
                        </div>
                        <input key={fileKey} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" className="sr-only" onChange={(event) => setFile(event.target.files?.[0] || null)} />
                    </label>
                    <input value={notes} onChange={(event) => setNotes(event.target.value)} className="min-h-10 w-full rounded-lg border border-slate-200 px-3 text-xs" placeholder="ملاحظة اختيارية" />
                    <div className="flex justify-end">
                        <button disabled={!file || !canUpload || busy === "upload"} className="min-h-10 rounded-xl bg-emerald-800 px-4 text-xs font-black text-white disabled:opacity-40">
                            {busy === "upload" ? "جاري الحفظ…" : "حفظ الإيصال للمراجعة"}
                        </button>
                    </div>
                </form>
            )}

            {review && ["pending_approval", "confirmed_waiting_delivery", "recognized"].includes(review.status) && (
                <div className="space-y-4">
                    <div>
                        <div className="mb-2 flex items-center gap-2 text-sm font-black text-slate-900"><Receipt size={19} /> الإيصال المرفوع</div>
                        <ReceiptPreview reviewId={review.id} filename={review.receipt_filename} />
                    </div>

                    {review.status === "pending_approval" && (
                        <>
                            <div>
                                <div className="flex items-center gap-2 text-sm font-black text-slate-900"><Bank size={19} /> الحركة التي وصلت فعليًا في البنك</div>
                                <p className="mt-1 text-xs font-semibold text-slate-500">
                                    قارن الإيصال بالحركة البنكية. الاعتماد لا يعمل إلا لحركة واردة في نفس البنك وبنفس مبلغ الطلب ولم تُستخدم من قبل.
                                </p>
                            </div>
                            {!candidates ? (
                                <button type="button" onClick={loadCandidates} className="min-h-10 rounded-xl border border-slate-200 px-4 text-xs font-extrabold">تحميل حركات البنك</button>
                            ) : (candidates?.items || []).length === 0 ? (
                                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs font-bold leading-6 text-amber-900">
                                    لا توجد حركة واردة في هذا البنك بعد. ارفع كشف البنك أولًا أو انتظر ظهور التحويل، ثم ارجع واضغط تحديث.
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {(candidates?.items || []).map((movement) => (
                                        <label key={movement.id} className={"flex items-start gap-3 rounded-xl border p-3 " + (
                                            movement.amount_matches
                                                ? selectedMovement === movement.id
                                                    ? "cursor-pointer border-emerald-400 bg-emerald-50"
                                                    : "cursor-pointer border-slate-200 bg-white"
                                                : "cursor-not-allowed border-rose-200 bg-rose-50"
                                        )}>
                                            <input
                                                type="radio"
                                                name={"movement-" + review.id}
                                                value={movement.id}
                                                checked={selectedMovement === movement.id}
                                                disabled={!movement.amount_matches}
                                                onChange={() => movement.amount_matches && setSelectedMovement(movement.id)}
                                                className="mt-1"
                                            />
                                            <div className="min-w-0 flex-1">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className={"font-mono text-sm font-black " + (movement.amount_matches ? "text-emerald-900" : "text-rose-900")}>{formatMoney(movement.amount)}</span>
                                                    <span className="text-xs font-bold text-slate-600">{movement.movement_date}</span>
                                                    {!movement.amount_matches && (
                                                        <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-extrabold text-rose-800">المبلغ لا يطابق الطلب</span>
                                                    )}
                                                </div>
                                                <div className="mt-1 text-xs font-semibold text-slate-600">{movement.description || "حركة واردة"}</div>
                                                {movement.reference && <div className="mt-1 font-mono text-[10px] text-slate-400" dir="ltr">{movement.reference}</div>}
                                            </div>
                                        </label>
                                    ))}
                                    {exactCandidates.length === 0 && (
                                        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-900">
                                            توجد حركة/حركات واردة، لكن لا يوجد مبلغ مطابق للطلب؛ لا يمكن الاعتماد حتى تتضح الحالة.
                                        </div>
                                    )}
                                </div>
                            )}
                            <label className="flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs font-extrabold leading-6 text-violet-950">
                                <ShieldCheck size={19} className="mt-0.5 shrink-0" />
                                بالموافقة أنت تؤكد أنك راجعت الإيصال ورأيت نفس المبلغ داخل البنك المحدد في الطلب.
                            </label>
                            <div className="flex justify-end">
                                <button type="button" onClick={approve} disabled={!selectedMovement || !canApprove || busy === "approve"} className="min-h-11 rounded-xl bg-emerald-800 px-5 text-sm font-black text-white disabled:opacity-40">
                                    {busy === "approve" ? "جاري الاعتماد…" : "اعتماد وصول التحويل"}
                                </button>
                            </div>
                        </>
                    )}

                    {review.status === "confirmed_waiting_delivery" && (
                        <div className="flex items-center gap-2 rounded-xl border border-sky-200 bg-sky-50 p-4 text-xs font-extrabold text-sky-900">
                            <CheckCircle size={19} weight="fill" /> تم تأكيد وصول المبلغ للبنك؛ الطلب لم يكتمل توصيله بعد.
                        </div>
                    )}
                    {review.status === "recognized" && (
                        <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-xs font-extrabold text-emerald-900">
                            <CheckCircle size={19} weight="fill" /> تم تأكيد التحويل وإثبات البيع بدون تكرار حركة البنك.
                        </div>
                    )}
                </div>
            )}
        </article>
    );
}

export default function AccountingBankTransferReceipts({ accountingPermissions = [] }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    async function refresh() {
        setLoading(true);
        try {
            setData(await getAccountingBankTransferReviews({ limit: 300 }));
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل مراجعة التحويلات البنكية"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { refresh(); }, []);

    if (loading && !data) return <LoadingBlock label="جاري تحميل التحويلات البنكية…" />;

    const items = data?.items || [];
    return (
        <section className="space-y-4" dir="rtl" data-testid="accounting-bank-transfer-receipts">
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
                <div className="flex items-start gap-3">
                    <Bank size={26} weight="duotone" className="shrink-0 text-emerald-800" />
                    <div>
                        <h2 className="text-xl font-black text-emerald-950">مراجعة إيصالات التحويل البنكي</h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-emerald-900">
                            البنك والمبلغ يأتيان من طلب سلة. راجع صورة الإيصال، ثم طابقها مع الحركة التي وصلت فعليًا في كشف البنك، وبعدها اضغط اعتماد فقط.
                        </p>
                    </div>
                </div>
            </div>

            {items.length === 0 ? (
                <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm font-semibold text-slate-400">
                    لا توجد طلبات حوالة بنكية تحتاج مراجعة.
                </div>
            ) : items.map((item) => (
                <ReviewCard
                    key={item.evidence_id}
                    item={item}
                    permissions={accountingPermissions}
                    onDone={refresh}
                />
            ))}
        </section>
    );
}

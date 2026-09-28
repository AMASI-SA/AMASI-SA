
import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CheckCircle,
    Package,
    Truck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getAccountingDailyMovements,
    getAccountingShippingContext,
    getAccountingShippingRates,
    postAccountingShippingSettlement,
    previewAccountingShippingSettlement,
    processAccountingCourierPending,
    processAccountingStoreDriverPending,
    saveAccountingShippingRate,
} from "../../services/accountingModule";
import { formatMoney, LoadingBlock } from "./AccountingShared";

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) return detail.code;
    return fallback;
}

function localNow() {
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
    }).formatToParts(new Date());
    const part = (type) => parts.find((item) => item.type === type)?.value || "";
    return part("year") + "-" + part("month") + "-" + part("day") + "T" + part("hour") + ":" + part("minute");
}

export default function AccountingShippingP02({ accountingPermissions = [] }) {
    const [context, setContext] = useState(null);
    const [rates, setRates] = useState(null);
    const [movements, setMovements] = useState([]);
    const [driverQueue, setDriverQueue] = useState(null);
    const [courierQueue, setCourierQueue] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");

    const [courierId, setCourierId] = useState("imile");
    const [courierName, setCourierName] = useState("iMile");
    const [aliases, setAliases] = useState("iMile للتوصيل, iMile");
    const [fee, setFee] = useState("17.25");
    const [effectiveAt, setEffectiveAt] = useState(localNow);
    const [evidenceRef, setEvidenceRef] = useState("");
    const [rateReason, setRateReason] = useState("");

    const [movementId, setMovementId] = useState("");
    const [counterpartyType, setCounterpartyType] = useState("store_driver");
    const [counterpartyId, setCounterpartyId] = useState("");
    const [settlementType, setSettlementType] = useState("cod_remittance");
    const [offsetAmount, setOffsetAmount] = useState("0");
    const [settlementReason, setSettlementReason] = useState("");
    const [preview, setPreview] = useState(null);

    const canRules = accountingPermissions.includes("accounting.rules.manage");
    const canPost = accountingPermissions.includes("accounting.settlements.post");

    async function refresh() {
        setLoading(true);
        try {
            const [nextContext, nextRates, nextMovements, nextDrivers, nextCouriers] = await Promise.all([
                getAccountingShippingContext(),
                getAccountingShippingRates(),
                getAccountingDailyMovements({ limit: 300, status: "unclassified" }),
                processAccountingStoreDriverPending({ limit: 200, dryRun: true }),
                processAccountingCourierPending({ limit: 200, dryRun: true }),
            ]);
            setContext(nextContext);
            setRates(nextRates);
            setMovements(nextMovements?.items || []);
            setDriverQueue(nextDrivers);
            setCourierQueue(nextCouriers);
            setCounterpartyId((current) => current || nextContext?.drivers?.[0]?.id || "");
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل الشحن والتحصيل"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { refresh(); }, []);

    const counterparties = useMemo(
        () => counterpartyType === "store_driver"
            ? (context?.drivers || []).map((row) => ({ id: row.id, name: row.name || row.id }))
            : (context?.couriers || []).map((row) => ({ id: row.id, name: row.name || row.id })),
        [context, counterpartyType],
    );

    useEffect(() => {
        if (!counterparties.some((item) => item.id === counterpartyId)) {
            setCounterpartyId(counterparties[0]?.id || "");
        }
    }, [counterparties, counterpartyId]);

    useEffect(() => {
        const movement = movements.find((row) => row.id === movementId);
        if (!movement) return;
        setSettlementType(movement.direction === "out" ? "fee_payment" : "cod_remittance");
        setOffsetAmount("0");
        setPreview(null);
    }, [movementId, movements]);

    async function saveRate(event) {
        event.preventDefault();
        if (!canRules) return toast.error("لا تملك صلاحية قواعد المحاسبة");
        setBusy("rate");
        try {
            await saveAccountingShippingRate({
                courier_id: courierId.trim(),
                name: courierName.trim(),
                aliases: aliases.split(",").map((value) => value.trim()).filter(Boolean),
                total_fee: fee,
                effective_at: effectiveAt + ":00+03:00",
                evidence_ref: evidenceRef.trim(),
                revision: Number(rates?.revision || 0),
                reason: rateReason.trim(),
                tax_treatment: "gross_expense_no_input_vat",
            });
            toast.success("تم اعتماد نسخة سعر جديدة لـMZ2. لا يعاد حساب الشحنات القديمة.");
            setEvidenceRef("");
            setRateReason("");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر حفظ سعر الشحن"));
        } finally {
            setBusy("");
        }
    }

    async function runPending() {
        if (!canPost) return toast.error("لا تملك صلاحية ترحيل التسويات");
        setBusy("pending");
        try {
            const [drivers, couriers] = await Promise.all([
                processAccountingStoreDriverPending({ limit: 500, dryRun: false }),
                processAccountingCourierPending({ limit: 500, dryRun: false }),
            ]);
            toast.success(
                "تم ترحيل "
                + (Number(drivers?.posted_count || 0) + Number(couriers?.posted_count || 0)).toLocaleString("en-US")
                + " حركة شحن/COD آمنة.",
            );
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تشغيل محاسبة الشحن"));
        } finally {
            setBusy("");
        }
    }

    function settlementPayload() {
        return {
            movement_id: movementId,
            counterparty_type: counterpartyType,
            counterparty_id: counterpartyId,
            settlement_type: settlementType,
            offset_amount: settlementType === "net_settlement" ? offsetAmount : "0",
            reason: settlementReason.trim(),
        };
    }

    async function previewSettlement() {
        if (!movementId || !counterpartyId || settlementReason.trim().length < 3) {
            return toast.error("أكمل حركة البنك والطرف والسبب");
        }
        setBusy("preview");
        try {
            const result = await previewAccountingShippingSettlement(settlementPayload());
            setPreview(result);
            if (result?.state === "eligible" || result?.state === "already_posted") {
                toast.success("المعاينة سليمة. لم يُنشأ قيد بعد.");
            } else {
                toast.warning("الحركة غير جاهزة للترحيل.");
            }
        } catch (error) {
            toast.error(errorText(error, "تعذر معاينة التسوية"));
        } finally {
            setBusy("");
        }
    }

    async function postSettlement() {
        if (!canPost || !preview || !["eligible", "already_posted"].includes(preview.state)) return;
        setBusy("post");
        try {
            const result = await postAccountingShippingSettlement(settlementPayload());
            toast.success(result?.state === "already_posted" ? "التسوية مرحلة مسبقًا؛ لم تتكرر." : "تم ترحيل التسوية من حركة البنك نفسها.");
            setPreview(null);
            setMovementId("");
            setSettlementReason("");
            setOffsetAmount("0");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر ترحيل التسوية"));
        } finally {
            setBusy("");
        }
    }

    if (loading && !context) return <LoadingBlock label="جاري تحميل الشحن والتحصيل…" />;

    const p02Ready = context?.p02_enabled === true && context?.p02_activation_ref_present === true;
    const pendingCount =
        Number(driverQueue?.waiting_count || 0)
        + Number(driverQueue?.blocked_count || 0)
        + Number(courierQueue?.waiting_count || 0)
        + Number(courierQueue?.blocked_count || 0);

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-shipping-p02">
            <section className={"rounded-2xl border p-5 " + (p02Ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50")}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2">
                            <Truck size={27} weight="duotone" className={p02Ready ? "text-emerald-800" : "text-amber-800"} />
                            <h2 className="text-xl font-black text-slate-950">الشحن والتحصيل — MZ2</h2>
                        </div>
                        <p className="mt-2 max-w-3xl text-xs font-semibold leading-6 text-slate-700">
                            تكلفة الشحن، عهدة COD، أجرة الموصل وتسوية البنك أحداث مستقلة. لا ينشئ الشحن بيعًا ثانيًا ولا يستخدم «تكلفة الشحن» من سلة كتكلفة ناقل.
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <span className={"rounded-full px-3 py-1 text-xs font-extrabold " + (p02Ready ? "bg-emerald-100 text-emerald-900" : "bg-amber-100 text-amber-900")}>
                            {p02Ready ? "P02 UAT مفتوح لهذه البيئة" : "P02 مقفل"}
                        </span>
                        <button type="button" onClick={refresh} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-xs font-extrabold">
                            <ArrowClockwise size={16} /> تحديث
                        </button>
                    </div>
                </div>
            </section>

            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-500">شركات شحن معتمدة</div>
                    <div className="mt-1 text-2xl font-black">{(context?.couriers || []).length.toLocaleString("en-US")}</div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-500">موصلون نشطون</div>
                    <div className="mt-1 text-2xl font-black">{(context?.drivers || []).length.toLocaleString("en-US")}</div>
                </div>
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                    <div className="text-xs font-bold text-amber-700">شحن/COD ينتظر</div>
                    <div className="mt-1 text-2xl font-black text-amber-900">{pendingCount.toLocaleString("en-US")}</div>
                </div>
            </div>

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h3 className="font-black text-slate-950">المعالجة التلقائية</h3>
                        <p className="mt-1 text-xs font-semibold text-slate-500">يعيد محاولة الشحنات التي اكتملت أدلتها. المفقود يبقى انتظارًا دون قيد.</p>
                    </div>
                    <button
                        type="button"
                        onClick={runPending}
                        disabled={!canPost || !p02Ready || busy === "pending"}
                        className="min-h-10 rounded-xl bg-emerald-800 px-4 text-xs font-black text-white disabled:opacity-40"
                    >
                        {busy === "pending" ? "جاري المعالجة…" : "معالجة الآمن الآن"}
                    </button>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                    {[...(driverQueue?.items || []), ...(courierQueue?.items || [])].slice(0, 12).map((item, index) => (
                        <div key={(item.assignment_id || item.evidence_id || item.order_number || "item") + index} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                            <div className="text-xs font-black text-slate-900">طلب {item.order_number || "—"}</div>
                            <div className="mt-1 text-[11px] font-semibold text-slate-500">
                                {item.state === "waiting" ? "ينتظر دليلًا" : item.state === "blocked" ? "مقفل/يحتاج إعدادًا" : item.state}
                                {(item.reasons || []).length ? " · " + item.reasons.join("، ") : ""}
                            </div>
                        </div>
                    ))}
                    {pendingCount === 0 && (
                        <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-xs font-extrabold text-emerald-800">
                            <CheckCircle size={18} weight="fill" /> لا توجد عناصر معلقة في المعالجة الحالية.
                        </div>
                    )}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <h3 className="font-black text-slate-950">تسوية من حركة البنك/الصندوق</h3>
                <p className="mt-1 text-xs font-semibold text-slate-500">اختر معنى الحركة فقط؛ المبلغ والبنك والتاريخ والمرجع تأتي من كشف البنك ولا يمكن إعادة كتابتها هنا.</p>
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    <label className="text-xs font-extrabold text-slate-700">
                        الحركة المالية
                        <select value={movementId} onChange={(event) => setMovementId(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                            <option value="">اختر حركة غير مصنفة</option>
                            {movements.map((row) => (
                                <option key={row.id} value={row.id}>
                                    {row.movement_date} · {row.direction === "in" ? "داخل" : "خارج"} · {formatMoney(row.amount)} · {row.reference || row.description || "بدون مرجع"}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        نوع الطرف
                        <select value={counterpartyType} onChange={(event) => { setCounterpartyType(event.target.value); setPreview(null); }} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                            <option value="store_driver">موصل المتجر</option>
                            <option value="courier">شركة شحن</option>
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        الطرف
                        <select value={counterpartyId} onChange={(event) => { setCounterpartyId(event.target.value); setPreview(null); }} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                            <option value="">اختر الطرف</option>
                            {counterparties.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
                        </select>
                    </label>
                    <label className="text-xs font-extrabold text-slate-700">
                        معنى التسوية
                        <select value={settlementType} onChange={(event) => { setSettlementType(event.target.value); setPreview(null); }} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 bg-white px-3">
                            <option value="cod_remittance">توريد COD</option>
                            <option value="fee_payment">دفع أجرة/فاتورة</option>
                            <option value="net_settlement">تسوية صافية مع مقاصة</option>
                        </select>
                    </label>
                    {settlementType === "net_settlement" && (
                        <label className="text-xs font-extrabold text-slate-700">
                            مبلغ المقاصة من المستحق للطرف
                            <input type="number" min="0.01" step="0.01" value={offsetAmount} onChange={(event) => { setOffsetAmount(event.target.value); setPreview(null); }} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3 text-left font-mono" dir="ltr" />
                        </label>
                    )}
                    <label className="text-xs font-extrabold text-slate-700">
                        سبب التصنيف
                        <input value={settlementReason} onChange={(event) => { setSettlementReason(event.target.value); setPreview(null); }} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3" placeholder="مثال: توريد COD حسب كشف البنك" />
                    </label>
                </div>
                <div className="mt-4 flex flex-wrap justify-end gap-2">
                    <button type="button" onClick={previewSettlement} disabled={!movementId || busy === "preview"} className="min-h-10 rounded-xl border border-slate-300 bg-white px-4 text-xs font-black text-slate-800 disabled:opacity-40">
                        {busy === "preview" ? "جاري التحقق…" : "معاينة"}
                    </button>
                    <button type="button" onClick={postSettlement} disabled={!canPost || !preview || !["eligible", "already_posted"].includes(preview.state) || busy === "post"} className="min-h-10 rounded-xl bg-emerald-800 px-4 text-xs font-black text-white disabled:opacity-40">
                        {busy === "post" ? "جاري الترحيل…" : "ترحيل من الدليل البنكي"}
                    </button>
                </div>
                {preview && (
                    <div className={"mt-4 rounded-xl border p-3 text-xs font-semibold " + (["eligible", "already_posted"].includes(preview.state) ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-200 bg-amber-50 text-amber-900")}>
                        الحالة: {preview.state}
                        {(preview.reasons || []).length ? " · " + preview.reasons.join("، ") : ""}
                    </div>
                )}
            </section>

            {canRules && (
                <section className="rounded-2xl border border-slate-200 bg-slate-50 p-5">
                    <h3 className="font-black text-slate-950">سعر شركة شحن — إعداد متقدم</h3>
                    <p className="mt-1 text-xs font-semibold leading-6 text-slate-500">
                        أدخل السعر المؤيد بالعقد/الفاتورة. «تكلفة الشحن» القادمة من سلة لا تُستخدم كتكلفة الناقل. حاليًا يُسجل الإجمالي مصروفًا بدون افتراض ضريبة مدخلات.
                    </p>
                    <form onSubmit={saveRate} className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                        <label className="text-[11px] font-extrabold text-slate-600">
                            معرف الشركة
                            <input value={courierId} onChange={(event) => setCourierId(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3" />
                        </label>
                        <label className="text-[11px] font-extrabold text-slate-600">
                            الاسم
                            <input value={courierName} onChange={(event) => setCourierName(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3" />
                        </label>
                        <label className="text-[11px] font-extrabold text-slate-600">
                            الأسماء في سلة، مفصولة بفاصلة
                            <input value={aliases} onChange={(event) => setAliases(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3" />
                        </label>
                        <label className="text-[11px] font-extrabold text-slate-600">
                            إجمالي التكلفة
                            <input type="number" min="0.01" step="0.01" value={fee} onChange={(event) => setFee(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-left font-mono" dir="ltr" />
                        </label>
                        <label className="text-[11px] font-extrabold text-slate-600">
                            ساري من
                            <input type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3" />
                        </label>
                        <label className="text-[11px] font-extrabold text-slate-600">
                            مرجع الدليل
                            <input value={evidenceRef} onChange={(event) => setEvidenceRef(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3" placeholder="عقد/فاتورة/قرار UAT" />
                        </label>
                        <label className="text-[11px] font-extrabold text-slate-600 md:col-span-2">
                            سبب الاعتماد
                            <input value={rateReason} onChange={(event) => setRateReason(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3" />
                        </label>
                        <div className="flex items-end">
                            <button disabled={busy === "rate" || !evidenceRef.trim() || !rateReason.trim()} className="min-h-10 rounded-xl bg-slate-900 px-4 text-xs font-black text-white disabled:opacity-40">
                                {busy === "rate" ? "جاري الحفظ…" : "اعتماد نسخة السعر"}
                            </button>
                        </div>
                    </form>
                    <div className="mt-4 space-y-2">
                        {(context?.couriers || []).map((row) => (
                            <div key={row.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white p-3 text-xs">
                                <span className="font-black text-slate-900">{row.name}</span>
                                <span className="font-mono font-bold">{formatMoney(row.total_fee)}</span>
                                <span className="text-slate-500">{row.evidence_ref}</span>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            {!p02Ready && (
                <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900">
                    <WarningCircle size={18} className="mt-0.5 shrink-0" />
                    P02 مقفل في هذه البيئة؛ القراءة والمعاينة متاحة لكن الكتابات المالية لن تمر. هذا هو الوضع المطلوب في Production.
                </div>
            )}
        </div>
    );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CheckCircle,
    Flask,
    HandPalm,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    createFulfillmentHold,
    getFulfillmentExperimentState,
    releaseFulfillmentHold,
    resetFulfillmentExperiment,
} from "../../services/fulfillmentExperiment";

const STOP_LABELS = {
    cancel: "إيقاف إلغاء",
    edit: "إيقاف تعديل",
    note: "إيقاف ملاحظة",
    employee: "إيقاف من موظف التجهيز",
};

const STAGE_LABELS = {
    reviewed: "تمت المراجعة — جاهز لإسناد جديد",
    in_progress: "قيد التجهيز",
    ready_to_ship: "جاهز للشحن",
    completed: "مكتمل",
};

const DELIVERY_FLOW_LABELS = {
    salla: "الشحن المسجل في سلة",
    store_courier: "مندوب توصيل المتجر",
};

export default function FulfillmentExperimentPanel({ orderNumber, items = [] }) {
    const [state, setState] = useState(null);
    const [unavailable, setUnavailable] = useState(false);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [resetConfirmationOpen, setResetConfirmationOpen] = useState(false);
    const [scope, setScope] = useState("order");
    const [targetId, setTargetId] = useState("");
    const [stopType, setStopType] = useState("note");
    const [note, setNote] = useState("");
    const [deliveryFlow, setDeliveryFlow] = useState("salla");

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!orderNumber) return;
        if (!quiet) setLoading(true);
        setError("");
        try {
            const result = await getFulfillmentExperimentState(orderNumber);
            setState(result || null);
            const savedFlow = result?.latest_run?.delivery_flow;
            if (savedFlow === "salla" || savedFlow === "store_courier") {
                setDeliveryFlow(savedFlow);
            }
            setUnavailable(false);
        } catch (loadError) {
            if (loadError?.status === 403) {
                setUnavailable(true);
                return;
            }
            setError(loadError?.message || "تعذّر تحميل التحكم.");
        } finally {
            if (!quiet) setLoading(false);
        }
    }, [orderNumber]);

    useEffect(() => { void load(); }, [load]);

    const pieces = useMemo(
        () => (Array.isArray(state?.pieces) ? state.pieces : []),
        [state?.pieces],
    );
    const activeHolds = useMemo(
        () => (state?.holds || []).filter((hold) => hold?.status === "active"),
        [state?.holds],
    );
    const itemOptions = useMemo(() => {
        const labels = new Map((items || []).map((item) => [
            String(item?.order_item_id || item?.id || ""),
            item?.name || item?.product_name || "منتج",
        ]));
        for (const piece of pieces) {
            const id = String(piece?.order_item_id || "");
            if (id && !labels.has(id)) labels.set(id, piece?.product_name || "منتج");
        }
        return [...labels.entries()].filter(([id]) => id);
    }, [items, pieces]);

    useEffect(() => {
        if (scope === "order") setTargetId("");
        if (scope === "item" && !itemOptions.some(([id]) => id === targetId)) {
            setTargetId(itemOptions[0]?.[0] || "");
        }
        if (scope === "piece" && !pieces.some((piece) => piece.piece_id === targetId)) {
            setTargetId(pieces[0]?.piece_id || "");
        }
    }, [scope, itemOptions, pieces, targetId]);

    if (unavailable) return null;
    if (loading && !state) {
        return <section className="rounded-2xl border border-violet-200 bg-violet-50 p-5 text-violet-800"><SpinnerGap className="ml-2 inline animate-spin" /> جارٍ تحميل تحكم التجهيز…</section>;
    }

    async function resetOrder() {
        setResetConfirmationOpen(false);
        setBusy("reset");
        setError("");
        setNotice("");
        try {
            const result = await resetFulfillmentExperiment(
                orderNumber,
                "إعادة الطلب لاختبار دورة التجهيز والصلاحيات",
                deliveryFlow,
            );
            const flowLabel = DELIVERY_FLOW_LABELS[deliveryFlow] || deliveryFlow;
            setNotice(`بدأت التجربة رقم ${result?.run?.generation || "—"} على مسار ${flowLabel}. أُرشفت ${result?.archived_piece_count || 0} قطعة تشغيلية دون أي قيد مالي.`);
            await load({ quiet: true });
        } catch (resetError) {
            setError(resetError?.message || "تعذّرت إعادة الطلب.");
        } finally {
            setBusy("");
        }
    }

    async function stopPreparation(event) {
        event.preventDefault();
        if (!note.trim() || (scope !== "order" && !targetId)) return;
        setBusy("hold");
        setError("");
        setNotice("");
        try {
            const result = await createFulfillmentHold(orderNumber, {
                scope,
                target_id: scope === "order" ? null : targetId,
                stop_type: stopType,
                note: note.trim(),
            });
            setNotice(`تم الإيقاف وإشعار ${result?.notified_employee_count || 0} موظف، ومنع اعتماد فاتورة المورد لهذه القطع.`);
            setNote("");
            await load({ quiet: true });
        } catch (holdError) {
            setError(holdError?.message || "تعذّر إنشاء الإيقاف.");
        } finally {
            setBusy("");
        }
    }

    async function releaseHold(hold) {
        setBusy(hold.id);
        setError("");
        setNotice("");
        try {
            const result = await releaseFulfillmentHold(hold.id, "استئناف التجهيز بعد معالجة سبب الإيقاف");
            setNotice(`تم إنهاء الإيقاف واستعادة حالة ${result?.restored_piece_count || 0} قطعة.`);
            await load({ quiet: true });
        } catch (releaseError) {
            setError(releaseError?.message || "تعذّر إنهاء الإيقاف.");
        } finally {
            setBusy("");
        }
    }

    return (
        <section className="rounded-2xl border-2 border-violet-200 bg-white p-5 shadow-sm" data-testid="fulfillment-experiment-panel">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex items-start gap-3">
                    <span className="rounded-xl bg-violet-100 p-3 text-violet-700"><Flask size={24} weight="fill" /></span>
                    <div><h2 className="text-lg font-black text-slate-950">تجربة مراحل التجهيز والإيقاف</h2><p className="mt-1 text-xs font-bold leading-6 text-slate-500">المسار التجريبي يحفظ التاريخ السابق، ويمنع إنشاء مديونية المورد أو تغيير أسعار المنتج والخدمة فعليًا.</p></div>
                </div>
                <button type="button" onClick={() => load()} disabled={loading || Boolean(busy)} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border px-3 text-xs font-black"><ArrowClockwise className={loading ? "animate-spin" : ""} /> تحديث</button>
            </div>

            {error && <div className="mt-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-900"><WarningCircle className="mt-0.5 shrink-0" />{error}</div>}
            {notice && <div className="mt-4 flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-black text-emerald-900"><CheckCircle className="mt-0.5 shrink-0" weight="fill" />{notice}</div>}

            <div className="mt-4 grid gap-3 sm:grid-cols-3">
                <div className="rounded-xl bg-slate-50 p-3"><div className="text-[11px] font-black text-slate-400">مرحلة الطلب</div><div className="mt-1 text-sm font-black text-slate-900">{STAGE_LABELS[state?.workflow_stage] || state?.workflow_stage || "—"}</div></div>
                <div className="rounded-xl bg-slate-50 p-3"><div className="text-[11px] font-black text-slate-400">القطع الحالية</div><div className="mt-1 text-xl font-black tabular-nums">{pieces.length}</div></div>
                <div className="rounded-xl bg-violet-50 p-3"><div className="text-[11px] font-black text-violet-500">جيل التجربة</div><div className="mt-1 text-xl font-black tabular-nums text-violet-900">{state?.latest_run?.generation || "—"}</div></div>
            </div>

            {state?.latest_run?.status === "active" && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-black leading-6 text-amber-900">وضع تجريبي نشط: قيود مالية 0 · مديونية مورد 0 · تحديث سلة 0 · تحديث قيود 0 · مسار الشحن: {DELIVERY_FLOW_LABELS[state?.latest_run?.delivery_flow] || "الشحن المسجل في سلة"}.</div>}

            {state?.capabilities?.can_reset_experiment && (
                <div className="mt-4 rounded-2xl border border-violet-200 bg-violet-50 p-4">
                    <label className="text-xs font-black text-violet-950" htmlFor="fulfillment-experiment-delivery-flow">مسار الشحن في التجربة</label>
                    <select
                        id="fulfillment-experiment-delivery-flow"
                        value={deliveryFlow}
                        onChange={(event) => setDeliveryFlow(event.target.value)}
                        disabled={Boolean(busy)}
                        className="mt-2 h-12 w-full rounded-xl border border-violet-200 bg-white px-3 text-sm font-black text-slate-900"
                        data-testid="fulfillment-experiment-delivery-flow"
                    >
                        <option value="salla">الشحن المسجل في سلة</option>
                        <option value="store_courier">مندوب توصيل المتجر — بوليصة أماسي</option>
                    </select>
                    <p className="mt-2 text-xs font-bold leading-6 text-violet-800">
                        اختيار مندوب المتجر يغيّر مسار التجربة داخل ميزان فقط، ويُبقي بوليصة شركة الشحن الحقيقية في سلة دون إلغاء أو تعديل.
                    </p>
                </div>
            )}

            {state?.capabilities?.can_reset_experiment && (
                <button type="button" onClick={() => setResetConfirmationOpen(true)} disabled={Boolean(busy)} className="mt-3 min-h-11 w-full rounded-xl bg-violet-700 px-4 text-sm font-black text-white disabled:opacity-50" data-testid="fulfillment-experiment-reset">
                    {busy === "reset" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <Flask className="ml-1 inline" />} إعادة الطلب إلى «تمت المراجعة» كتجربة جديدة
                </button>
            )}

            {resetConfirmationOpen && (
                <div className="fixed inset-0 z-[160] flex items-end justify-center bg-slate-950/60 p-3 sm:items-center" role="dialog" aria-modal="true" aria-labelledby="fulfillment-experiment-reset-title" data-testid="fulfillment-experiment-reset-dialog">
                    <div className="w-full max-w-lg rounded-2xl bg-white p-5 text-right shadow-2xl" dir="rtl">
                        <div className="flex items-start gap-3">
                            <span className="rounded-xl bg-violet-100 p-3 text-violet-700"><Flask size={24} weight="fill" /></span>
                            <div>
                                <h3 id="fulfillment-experiment-reset-title" className="text-lg font-black text-slate-950">تأكيد إعادة الطلب للتجربة</h3>
                                <p className="mt-2 text-sm font-bold leading-7 text-slate-600">سيُعاد الطلب #{orderNumber} إلى مرحلة تمت المراجعة على مسار {DELIVERY_FLOW_LABELS[deliveryFlow] || deliveryFlow}، وتؤرشف مراحل التجهيز التشغيلية فقط. الفاتورة والمحاسبة وشحنة سلة الحقيقية لن تتغير.</p>
                            </div>
                        </div>
                        <div className="mt-5 grid grid-cols-2 gap-3">
                            <button type="button" onClick={() => setResetConfirmationOpen(false)} className="min-h-11 rounded-xl border border-slate-300 bg-white px-4 text-sm font-black text-slate-700" data-testid="fulfillment-experiment-reset-cancel">إلغاء</button>
                            <button type="button" onClick={resetOrder} className="min-h-11 rounded-xl bg-violet-700 px-4 text-sm font-black text-white" data-testid="fulfillment-experiment-reset-confirm">تأكيد إعادة الطلب</button>
                        </div>
                    </div>
                </div>
            )}

            {state?.capabilities?.can_manage_stops && (
                <form onSubmit={stopPreparation} className="mt-5 rounded-2xl border border-amber-200 bg-amber-50/60 p-4">
                    <div className="flex items-center gap-2 font-black text-amber-950"><HandPalm size={22} weight="fill" /> إيقاف التجهيز</div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                        <label className="text-xs font-black text-slate-600">النطاق<select value={scope} onChange={(event) => setScope(event.target.value)} className="mt-1 h-11 w-full rounded-xl border bg-white px-3 text-sm"><option value="order">الطلب كاملًا</option><option value="item">منتج محدد</option><option value="piece">قطعة محددة</option></select></label>
                        {scope === "item" && <label className="text-xs font-black text-slate-600">المنتج<select value={targetId} onChange={(event) => setTargetId(event.target.value)} className="mt-1 h-11 w-full rounded-xl border bg-white px-3 text-sm">{itemOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
                        {scope === "piece" && <label className="text-xs font-black text-slate-600">القطعة<select value={targetId} onChange={(event) => setTargetId(event.target.value)} className="mt-1 h-11 w-full rounded-xl border bg-white px-3 text-sm">{pieces.map((piece) => <option key={piece.piece_id} value={piece.piece_id}>{piece.product_name || "منتج"} · {piece.unit_index} · {piece.status}</option>)}</select></label>}
                        <label className="text-xs font-black text-slate-600">نوع الإيقاف<select value={stopType} onChange={(event) => setStopType(event.target.value)} className="mt-1 h-11 w-full rounded-xl border bg-white px-3 text-sm"><option value="cancel">إيقاف إلغاء</option><option value="edit">إيقاف تعديل</option><option value="note">إيقاف ملاحظة</option></select></label>
                    </div>
                    <textarea value={note} onChange={(event) => setNote(event.target.value)} minLength={3} maxLength={1000} rows={3} placeholder="سبب الإيقاف الذي سيظهر لموظف التجهيز" className="mt-3 w-full rounded-xl border bg-white p-3 text-sm font-bold" />
                    <button type="submit" disabled={Boolean(busy) || note.trim().length < 3 || (scope !== "order" && !targetId)} className="mt-2 min-h-11 w-full rounded-xl bg-amber-700 px-4 text-sm font-black text-white disabled:opacity-40">{busy === "hold" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <HandPalm className="ml-1 inline" />} تسجيل الإيقاف وإشعار الموظف</button>
                </form>
            )}

            {activeHolds.length > 0 && <div className="mt-5 space-y-2"><h3 className="text-sm font-black text-slate-950">الإيقافات النشطة</h3>{activeHolds.map((hold) => <article key={hold.id} className="flex flex-col gap-3 rounded-xl border border-rose-200 bg-rose-50 p-3 sm:flex-row sm:items-center sm:justify-between"><div><div className="font-black text-rose-950">{hold.stop_label || STOP_LABELS[hold.stop_type] || hold.stop_type}</div><div className="mt-1 text-xs font-bold leading-5 text-rose-800">{hold.note} · {hold.piece_ids?.length || 0} قطعة</div></div>{state?.capabilities?.can_manage_stops && <button type="button" onClick={() => releaseHold(hold)} disabled={Boolean(busy)} className="min-h-10 rounded-xl bg-rose-700 px-4 text-xs font-black text-white disabled:opacity-40">{busy === hold.id ? <SpinnerGap className="ml-1 inline animate-spin" /> : null} إنهاء الإيقاف واستئناف التجهيز</button>}</article>)}</div>}
        </section>
    );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
    ArrowClockwise,
    CheckCircle,
    ClockCounterClockwise,
    MagnifyingGlass,
    NotePencil,
    Package,
    SpinnerGap,
    UserCircle,
    WarningCircle,
    XCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    approveTrackingInstruction,
    completeTrackingInstruction,
    createTrackingInstruction,
    getTrackedOrder,
    rejectTrackingInstruction,
    searchTrackedOrders,
} from "../services/orderTrackingNotes";

const STAGES = [
    ["current_stage", "المرحلة الحالية"],
    ["pending_review", "انتظار المراجعة"],
    ["reviewed", "تم المراجعة"],
    ["preparation", "تجهيز الموظف"],
    ["supplier_dispatch", "الإرسال إلى المورد"],
    ["supplier_receiving", "الاستلام من المورد"],
    ["preparation_receiving", "الاستلام من موظف التجهيز"],
    ["assembly_labeling", "التجميع والعنونة"],
    ["carrier_handoff", "تسليم الشحنة لشركة الشحن"],
    ["store_courier", "مندوب توصيل المتجر"],
];

const ACTIONS = [
    ["general", "ملاحظة عامة"],
    ["edit_product", "إيقاف لتعديل منتج"],
    ["edit_order", "إيقاف لتعديل الطلب كاملًا"],
    ["delete_product", "إيقاف لحذف منتج"],
    ["cancel_order", "إيقاف لإلغاء الطلب كاملًا"],
    ["urgent_preparation", "تجهيز مستعجل"],
    ["delivery_instruction", "تعليمات أو موعد توصيل"],
];
const BLOCKING_ACTIONS = new Set(["edit_product", "edit_order", "delete_product", "cancel_order"]);

const REQUIRED_ACTIONS = [
    ["none", "لا يوجد تنفيذ — عرض التنبيه فقط"],
    ["upload_photos", "رفع صور قبل إكمال المرحلة"],
    ["arrival_confirmation", "إبلاغ خدمة العملاء بوصول المنتج وانتظار موافقتها"],
    ["write_result", "كتابة نتيجة التنفيذ"],
    ["upload_photos_and_result", "رفع صور وكتابة النتيجة"],
];

const STATUS_LABELS = {
    active: "نشطة",
    waiting_customer_service_approval: "بانتظار موافقة خدمة العملاء",
    completed: "مكتملة",
};

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    return detail?.message || detail?.code || error?.message || "تعذر تنفيذ العملية";
}

function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "Asia/Riyadh",
    }).format(date);
}

function orderItems(data) {
    return Array.isArray(data?.order?.items) ? data.order.items : [];
}

function itemId(item) {
    return String(item?.order_item_id || item?.id || "");
}

function itemImage(item) {
    return item?.image_url || item?.image || item?.main_image || item?.thumbnail || "";
}

const EMPTY_FORM = {
    scope: "order",
    target_ids: [],
    action_type: "general",
    priority: "normal",
    note: "",
    target_stages: ["current_stage"],
    enforcement: "notice",
    required_action: "none",
    approval_required: false,
    delivery_date: "",
    delivery_time: "",
};

export default function OrderTrackingNotes() {
    const [searchParams] = useSearchParams();
    const linkedOrder = (searchParams.get("order") || "").trim();
    const [query, setQuery] = useState("");
    const [results, setResults] = useState([]);
    const [searching, setSearching] = useState(false);
    const [selectedNumber, setSelectedNumber] = useState("");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    const [approvalBusy, setApprovalBusy] = useState("");

    const load = useCallback(async (orderNumber) => {
        if (!orderNumber) return;
        setLoading(true);
        try {
            const response = await getTrackedOrder(orderNumber);
            setData(response);
            setSelectedNumber(orderNumber);
            setForm(EMPTY_FORM);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (!linkedOrder) return;
        setQuery(linkedOrder);
        load(linkedOrder);
    }, [linkedOrder, load]);

    async function search(event) {
        event?.preventDefault();
        const normalized = query.trim();
        if (normalized.length < 2) return toast.error("اكتب رقم الطلب أو اسم العميل أو الجوال");
        setSearching(true);
        try {
            const response = await searchTrackedOrders(normalized);
            setResults(response.items || []);
            if ((response.items || []).length === 1) await load(response.items[0].order_number);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setSearching(false);
        }
    }

    function toggleStage(stage) {
        setForm((current) => ({
            ...current,
            target_stages: current.target_stages.includes(stage)
                ? current.target_stages.filter((value) => value !== stage)
                : [...current.target_stages, stage],
        }));
    }

    function toggleTarget(targetId) {
        setForm((current) => ({
            ...current,
            target_ids: current.target_ids.includes(targetId)
                ? current.target_ids.filter((value) => value !== targetId)
                : [...current.target_ids, targetId],
        }));
    }

    async function saveInstruction(event) {
        event.preventDefault();
        if (!form.note.trim()) return toast.error("اكتب الملاحظة أو المهمة المطلوبة");
        if (!form.target_stages.length) return toast.error("اختر مرحلة ظهور واحدة على الأقل");
        if (form.scope === "item" && !form.target_ids.length) return toast.error("اختر منتجًا واحدًا أو أكثر");
        const requiredAction = BLOCKING_ACTIONS.has(form.action_type) && form.required_action === "none"
            ? "arrival_confirmation"
            : form.required_action;
        const payload = {
            ...form,
            target_id: form.target_ids[0] || null,
            note: form.note.trim(),
            required_action: requiredAction,
            enforcement: requiredAction === "none" ? form.enforcement : "completion_required",
            approval_required: requiredAction === "arrival_confirmation" || form.approval_required,
            delivery_date: form.delivery_date || null,
            delivery_time: form.delivery_time || null,
        };
        setSaving(true);
        try {
            await createTrackingInstruction(selectedNumber, payload);
            toast.success("حُفظت التعليمات وربطت بالمراحل المختارة.");
            await load(selectedNumber);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setSaving(false);
        }
    }

    async function decide(instruction, approved) {
        if (approvalBusy) return;
        let note = "";
        if (!approved) {
            note = window.prompt("اكتب سبب رفض التنفيذ وما المطلوب من الموظف:", "") || "";
            if (!note.trim()) return;
        }
        setApprovalBusy(instruction.id);
        try {
            if (approved) await approveTrackingInstruction(instruction.id, "تمت موافقة خدمة العملاء");
            else await rejectTrackingInstruction(instruction.id, note.trim());
            toast.success(approved ? "تمت الموافقة وفُتح المسار للموظف." : "أُعيدت المهمة للموظف مع سبب الرفض.");
            await load(selectedNumber);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setApprovalBusy("");
        }
    }

    async function releaseActiveStop(instruction) {
        if (approvalBusy) return;
        setApprovalBusy(instruction.id);
        try {
            await completeTrackingInstruction(
                instruction.id,
                instruction.operational_hold
                    ? "أنهت خدمة العملاء الإيقاف وسمحت باستكمال المسار"
                    : "أنهت خدمة العملاء الملاحظة",
            );
            toast.success(instruction.operational_hold
                ? "تم إنهاء الإيقاف وفتح المسار للموظف."
                : "تم إنهاء الملاحظة.");
            await load(selectedNumber);
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setApprovalBusy("");
        }
    }

    const activeInstructions = useMemo(
        () => (data?.instructions || []).filter((row) => ["active", "waiting_customer_service_approval"].includes(row.status)),
        [data],
    );

    return (
        <main className="mx-auto max-w-7xl space-y-5 p-3 sm:p-5" dir="rtl" data-testid="order-tracking-notes-page">
            <header className="rounded-3xl bg-gradient-to-l from-slate-950 via-violet-950 to-violet-800 p-5 text-white shadow-xl">
                <div className="flex items-start gap-3"><ClockCounterClockwise size={34} weight="duotone" /><div><h1 className="text-2xl font-black">تتبع الطلب وملاحظاته</h1><p className="mt-1 text-sm font-bold text-violet-100">المسار الكامل للطلب ولكل قطعة، وتعليمات خدمة العملاء الملزمة في المرحلة المحددة.</p></div></div>
                <form onSubmit={search} className="mt-5 flex flex-col gap-2 sm:flex-row">
                    <div className="relative flex-1"><MagnifyingGlass className="absolute right-4 top-3.5 text-slate-400" size={22} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="رقم الطلب، اسم العميل، أو رقم الجوال" className="h-12 w-full rounded-2xl border-0 bg-white pr-12 pl-4 font-bold text-slate-950 outline-none ring-violet-300 focus:ring-4" /></div>
                    <button disabled={searching} className="h-12 rounded-2xl bg-emerald-500 px-6 font-black text-slate-950 disabled:opacity-50">{searching ? <SpinnerGap className="ml-1 inline animate-spin" /> : null} بحث</button>
                </form>
            </header>

            {results.length > 0 && <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{results.map((row) => <button key={row.order_number} onClick={() => load(row.order_number)} className={`rounded-2xl border p-4 text-right shadow-sm ${selectedNumber === row.order_number ? "border-violet-500 bg-violet-50" : "border-slate-200 bg-white"}`}><div className="font-black">طلب #{row.order_number}</div><div className="mt-1 text-sm font-bold text-slate-600">{row.customer_name || "—"} · {row.shipping_city || "—"}</div><div className="mt-2 text-xs font-black text-violet-700">{row.order_status || "غير محدد"}</div></button>)}</section>}

            {loading && <div className="flex min-h-64 items-center justify-center"><SpinnerGap size={38} className="animate-spin text-violet-700" /></div>}

            {!loading && data && <>
                <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
                    <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-black text-slate-400">الطلب</div><h2 className="mt-1 text-2xl font-black">#{selectedNumber}</h2><div className="mt-1 font-bold text-slate-600">{data.order?.customer?.name || data.order?.customer_name || "—"}</div></div><div className="rounded-2xl bg-violet-100 px-4 py-3 text-center"><div className="text-xs font-black text-violet-500">المرحلة الحالية</div><div className="mt-1 font-black text-violet-950">{data.current_stage_label || data.current_stage}</div></div></div>
                        <div className="mt-5 grid gap-2 sm:grid-cols-3"><div className="rounded-xl bg-slate-50 p-3 text-xs"><div className="font-bold text-slate-400">الحالة في سلة</div><div className="mt-1 font-black">{data.order?.status_native || data.order?.status || "—"}</div></div><div className="rounded-xl bg-slate-50 p-3 text-xs"><div className="font-bold text-slate-400">عدد القطع</div><div className="mt-1 font-black">{data.pieces?.length || 0}</div></div><div className="rounded-xl bg-slate-50 p-3 text-xs"><div className="font-bold text-slate-400">التعليمات النشطة</div><div className="mt-1 font-black">{activeInstructions.length}</div></div></div>
                    </div>
                    <div className="rounded-3xl border border-amber-200 bg-amber-50 p-5 shadow-sm"><div className="flex items-center gap-2 font-black text-amber-950"><WarningCircle size={24} weight="fill" /> العهدة الحالية</div><div className="mt-3 space-y-2">{(data.pieces || []).slice(0, 6).map((piece) => <div key={piece.piece_id} className="rounded-xl bg-white p-3 text-xs"><div className="font-black">{piece.product_name}</div><div className="mt-1 font-bold text-slate-500">{piece.custody?.name || "—"} · {piece.stage_label}</div></div>)}</div></div>
                </section>

                <section className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
                    <form onSubmit={saveInstruction} className="h-fit rounded-3xl border border-violet-200 bg-white p-5 shadow-sm xl:sticky xl:top-4">
                        <h2 className="flex items-center gap-2 text-lg font-black"><NotePencil size={24} /> إضافة ملاحظة أو مهمة</h2>
                        <label className="mt-4 block text-xs font-black text-slate-600">النطاق<select value={form.scope} onChange={(event) => setForm((current) => ({ ...current, scope: event.target.value, target_ids: [] }))} className="mt-1 h-11 w-full rounded-xl border px-3"><option value="order">الطلب كاملًا</option><option value="item">منتجات محددة</option></select></label>
                        {form.scope === "item" && <div className="mt-3 max-h-52 space-y-2 overflow-y-auto rounded-2xl border bg-slate-50 p-2">{orderItems(data).map((item) => { const id = itemId(item); return <label key={id} className="flex cursor-pointer items-center gap-2 rounded-xl bg-white p-2 text-xs font-black"><input type="checkbox" checked={form.target_ids.includes(id)} onChange={() => toggleTarget(id)} />{itemImage(item) ? <img src={itemImage(item)} alt="" className="h-10 w-10 rounded-lg object-cover" /> : <Package size={30} className="text-slate-300" />}<span>{item.name || "منتج"}</span></label>; })}</div>}
                        <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="text-xs font-black text-slate-600">نوع الملاحظة<select value={form.action_type} onChange={(event) => setForm((current) => ({ ...current, action_type: event.target.value }))} className="mt-1 h-11 w-full rounded-xl border px-3">{ACTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="text-xs font-black text-slate-600">الأولوية<select value={form.priority} onChange={(event) => setForm((current) => ({ ...current, priority: event.target.value }))} className="mt-1 h-11 w-full rounded-xl border px-3"><option value="normal">عادية</option><option value="high">مهمة</option><option value="urgent">مستعجلة</option></select></label></div>
                        {BLOCKING_ACTIONS.has(form.action_type) && <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900">هذا النوع يوقف المنتج أو الطلب إلزاميًا، ولا يُفتح المسار إلا بعد موافقة خدمة العملاء.</div>}
                        <label className="mt-3 block text-xs font-black text-slate-600">الملاحظة أو المطلوب<textarea rows={4} value={form.note} onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))} className="mt-1 w-full rounded-xl border p-3 text-sm" placeholder="اكتب المطلوب بوضوح للموظف…" /></label>
                        <div className="mt-3"><div className="text-xs font-black text-slate-600">مرحلة الظهور</div><div className="mt-2 flex max-h-44 flex-wrap gap-2 overflow-y-auto">{STAGES.map(([value, label]) => <button key={value} type="button" onClick={() => toggleStage(value)} className={`rounded-full border px-3 py-1.5 text-xs font-black ${form.target_stages.includes(value) ? "border-violet-700 bg-violet-700 text-white" : "border-slate-300 bg-white text-slate-600"}`}>{label}</button>)}</div></div>
                        <label className="mt-3 block text-xs font-black text-slate-600">المطلوب قبل السماح بالإكمال<select value={form.required_action} onChange={(event) => setForm((current) => ({ ...current, required_action: event.target.value, approval_required: event.target.value === "arrival_confirmation" ? true : current.approval_required }))} className="mt-1 h-11 w-full rounded-xl border px-3">{REQUIRED_ACTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
                        {form.required_action === "none" && !BLOCKING_ACTIONS.has(form.action_type) && <label className="mt-3 block text-xs font-black text-slate-600">درجة الإلزام<select value={form.enforcement} onChange={(event) => setForm((current) => ({ ...current, enforcement: event.target.value }))} className="mt-1 h-11 w-full rounded-xl border px-3"><option value="notice">تنبيه فقط</option><option value="acknowledgement_required">يجب تأكيد الاطلاع</option></select></label>}
                        {["upload_photos", "write_result", "upload_photos_and_result"].includes(form.required_action) && <label className="mt-3 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-black"><input type="checkbox" checked={form.approval_required} onChange={(event) => setForm((current) => ({ ...current, approval_required: event.target.checked }))} />بعد التنفيذ يبقى متوقفًا حتى موافقة خدمة العملاء</label>}
                        {form.action_type === "delivery_instruction" && <div className="mt-3 grid grid-cols-2 gap-2"><label className="text-xs font-black text-slate-600">تاريخ التوصيل<input type="date" value={form.delivery_date} onChange={(event) => setForm((current) => ({ ...current, delivery_date: event.target.value }))} className="mt-1 h-11 w-full rounded-xl border px-2" /></label><label className="text-xs font-black text-slate-600">الوقت<input type="time" value={form.delivery_time} onChange={(event) => setForm((current) => ({ ...current, delivery_time: event.target.value }))} className="mt-1 h-11 w-full rounded-xl border px-2" /></label></div>}
                        <button disabled={saving} className="mt-4 min-h-12 w-full rounded-2xl bg-violet-800 px-4 font-black text-white disabled:opacity-50">{saving ? <SpinnerGap className="ml-1 inline animate-spin" /> : null} حفظ وربط التعليمات بالمسار</button>
                    </form>

                    <div className="space-y-5">
                        <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center justify-between"><h2 className="text-lg font-black">تعليمات وملاحظات الطلب</h2><button onClick={() => load(selectedNumber)} className="rounded-xl border p-2 text-violet-700"><ArrowClockwise size={20} /></button></div><div className="mt-4 space-y-3">{!(data.instructions || []).length ? <div className="rounded-2xl border border-dashed p-8 text-center text-sm font-bold text-slate-400">لا توجد ملاحظات حتى الآن.</div> : (data.instructions || []).map((row) => <article key={row.id} className={`rounded-2xl border p-4 ${row.status === "waiting_customer_service_approval" ? "border-violet-300 bg-violet-50" : row.status === "active" ? "border-amber-200 bg-amber-50" : "border-emerald-200 bg-emerald-50"}`}><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="font-black">{row.action_label || row.action_type}</div><div className="mt-1 text-xs font-black text-slate-500">{STATUS_LABELS[row.status] || row.status} · {row.target_stage_labels?.join("، ") || row.target_stages?.join("، ")}</div></div><span className="rounded-full bg-white px-3 py-1 text-xs font-black">{row.scope === "order" ? "الطلب كاملًا" : `${row.target_ids?.length || 1} منتج`}</span></div><p className="mt-3 whitespace-pre-wrap text-sm font-bold leading-7">{row.note}</p>{row.submitted_result && <div className="mt-3 rounded-xl bg-white p-3 text-xs font-bold"><span className="font-black">نتيجة الموظف:</span> {row.submitted_result}</div>}{row.evidence?.length > 0 && <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">{row.evidence.map((evidence) => <a key={evidence.id} href={evidence.url} target="_blank" rel="noreferrer"><img src={evidence.url} alt="إثبات التنفيذ" className="aspect-square w-full rounded-xl border object-cover" /></a>)}</div>}{row.status === "waiting_customer_service_approval" && <div className="mt-3 flex gap-2"><button disabled={Boolean(approvalBusy)} onClick={() => decide(row, true)} className="min-h-11 flex-1 rounded-xl bg-emerald-700 px-3 text-xs font-black text-white">{approvalBusy === row.id ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" />} تمت الموافقة — فتح المسار</button><button disabled={Boolean(approvalBusy)} onClick={() => decide(row, false)} className="min-h-11 rounded-xl bg-rose-700 px-3 text-xs font-black text-white"><XCircle className="ml-1 inline" /> رفض وإعادة للموظف</button></div>}{row.status === "active" && <button disabled={Boolean(approvalBusy)} onClick={() => releaseActiveStop(row)} className="mt-3 min-h-11 w-full rounded-xl bg-emerald-700 px-3 text-xs font-black text-white"><CheckCircle className="ml-1 inline" /> {row.operational_hold ? "إنهاء الإيقاف وفتح المسار" : "إنهاء الملاحظة"}</button>}<div className="mt-3 text-[11px] font-bold text-slate-400">أضافها {row.created_by_name || "خدمة العملاء"} · {formatDate(row.created_at)}</div></article>)}</div></section>

                        <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-black">مسار كل منتج وقطعة</h2><div className="mt-4 grid gap-3 sm:grid-cols-2">{(data.pieces || []).map((piece) => <article key={piece.piece_id} className="rounded-2xl border bg-slate-50 p-3"><div className="flex gap-3">{piece.image_url ? <img src={piece.image_url} alt="" className="h-16 w-16 rounded-xl object-cover" /> : <div className="flex h-16 w-16 items-center justify-center rounded-xl bg-white"><Package size={30} className="text-slate-300" /></div>}<div className="min-w-0"><div className="truncate font-black">{piece.product_name}</div><div className="mt-1 text-xs font-black text-violet-700">{piece.stage_label}</div><div className="mt-1 flex items-center gap-1 text-xs font-bold text-slate-500"><UserCircle /> {piece.custody?.name || "—"}</div></div></div>{piece.active_hold_id && <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-2 text-xs font-black text-rose-900">متوقف: {piece.hold_note || "راجع خدمة العملاء"}</div>}<details className="mt-3"><summary className="cursor-pointer text-xs font-black text-violet-700">عرض مسار القطعة ({piece.timeline?.length || 0})</summary><div className="mt-2 space-y-2">{(piece.timeline || []).map((event) => <div key={event.id} className="border-r-2 border-violet-200 pr-3 text-xs"><div className="font-black">{event.label}</div><div className="mt-0.5 font-bold text-slate-400">{formatDate(event.occurred_at)} {event.actor_name ? `· ${event.actor_name}` : ""}</div></div>)}</div></details></article>)}</div></section>

                        <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-black">المسار الكامل للطلب</h2><div className="mt-4 space-y-3">{(data.timeline || []).map((event) => <div key={event.id} className="relative border-r-2 border-violet-200 pr-5 before:absolute before:-right-[7px] before:top-1 before:h-3 before:w-3 before:rounded-full before:bg-violet-600"><div className="font-black">{event.label}</div><div className="mt-1 text-xs font-bold text-slate-400">{formatDate(event.occurred_at)} {event.actor_name ? `· ${event.actor_name}` : ""}</div>{event.note && <div className="mt-1 text-xs font-bold text-slate-600">{event.note}</div>}</div>)}</div></section>
                    </div>
                </section>
            </>}
        </main>
    );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    ArrowCounterClockwise,
    CaretLeft,
    Check,
    CheckCircle,
    Clock,
    CreditCard,
    Funnel,
    Gear,
    Gift,
    MagnifyingGlass,
    NotePencil,
    Package,
    SpinnerGap,
    Truck,
    User,
    UserCheck,
    WarningCircle,
    X,
    XCircle,
} from "@phosphor-icons/react";

import { useOrders } from "../hooks/useOrders";
import { getOrderFilterSummary } from "../services/orderEngine";

const STATUS_PRIORITY = [
    ["بإنتظار المراجعة", "بانتظار المراجعة", "انتظار المراجعة", "under review"],
    ["تم المراجعة", "تمت المراجعة", "reviewed"],
    ["قيد التنفيذ", "جاري التنفيذ", "processing", "in progress"],
    ["تم التنفيذ", "completed"],
    ["جاري التوصيل", "delivering", "out for delivery"],
    ["تم التوصيل", "delivered"],
    ["بإنتظار الدفع", "بانتظار الدفع", "payment pending"],
    ["بإنتظار تأكيد العميل", "بانتظار تأكيد العميل"],
    ["بإنتظار مراجعة العميل", "بانتظار مراجعة العميل"],
    ["مراجعة الملاحظات"],
    ["مدمج", "merged"],
    ["تم التجهيز", "prepared"],
    ["مسند إلى مندوب التوصيل", "مسند الى مندوب التوصيل", "assigned"],
    ["تم الشحن", "shipped"],
    ["ملغي", "ملغى", "canceled", "cancelled"],
    ["محذوف", "deleted"],
    ["مسترجع", "refunded", "returned"],
    ["قيد الاسترجاع", "refunding"],
    ["طلب عرض سعر", "quote"],
];

function normalizedStatus(status) {
    return String(status || "").replaceAll("_", " ").trim().toLowerCase();
}

function statusPriority(status) {
    const value = normalizedStatus(status);
    const index = STATUS_PRIORITY.findIndex((aliases) =>
        aliases.some((alias) => value === normalizedStatus(alias))
    );
    return index === -1 ? STATUS_PRIORITY.length + 100 : index;
}

function statusVisual(status) {
    const value = normalizedStatus(status);
    if (value.includes("بإنتظار المراجعة") || value.includes("بانتظار المراجعة") || value === "under review") {
        return { Icon: Clock, dot: "bg-slate-800", iconBox: "bg-slate-100 text-slate-700", active: "border-slate-700 bg-slate-50 ring-2 ring-slate-200" };
    }
    if (value.includes("تم المراجعة") || value.includes("تمت المراجعة") || value === "reviewed") {
        return { Icon: UserCheck, dot: "bg-slate-800", iconBox: "bg-slate-100 text-slate-700", active: "border-slate-700 bg-slate-50 ring-2 ring-slate-200" };
    }
    if (value.includes("قيد التنفيذ") || value.includes("جاري التنفيذ") || value.includes("processing") || value.includes("مدمج")) {
        return { Icon: Gear, dot: "bg-sky-500", iconBox: "bg-sky-50 text-sky-600", active: "border-sky-500 bg-sky-50 ring-2 ring-sky-100" };
    }
    if (value === "تم التنفيذ" || value === "completed") {
        return { Icon: CheckCircle, dot: "bg-emerald-400", iconBox: "bg-emerald-50 text-emerald-600", active: "border-emerald-500 bg-emerald-50 ring-2 ring-emerald-100" };
    }
    if (value.includes("جاري التوصيل") || value.includes("delivering") || value.includes("out for delivery")) {
        return { Icon: Truck, dot: "bg-amber-400", iconBox: "bg-amber-50 text-amber-600", active: "border-amber-500 bg-amber-50 ring-2 ring-amber-100" };
    }
    if (value === "تم التوصيل" || value === "delivered") {
        return { Icon: Package, dot: "bg-emerald-400", iconBox: "bg-emerald-50 text-emerald-600", active: "border-emerald-500 bg-emerald-50 ring-2 ring-emerald-100" };
    }
    if (value.includes("الدفع") || value.includes("payment")) {
        return { Icon: CreditCard, dot: "bg-rose-500", iconBox: "bg-rose-50 text-rose-600", active: "border-rose-500 bg-rose-50 ring-2 ring-rose-100" };
    }
    if (value.includes("التجهيز") || value.includes("الشحن") || value.includes("مندوب")) {
        return { Icon: Package, dot: "bg-teal-400", iconBox: "bg-teal-50 text-teal-600", active: "border-teal-500 bg-teal-50 ring-2 ring-teal-100" };
    }
    if (value.includes("مراجعة") || value.includes("تأكيد العميل") || value.includes("الملاحظات")) {
        return { Icon: NotePencil, dot: "bg-slate-800", iconBox: "bg-slate-100 text-slate-700", active: "border-slate-700 bg-slate-50 ring-2 ring-slate-200" };
    }
    if (value.includes("ملغ") || value.includes("محذوف") || value.includes("cancel") || value.includes("deleted")) {
        return { Icon: XCircle, dot: "bg-rose-500", iconBox: "bg-rose-50 text-rose-600", active: "border-rose-500 bg-rose-50 ring-2 ring-rose-100" };
    }
    if (value.includes("مسترج") || value.includes("استرجاع") || value.includes("refund") || value.includes("return")) {
        return { Icon: ArrowCounterClockwise, dot: "bg-rose-500", iconBox: "bg-rose-50 text-rose-600", active: "border-rose-500 bg-rose-50 ring-2 ring-rose-100" };
    }
    return { Icon: Package, dot: "bg-violet-500", iconBox: "bg-violet-50 text-violet-600", active: "border-violet-500 bg-violet-50 ring-2 ring-violet-100" };
}

function formatMoney(value) {
    return `${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function formatOrderDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        timeZone: "Asia/Riyadh", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true,
    }).format(date);
}

function cityName(order) {
    return order.shipping?.address?.city || order.customer?.shipping_address?.city || "غير محدد";
}

function CustomerAvatar({ customer }) {
    const avatarUrl = String(customer?.avatar_url || "").trim();
    const gender = String(customer?.gender || "").toLowerCase();
    const fallback = gender === "female" ? "👩" : gender === "male" ? "👨" : null;
    return (
        <div className="relative flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-full bg-slate-100 text-slate-600">
            {fallback ? <span className="text-2xl leading-none">{fallback}</span> : <User size={24} weight="fill" />}
            {avatarUrl && <img src={avatarUrl} alt="" className="absolute inset-0 h-full w-full object-cover" loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
        </div>
    );
}

function SelectionBox({ checked, disabled = false, onChange, label }) {
    return (
        <button
            type="button"
            disabled={disabled}
            aria-label={label}
            aria-pressed={checked}
            onClick={(event) => { event.stopPropagation(); onChange?.(); }}
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border-2 transition ${
                checked ? "border-teal-700 bg-teal-50 text-teal-700" : "border-teal-700 bg-white text-transparent"
            } ${disabled ? "cursor-not-allowed opacity-30" : "hover:bg-teal-50"}`}
        >
            <Check size={20} weight="bold" />
        </button>
    );
}

function CountCard({ label, count, active, onClick, isAll = false }) {
    const visual = isAll ? { Icon: Package, iconBox: "bg-violet-50 text-violet-600", active: "border-violet-500 bg-violet-50 ring-2 ring-violet-100" } : statusVisual(label);
    const Icon = visual.Icon;
    return (
        <button type="button" onClick={onClick} className={`min-w-[170px] rounded-2xl border bg-white px-4 py-4 text-right transition hover:-translate-y-0.5 hover:shadow-sm ${active ? visual.active : "border-slate-200"}`}>
            <div className="flex items-start justify-between gap-3">
                <div className="text-xs font-bold text-slate-500">{label}</div>
                <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${visual.iconBox}`}><Icon size={18} weight="duotone" /></span>
            </div>
            <div className="num mt-2 text-2xl font-extrabold text-slate-950">{Number(count || 0).toLocaleString("en-US")}</div>
        </button>
    );
}

export default function OrdersV2() {
    const navigate = useNavigate();
    const loadMoreRef = useRef(null);
    const [activeStatus, setActiveStatus] = useState(null);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [draftStatus, setDraftStatus] = useState(null);
    const [summary, setSummary] = useState({ total: 0, statusCards: [], statusCounts: {} });
    const [summaryError, setSummaryError] = useState("");
    const [searchDraft, setSearchDraft] = useState("");
    const [selected, setSelected] = useState(() => new Set());
    const [allMatchingSelected, setAllMatchingSelected] = useState(false);
    const [quickEditOpen, setQuickEditOpen] = useState(false);
    const [targetStatus, setTargetStatus] = useState("");
    const [previewOpen, setPreviewOpen] = useState(false);

    const { orders, hasMore, loading, initialLoading, error, searchMode, loadMore, reload, searchExactOrder } = useOrders({ statusExact: activeStatus });

    useEffect(() => {
        let mounted = true;
        async function loadSummary() {
            try {
                const result = await getOrderFilterSummary();
                if (mounted) { setSummary(result); setSummaryError(""); }
            } catch (loadError) {
                if (mounted) setSummaryError(loadError.message);
            }
        }
        loadSummary();
        const intervalId = window.setInterval(loadSummary, 30_000);
        return () => { mounted = false; window.clearInterval(intervalId); };
    }, []);

    useEffect(() => {
        const node = loadMoreRef.current;
        if (!node || !hasMore || initialLoading || searchMode) return undefined;
        const observer = new IntersectionObserver((entries) => { if (entries[0]?.isIntersecting) loadMore(); }, { rootMargin: "300px" });
        observer.observe(node);
        return () => observer.disconnect();
    }, [hasMore, initialLoading, loadMore, searchMode]);

    useEffect(() => {
        setSelected(new Set());
        setAllMatchingSelected(false);
        setTargetStatus("");
    }, [activeStatus, searchMode]);

    const statusCards = useMemo(() => {
        const providerCards = [...(summary.statusCards || [])].sort((left, right) => {
            const difference = statusPriority(left.label) - statusPriority(right.label);
            return difference || String(left.label || "").localeCompare(String(right.label || ""), "ar");
        });
        return [{ key: null, label: "كل الطلبات", count: summary.total, isAll: true }, ...providerCards];
    }, [summary.statusCards, summary.total]);

    const activeCard = useMemo(() => statusCards.find((card) => card.key === activeStatus) || null, [activeStatus, statusCards]);
    const activeStatusLabel = activeCard?.label || null;
    const visibleIds = useMemo(() => orders.map((order) => String(order.order_number)), [orders]);
    const allVisibleSelected = Boolean(activeStatus && visibleIds.length && visibleIds.every((id) => selected.has(id)));
    const selectedCount = allMatchingSelected ? Number(activeCard?.count || 0) : selected.size;
    const canSelectVisible = Boolean(activeStatus && !searchMode && orders.length);

    function toggleOrder(orderNumber) {
        const id = String(orderNumber);
        setAllMatchingSelected(false);
        setSelected((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    }

    function toggleVisible() {
        if (!canSelectVisible) return;
        setAllMatchingSelected(false);
        setSelected((current) => {
            const next = new Set(current);
            if (allVisibleSelected) visibleIds.forEach((id) => next.delete(id));
            else visibleIds.forEach((id) => next.add(id));
            return next;
        });
    }

    function clearSelection() {
        setSelected(new Set());
        setAllMatchingSelected(false);
        setQuickEditOpen(false);
        setPreviewOpen(false);
        setTargetStatus("");
    }

    function submitSearch(event) { event.preventDefault(); searchExactOrder(searchDraft); }
    async function clearSearch() { setSearchDraft(""); await reload(); }

    return (
        <div className="space-y-5" dir="rtl" data-testid="orders-v2-page">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-2"><div className="rounded-xl bg-violet-100 p-2 text-violet-700"><Package size={24} weight="fill" /></div><div><h1 className="text-2xl font-extrabold text-slate-950">الطلبات</h1><p className="mt-1 text-sm text-slate-500">مركز الطلبات الموحد من Order Engine</p></div></div>
                    <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3"><div className="text-xs font-bold text-violet-700">الطلبات المعروضة</div><div className="num mt-1 text-2xl font-extrabold text-violet-950">{orders.length.toLocaleString("en-US")}</div></div>
                </div>
                <div className="mt-5 flex gap-3 overflow-x-auto pb-3">{statusCards.map((card) => <CountCard key={card.key || "all"} label={card.label} count={card.count} isAll={card.isAll} active={activeStatus === card.key} onClick={() => setActiveStatus(card.key)} />)}</div>
                {summaryError && <div className="mt-2 text-xs text-rose-600">{summaryError}</div>}
                <form onSubmit={submitSearch} className="mt-5 flex gap-2"><div className="relative flex-1"><MagnifyingGlass size={20} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="ابحث برقم الطلب الدقيق…" className="w-full rounded-xl border border-slate-200 bg-slate-50 py-3 pr-11 pl-4 outline-none focus:border-violet-400" /></div><button type="button" onClick={() => { setDraftStatus(activeStatus); setDrawerOpen(true); }} className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-bold"><Funnel size={18} /> تصفية</button><button type="submit" disabled={loading} className="rounded-xl bg-violet-700 px-5 py-3 text-sm font-bold text-white disabled:opacity-60">بحث</button>{searchMode && <button type="button" onClick={clearSearch} className="rounded-xl border px-4 py-3 text-sm font-bold"><X size={17} /> مسح</button>}</form>
            </section>

            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
                    <div className="flex items-center gap-3">
                        <SelectionBox checked={allVisibleSelected} disabled={!canSelectVisible} onChange={toggleVisible} label={canSelectVisible ? "تحديد الطلبات الظاهرة" : "تحديد الكل متاح داخل حالة موحدة فقط"} />
                        <h2 className="font-extrabold text-slate-900">{searchMode ? "نتيجة البحث" : activeStatusLabel ? `طلبات: ${activeStatusLabel}` : "أحدث الطلبات حسب تاريخ الإنشاء"}</h2>
                    </div>
                    {selectedCount > 0 && <div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-teal-100 px-3 py-2 text-sm font-bold text-teal-900">تم تحديد {selectedCount.toLocaleString("en-US")} طلب</span><button type="button" onClick={() => setQuickEditOpen(true)} className="rounded-xl border border-teal-500 bg-white px-4 py-2 text-sm font-bold text-teal-800">تحرير سريع</button><button type="button" onClick={clearSelection} className="rounded-xl border px-3 py-2 text-sm">إلغاء التحديد</button></div>}
                </div>

                {canSelectVisible && allVisibleSelected && !allMatchingSelected && Number(activeCard?.count || 0) > visibleIds.length && (
                    <div className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-center text-sm text-amber-800">تم تحديد الطلبات المحملة ({visibleIds.length}). <button type="button" onClick={() => setAllMatchingSelected(true)} className="font-extrabold underline">تحديد جميع طلبات {activeStatusLabel} ({Number(activeCard?.count || 0).toLocaleString("en-US")})</button></div>
                )}
                {allMatchingSelected && <div className="border-b border-teal-200 bg-teal-50 px-5 py-3 text-center text-sm font-bold text-teal-900">تم تحديد جميع طلبات {activeStatusLabel} ({selectedCount.toLocaleString("en-US")}) — اختيار واجهة فقط، دون تنفيذ على سلة.</div>}
                {error && <div className="m-5 flex gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><WarningCircle size={22} /><div><b>تعذّر تحميل الطلبات</b><div>{error}</div></div></div>}
                {initialLoading ? <div className="flex min-h-72 items-center justify-center"><SpinnerGap size={32} className="animate-spin text-violet-600" /></div> : orders.length === 0 ? <div className="flex min-h-72 items-center justify-center text-slate-500">لا توجد طلبات مطابقة</div> : (
                    <div className="divide-y divide-slate-100">{orders.map((order) => {
                        const id = String(order.order_number);
                        const status = order.status_native || order.status || "غير محدد";
                        const checked = allMatchingSelected || selected.has(id);
                        return <div key={id} className={`flex items-center gap-3 px-4 py-4 sm:px-5 sm:py-5 ${checked ? "bg-teal-50/60" : "hover:bg-slate-50"}`}>
                            <SelectionBox checked={checked} onChange={() => toggleOrder(id)} label={`تحديد الطلب ${id}`} />
                            <button type="button" onClick={() => navigate(`/orders-v2/${encodeURIComponent(id)}`)} className="flex min-w-0 flex-1 items-center gap-3 text-right">
                                <CustomerAvatar customer={order.customer} />
                                <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><div className="truncate text-[15px] font-semibold">{order.customer?.name || "عميل بدون اسم"}</div>{order.is_gift && <span className="rounded-full bg-emerald-500 px-2 py-1 text-[10px] font-bold text-white"><Gift size={14} /> إهداء</span>}</div><div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-slate-400"><span>#{id}</span><span>•</span><span>{cityName(order)}</span><span>•</span><span className="inline-flex items-center gap-1"><span className={`h-3 w-3 rounded-full ${statusVisual(status).dot}`} />{status}</span><span>•</span><span>{Number(order.items?.length || 0)} قطعة</span><span>•</span><span>{order.payment?.method_native || order.payment?.method || "غير محدد"}</span></div></div>
                                <div className="flex shrink-0 items-center gap-3"><div className="text-left">{order.is_new && <span className="ml-2 rounded-full border border-rose-300 px-2 py-0.5 text-xs font-bold text-rose-600">جديد</span>}<span className="font-semibold text-teal-800">{formatMoney(order.totals?.total)}</span><div className="mt-1 text-xs text-slate-400">{formatOrderDate(order.created_at)}</div></div><CaretLeft size={18} className="text-slate-300" /></div>
                            </button>
                        </div>;
                    })}</div>
                )}
                <div ref={loadMoreRef} className="flex min-h-20 items-center justify-center border-t">{loading && !initialLoading && <span>تحميل 15 طلبًا إضافيًا…</span>}</div>
            </section>

            {drawerOpen && <div className="fixed inset-0 z-50 flex bg-slate-950/30"><button className="flex-1" onClick={() => setDrawerOpen(false)} /><aside className="h-full w-full max-w-sm overflow-y-auto bg-white p-5"><div className="flex justify-between"><b>فرز الطلبات حسب</b><button onClick={() => setDrawerOpen(false)}><X /></button></div><div className="mt-6 space-y-2">{statusCards.map((card) => <label key={card.key || "all"} className="flex justify-between rounded-xl border p-3"><span>{card.label}</span><input type="radio" checked={draftStatus === card.key} onChange={() => setDraftStatus(card.key)} /></label>)}</div><div className="mt-6 grid grid-cols-2 gap-3"><button onClick={() => { setDraftStatus(null); setActiveStatus(null); setDrawerOpen(false); }} className="rounded-xl border p-3">إعادة تعيين</button><button onClick={() => { setActiveStatus(draftStatus); setDrawerOpen(false); }} className="rounded-xl bg-violet-700 p-3 text-white">عرض النتائج</button></div></aside></div>}

            {quickEditOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl"><div className="flex items-center justify-between border-b bg-teal-100 px-5 py-4"><h3 className="text-lg font-extrabold">تحرير سريع للطلبات المحددة</h3><button onClick={() => setQuickEditOpen(false)}><X /></button></div><div className="space-y-4 p-5"><div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-800"><WarningCircle size={22} className="inline ml-2" />عدد الطلبات المحددة: <b>{selectedCount.toLocaleString("en-US")}</b></div><label className="block"><span className="mb-2 block text-sm font-bold">تغيير الحالة إلى</span><select value={targetStatus} onChange={(event) => setTargetStatus(event.target.value)} className="w-full rounded-xl border p-3"><option value="">اختر الحالة الجديدة</option>{statusCards.filter((card) => card.key && card.key !== activeStatus).map((card) => <option key={card.key} value={card.key}>{card.label}</option>)}</select></label><div className="rounded-xl bg-slate-50 p-3 text-sm text-slate-500">هذه واجهة Preview فقط. لن يتم إرسال أي تعديل إلى سلة في هذه المرحلة.</div></div><div className="flex justify-between border-t bg-slate-50 p-5"><button onClick={() => setQuickEditOpen(false)} className="rounded-xl border px-5 py-3">إغلاق</button><button disabled={!targetStatus} onClick={() => { setQuickEditOpen(false); setPreviewOpen(true); }} className="rounded-xl bg-teal-500 px-5 py-3 font-bold text-white disabled:opacity-40">معاينة التأكيد</button></div></div></div>}

            {previewOpen && <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4"><div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl"><div className="border-b bg-teal-100 px-5 py-4 text-lg font-extrabold">معاينة تغيير حالة الطلبات</div><div className="space-y-4 p-5"><div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-900"><WarningCircle size={24} className="inline ml-2" />سيتم لاحقًا تغيير <b>{selectedCount.toLocaleString("en-US")}</b> طلب إلى <b>{statusCards.find((card) => card.key === targetStatus)?.label || targetStatus}</b>.</div><div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-violet-900"><b>التنفيذ غير مفعّل الآن.</b><div className="mt-1 text-sm">سيُربط لاحقًا بـ Order Mutation Engine مع Preview، سجل تدقيق، ونتيجة مستقلة لكل طلب.</div></div></div><div className="flex justify-end border-t bg-slate-50 p-5"><button onClick={() => setPreviewOpen(false)} className="rounded-xl bg-slate-800 px-5 py-3 font-bold text-white">تم</button></div></div></div>}
        </div>
    );
}

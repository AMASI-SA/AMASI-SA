import { useCallback, useEffect, useState } from "react";
import {
    Camera,
    CaretLeft,
    CheckCircle,
    ClipboardText,
    Clock,
    FileText,
    FolderOpen,
    Gear,
    MagnifyingGlass,
    SpinnerGap,
    Truck,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    listPendingOrderReviews,
    listPreparationFiles,
    listReviewedProductCatalog,
} from "../../services/orderReviewEngine";
import { getMyPreparationWork } from "../../services/preparationWorkService";
import { listReadyToShipOrders } from "../../services/fulfillmentV2";

const EMPTY_SUMMARY = {
    pending: null,
    pendingHasMore: false,
    reviewed: null,
    files: null,
    inProgress: null,
    readyToShip: null,
};

function visibleCount(value, hasMore = false) {
    if (value === null || value === undefined) return "—";
    const formatted = Number(value || 0).toLocaleString("en-US");
    return hasMore ? `${formatted}+` : formatted;
}

function SummaryMetric({ label, value, hasMore = false, Icon }) {
    return (
        <div className="min-w-0 px-1.5 py-3 text-center" data-testid={`mobile-fulfillment-metric-${label}`}>
            <div className="truncate text-[10px] font-black leading-4 text-slate-600">{label}</div>
            <div className="mt-1 text-2xl font-black tabular-nums text-emerald-800">{visibleCount(value, hasMore)}</div>
            <Icon size={22} className="mx-auto mt-2 text-slate-500" weight="duotone" />
        </div>
    );
}

function QuickAction({ label, count = null, Icon, onClick }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="flex min-h-16 w-full items-center gap-3 border-b border-slate-100 px-4 text-right last:border-b-0"
            data-testid={`mobile-fulfillment-action-${label}`}
        >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-800">
                <Icon size={23} weight="duotone" />
            </span>
            <span className="min-w-0 flex-1 text-sm font-black text-slate-950">{label}</span>
            {count !== null && (
                <span className="flex min-w-9 items-center justify-center rounded-full bg-emerald-50 px-2 py-1 text-xs font-black tabular-nums text-emerald-800">
                    {visibleCount(count)}
                </span>
            )}
            <CaretLeft size={20} className="shrink-0 text-slate-400" weight="bold" />
        </button>
    );
}

function fileStatus(file = {}) {
    const status = String(file.execution_status || file.status || "").trim();
    if (status === "in_progress") return { label: "قيد التنفيذ", className: "bg-amber-50 text-amber-800" };
    if (status === "received") return { label: "مستلم", className: "bg-emerald-50 text-emerald-800" };
    return { label: "جاهز للبدء", className: "bg-violet-50 text-violet-800" };
}

export default function FulfillmentMobileOverview({ onOpenStage, stages = [] }) {
    const [summary, setSummary] = useState(EMPTY_SUMMARY);
    const [files, setFiles] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");
    const [showStages, setShowStages] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        const results = await Promise.allSettled([
            listPendingOrderReviews({ limit: 50 }),
            listReviewedProductCatalog({ limit: 500 }),
            getMyPreparationWork({ limit: 50 }),
            listReadyToShipOrders({ limit: 100 }),
            listPreparationFiles({ limit: 50 }),
        ]);
        const [pending, reviewed, work, ready, preparationFiles] = results;
        setSummary({
            pending: pending.status === "fulfilled" ? pending.value.items.length : null,
            pendingHasMore: pending.status === "fulfilled" && Boolean(pending.value.nextCursor),
            reviewed: reviewed.status === "fulfilled"
                ? Number(reviewed.value.summary?.reviewed_order_count || 0)
                : null,
            files: preparationFiles.status === "fulfilled"
                ? Number(preparationFiles.value.items?.length || 0)
                : null,
            inProgress: work.status === "fulfilled"
                ? Number(work.value.summary?.in_progress || 0)
                : null,
            readyToShip: ready.status === "fulfilled"
                ? Number(ready.value.total ?? ready.value.items?.length ?? 0)
                : null,
        });
        setFiles(preparationFiles.status === "fulfilled"
            ? (preparationFiles.value.items || []).slice(0, 2)
            : []);
        if (results.every((result) => result.status === "rejected")) {
            setError("تعذّر تحميل ملخص التجهيز. افتح إحدى المهام أو حاول التحديث.");
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        const mobile = typeof window === "undefined"
            || typeof window.matchMedia !== "function"
            || window.matchMedia("(max-width: 1023px)").matches;
        if (mobile) load();
    }, [load]);

    function submitSearch(event) {
        event.preventDefault();
        const value = String(search || "").replace(/^#/, "").trim();
        if (!value) return;
        onOpenStage("pending_review", { search: value });
    }

    return (
        <section className="space-y-5 pb-6 lg:hidden" dir="rtl" data-testid="fulfillment-mobile-overview">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="text-xs font-black text-emerald-700">MEZAN OS V2</div>
                    <h1 className="mt-1 text-2xl font-black text-slate-950">إدارة التجهيز</h1>
                </div>
                {loading && <SpinnerGap size={24} className="animate-spin text-emerald-700" aria-label="جارٍ تحميل الملخص" />}
            </div>

            <form onSubmit={submitSearch} className="relative" role="search">
                <MagnifyingGlass size={22} className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    inputMode="numeric"
                    placeholder="ابحث برقم الطلب"
                    className="min-h-14 w-full rounded-2xl border border-slate-200 bg-white pr-12 pl-4 text-sm font-bold shadow-sm outline-none focus:border-emerald-500 focus:ring-4 focus:ring-emerald-50"
                />
            </form>

            {error && (
                <button type="button" onClick={load} className="flex w-full items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-3 text-right text-xs font-black text-amber-900">
                    <WarningCircle size={19} className="mt-0.5 shrink-0" weight="fill" />
                    <span className="flex-1">{error}</span>
                    <span className="underline">تحديث</span>
                </button>
            )}

            <section>
                <h2 className="mb-3 text-lg font-black text-slate-950">ملخص العمل اليوم</h2>
                <div className="grid grid-cols-4 divide-x divide-x-reverse divide-slate-200 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                    <SummaryMetric label="بانتظار المراجعة" value={summary.pending} hasMore={summary.pendingHasMore} Icon={Clock} />
                    <SummaryMetric label="تمت المراجعة" value={summary.reviewed} Icon={CheckCircle} />
                    <SummaryMetric label="قيد التنفيذ" value={summary.inProgress} Icon={Gear} />
                    <SummaryMetric label="جاهز للشحن" value={summary.readyToShip} Icon={Truck} />
                </div>
            </section>

            <section>
                <h2 className="mb-3 text-lg font-black text-slate-950">المهام السريعة</h2>
                <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                    <QuickAction label="مراجعة الطلبات" count={summary.pending} Icon={ClipboardText} onClick={() => onOpenStage("pending_review")} />
                    <QuickAction label="ملفات التجهيز" count={summary.files} Icon={FolderOpen} onClick={() => onOpenStage("reviewed", { view: "files" })} />
                    <QuickAction label="استلام المورد" Icon={Camera} onClick={() => onOpenStage("preparation")} />
                </div>
            </section>

            <section>
                <div className="mb-3 flex items-center justify-between gap-3">
                    <h2 className="text-lg font-black text-slate-950">آخر ملفات التجهيز</h2>
                    <button type="button" onClick={() => onOpenStage("reviewed", { view: "files" })} className="text-xs font-black text-emerald-800">عرض السجل</button>
                </div>
                <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                    {!files.length ? (
                        <div className="p-6 text-center text-sm font-bold text-slate-500">لا توجد ملفات تجهيز محفوظة حتى الآن.</div>
                    ) : files.map((file) => {
                        const status = fileStatus(file);
                        return (
                            <button key={file.file_number || file.batch_id} type="button" onClick={() => onOpenStage("in_progress")} className="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 border-b border-slate-100 p-4 text-right last:border-b-0">
                                <FileText size={24} className="text-emerald-700" weight="duotone" />
                                <span className="min-w-0">
                                    <span className="block truncate text-sm font-black text-slate-950">{file.file_title || file.file_number || "ملف تجهيز"}</span>
                                    <span className="mt-1 block text-[11px] font-bold text-slate-500">{Number(file.allocated_quantity || 0)} قطعة · {file.responsible_employee_name || "غير مسند"}</span>
                                </span>
                                <span className={`rounded-lg px-2 py-1 text-[10px] font-black ${status.className}`}>{status.label}</span>
                            </button>
                        );
                    })}
                    <button type="button" onClick={() => setShowStages((value) => !value)} className="flex min-h-12 w-full items-center justify-center gap-2 border-t border-slate-100 text-sm font-black text-emerald-800">
                        عرض جميع المراحل <CaretLeft size={18} className={showStages ? "-rotate-90" : ""} weight="bold" />
                    </button>
                </div>
                {showStages && (
                    <div className="mt-3 grid grid-cols-2 gap-2" data-testid="fulfillment-mobile-all-stages">
                        {stages.map((stage) => {
                            const Icon = stage.Icon;
                            return (
                                <button key={stage.key} type="button" onClick={() => onOpenStage(stage.key)} className="rounded-xl border border-slate-200 bg-white p-3 text-sm font-black text-slate-800">
                                    <Icon className="ml-1 inline text-emerald-700" /> {stage.shortLabel || stage.label}
                                </button>
                            );
                        })}
                    </div>
                )}
            </section>
        </section>
    );
}

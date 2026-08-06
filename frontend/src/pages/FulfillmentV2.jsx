// Mezan OS V2 governed preparation workspace.
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
    ArrowRight,
    CheckCircle,
    Clock,
    Cube,
    Gear,
    Package,
    Queue,
    Truck,
    UserCheck,
    WarningCircle,
} from "@phosphor-icons/react";

import OrderReview from "./OrderReview";
import ReviewedOrders from "./ReviewedOrders";
import PreparationFilesRegistry from "../components/fulfillment/PreparationFilesRegistry";
import PreparationWorkDashboard from "../components/fulfillment/PreparationWorkDashboard";
import ReadyToShipOrders from "../components/fulfillment/ReadyToShipOrders";
import SupplierReceivingWorkspace from "../components/fulfillment/SupplierReceivingWorkspace";
import FulfillmentMobileOverview from "../components/fulfillment/FulfillmentMobileOverview";

export const FULFILLMENT_STAGES = [
    {
        key: "pending_review",
        label: "بانتظار المراجعة",
        shortLabel: "بانتظار المراجعة",
        Icon: Clock,
        description: "مراجعة بيانات العميل والدفع والشحن وكل قطعة قبل اعتماد الطلب.",
    },
    {
        key: "reviewed",
        label: "تم المراجعة",
        shortLabel: "تم المراجعة",
        Icon: UserCheck,
        description: "اختيار الطلبات أو المنتجات المؤهلة وإنشاء دفعات الرفع وملفات التجهيز.",
    },
    {
        key: "in_progress",
        label: "قيد التنفيذ",
        shortLabel: "قيد التنفيذ",
        Icon: Gear,
        description: "متابعة الملفات والقطع المسندة، الموظف المسؤول، الخدمات والموعد المتوقع.",
    },
    {
        key: "preparation",
        label: "التجهيز",
        shortLabel: "التجهيز",
        Icon: Queue,
        description: "إدارة مسارات المستودع والمورد والتصنيع الداخلي وملفات التجهيز.",
    },
    {
        key: "assembly",
        label: "الاستلام والتجميع",
        shortLabel: "الاستلام والتجميع",
        Icon: Cube,
        description: "استلام القطع بالباركود وتجميع مكونات الطلب ومنع التكرار.",
    },
    {
        key: "ready_to_ship",
        label: "جاهز للشحن",
        shortLabel: "جاهز للشحن",
        Icon: Package,
        description: "لا يظهر الطلب هنا إلا بعد اكتمال جميع المنتجات النشطة في الطلب.",
    },
    {
        key: "completed",
        label: "تم التنفيذ",
        shortLabel: "تم التنفيذ",
        Icon: CheckCircle,
        description: "الطلبات المكتملة تشغيليًا قبل انتقالها إلى مسار شركة الشحن.",
    },
    {
        key: "delivering",
        label: "جاري التوصيل",
        shortLabel: "جاري التوصيل",
        Icon: Truck,
        description: "متابعة الشحنة ورقم التتبع وحالة التسليم الحالية.",
    },
    {
        key: "delivered",
        label: "تم التوصيل",
        shortLabel: "تم التوصيل",
        Icon: CheckCircle,
        description: "الطلبات التي اكتملت دورة تنفيذها وتسليمها للعميل.",
    },
];

function PlannedStage({ stage }) {
    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid={`fulfillment-stage-${stage.key}`}>
            <div className="border-b border-slate-100 bg-slate-50 px-5 py-4">
                <div className="flex items-center gap-3">
                    <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-100 text-violet-700">
                        <stage.Icon size={24} weight="duotone" />
                    </span>
                    <div>
                        <h2 className="text-xl font-extrabold text-slate-950">{stage.label}</h2>
                        <p className="mt-1 text-sm text-slate-500">{stage.description}</p>
                    </div>
                </div>
            </div>

            <div className="p-5">
                <div className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-amber-950">
                    <WarningCircle size={24} className="mt-0.5 shrink-0" weight="duotone" />
                    <div>
                        <div className="font-extrabold">هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد.</div>
                        <p className="mt-1 text-sm leading-6">
                            تُبنى بعد اكتمال واعتماد المرحلة السابقة حتى لا تنتقل الطلبات أو المنتجات إلى الأمام قبل استيفاء شروطها.
                        </p>
                    </div>
                </div>

            </div>
        </section>
    );
}

export default function FulfillmentV2() {
    const [searchParams, setSearchParams] = useSearchParams();
    const [isMobile, setIsMobile] = useState(() => (
        typeof window !== "undefined"
        && typeof window.matchMedia === "function"
        && window.matchMedia("(max-width: 1023px)").matches
    ));
    const searchKey = searchParams.toString();
    const hasRequestedStage = Boolean(String(searchParams.get("stage") || "").trim());
    const requestedStage = String(searchParams.get("stage") || "pending_review").trim();
    const activeStage = useMemo(
        () => FULFILLMENT_STAGES.find((stage) => stage.key === requestedStage) || FULFILLMENT_STAGES[0],
        [requestedStage],
    );
    const reviewedView = activeStage.key === "reviewed" && searchParams.get("view") === "files"
        ? "files"
        : "products";
    const currentWindowLabel = activeStage.key === "reviewed" && reviewedView === "files"
        ? "سجل ملفات التجهيز"
        : activeStage.label;
    const ActiveStageIcon = activeStage.Icon;

    useEffect(() => {
        if (activeStage.key !== "reviewed" || searchParams.get("view")) return;
        const next = new URLSearchParams(searchParams);
        next.set("view", "products");
        setSearchParams(next, { replace: true });
    }, [activeStage.key, searchKey, searchParams, setSearchParams]);

    useEffect(() => {
        if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
        const media = window.matchMedia("(max-width: 1023px)");
        const sync = () => setIsMobile(media.matches);
        sync();
        if (typeof media.addEventListener === "function") {
            media.addEventListener("change", sync);
            return () => media.removeEventListener("change", sync);
        }
        media.addListener?.(sync);
        return () => media.removeListener?.(sync);
    }, []);

    const selectStage = (stageKey, params = {}) => {
        const next = new URLSearchParams(searchParams);
        next.set("stage", stageKey);
        if (stageKey === "reviewed") next.set("view", params.view || "products");
        else next.delete("view");
        if (params.search) next.set("search", params.search);
        else next.delete("search");
        setSearchParams(next, { replace: true });
    };

    const showMobileOverview = () => {
        setSearchParams(new URLSearchParams(), { replace: true });
    };

    const stageContent = activeStage.key === "pending_review" ? (
        <OrderReview embedded initialSearch={searchParams.get("search") || ""} />
    ) : activeStage.key === "reviewed" ? (
        reviewedView === "files"
            ? <PreparationFilesRegistry />
            : <ReviewedOrders />
    ) : activeStage.key === "in_progress" ? (
        <PreparationWorkDashboard />
    ) : activeStage.key === "preparation" ? (
        <SupplierReceivingWorkspace />
    ) : activeStage.key === "ready_to_ship" ? (
        <ReadyToShipOrders />
    ) : (
        <PlannedStage stage={activeStage} />
    );

    return (
        <div className="space-y-5" dir="rtl" data-testid="fulfillment-v2-page">
            {!hasRequestedStage && <FulfillmentMobileOverview onOpenStage={selectStage} stages={FULFILLMENT_STAGES} />}

            {hasRequestedStage && (
                <header className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm lg:hidden" data-testid="fulfillment-mobile-stage-header">
                    <div className="flex items-center gap-3 bg-emerald-800 px-3 py-3 text-white">
                        <button type="button" onClick={showMobileOverview} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10" aria-label="العودة إلى لوحة إدارة التجهيز">
                            <ArrowRight size={21} weight="bold" />
                        </button>
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10">
                            <ActiveStageIcon size={22} weight="duotone" />
                        </span>
                        <div className="min-w-0 flex-1">
                            <div className="text-[10px] font-black text-emerald-100">إدارة التجهيز</div>
                            <h1 className="truncate text-lg font-black">{currentWindowLabel}</h1>
                        </div>
                    </div>
                    <details className="group border-t border-emerald-100">
                        <summary className="cursor-pointer list-none px-4 py-2.5 text-center text-xs font-black text-emerald-800">الانتقال إلى مرحلة أخرى</summary>
                        <nav className="grid grid-cols-2 gap-2 border-t border-slate-100 bg-slate-50 p-3" aria-label="مراحل إدارة التجهيز للجوال">
                            {FULFILLMENT_STAGES.map((stage) => (
                                <button key={stage.key} type="button" onClick={() => selectStage(stage.key)} className={`rounded-xl border px-3 py-2 text-xs font-black ${stage.key === activeStage.key ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-slate-200 bg-white text-slate-700"}`}>
                                    {stage.shortLabel}
                                </button>
                            ))}
                        </nav>
                    </details>
                </header>
            )}

            <header className="hidden overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm lg:block">
                <div className="bg-gradient-to-l from-violet-700 via-violet-600 to-indigo-700 px-5 py-6 text-white sm:px-7">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="text-sm font-bold text-violet-100">Mezan OS V2</div>
                            <h1 className="mt-1 text-2xl font-black sm:text-3xl">إدارة التجهيز</h1>
                            <p className="mt-2 max-w-3xl text-sm leading-6 text-violet-100">
                                إدارة دورة الطلب من المراجعة والرفع والتجهيز والاستلام، حتى الشحن والتوصيل، داخل صفحة تشغيل واحدة.
                            </p>
                        </div>
                        <div className="rounded-2xl border border-white/20 bg-white/10 px-4 py-3 backdrop-blur">
                            <div className="text-xs font-bold text-violet-100">النافذة الحالية</div>
                            <div className="mt-1 text-lg font-extrabold">{currentWindowLabel}</div>
                        </div>
                    </div>
                </div>

                <div className="border-t border-slate-100 bg-slate-50/80 px-3 py-3 sm:px-4">
                    <div className="mb-3 flex flex-wrap items-end justify-between gap-2 px-1">
                        <div>
                            <div className="text-sm font-extrabold text-slate-800">تبويبات إدارة التجهيز</div>
                            <p className="mt-0.5 text-xs text-slate-500">كل تبويب يمثل مرحلة مستقلة من دورة الطلب.</p>
                        </div>
                        <span className="rounded-full border border-violet-200 bg-white px-3 py-1 text-xs font-bold text-violet-700">
                            {FULFILLMENT_STAGES.length} مراحل
                        </span>
                    </div>
                    <nav className="flex gap-2 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-2 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden" aria-label="مراحل إدارة التجهيز">
                        {FULFILLMENT_STAGES.map((stage, index) => {
                            const active = stage.key === activeStage.key;
                            const Icon = stage.Icon;
                            return (
                                <button
                                    type="button"
                                    key={stage.key}
                                    data-testid={`fulfillment-stage-tab-${stage.key}`}
                                    onClick={() => selectStage(stage.key)}
                                    aria-current={active ? "page" : undefined}
                                    className={`group flex min-w-[170px] items-center gap-3 rounded-xl border px-4 py-3 text-right transition ${active ? "border-violet-500 bg-violet-50 text-violet-950 ring-2 ring-violet-100" : "border-transparent bg-white text-slate-600 hover:border-violet-200 hover:bg-slate-50"}`}
                                >
                                    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${active ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-500 group-hover:bg-violet-100 group-hover:text-violet-700"}`}>
                                        <Icon size={19} weight="duotone" />
                                    </span>
                                    <span className="min-w-0">
                                        <span className="block text-[10px] font-bold opacity-60">المرحلة {index + 1}</span>
                                        <span className="block whitespace-nowrap text-sm font-extrabold">{stage.shortLabel}</span>
                                    </span>
                                </button>
                            );
                        })}
                    </nav>
                </div>
            </header>

            {(hasRequestedStage || !isMobile) && (
                <div className={!hasRequestedStage ? "hidden lg:block" : ""}>
                    {stageContent}
                </div>
            )}
        </div>
    );
}

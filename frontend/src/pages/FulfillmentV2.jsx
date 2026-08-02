// Mezan OS V2 governed preparation workspace.
import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
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
import ReadyToShipOrders from "../components/fulfillment/ReadyToShipOrders";

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
        description: "متابعة القطع المسندة، الموظف المسؤول، المورد، والموعد المتوقع.",
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

const PREPARATION_TRACKS = [
    "من المستودع",
    "من المورد",
    "تصنيع داخلي",
    "ينتظر توريد",
    "قيد التجميع",
    "متوقف بسبب نقص منتج",
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

                {stage.key === "preparation" && (
                    <div className="mt-5">
                        <div className="mb-3 text-sm font-extrabold text-slate-700">مسارات التجهيز المعتمدة</div>
                        <div className="flex flex-wrap gap-2">
                            {PREPARATION_TRACKS.map((track) => (
                                <span key={track} className="rounded-full border border-violet-200 bg-violet-50 px-3 py-2 text-sm font-bold text-violet-800">
                                    {track}
                                </span>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
}

export default function FulfillmentV2() {
    const [searchParams, setSearchParams] = useSearchParams();
    const searchKey = searchParams.toString();
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

    useEffect(() => {
        if (activeStage.key !== "reviewed" || searchParams.get("view")) return;
        const next = new URLSearchParams(searchParams);
        next.set("view", "products");
        setSearchParams(next, { replace: true });
    }, [activeStage.key, searchKey, searchParams, setSearchParams]);

    const selectStage = (stageKey) => {
        const next = new URLSearchParams(searchParams);
        next.set("stage", stageKey);
        if (stageKey === "reviewed") next.set("view", "products");
        else next.delete("view");
        setSearchParams(next, { replace: true });
    };

    return (
        <div className="space-y-5" dir="rtl" data-testid="fulfillment-v2-page">
            <header className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
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

            {activeStage.key === "pending_review" ? (
                <OrderReview embedded />
            ) : activeStage.key === "reviewed" ? (
                reviewedView === "files"
                    ? <PreparationFilesRegistry />
                    : <ReviewedOrders />
            ) : activeStage.key === "ready_to_ship" ? (
                <ReadyToShipOrders />
            ) : (
                <PlannedStage stage={activeStage} />
            )}
        </div>
    );
}

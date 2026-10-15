// Mezan OS V2 governed preparation workspace.
import { useEffect, useMemo } from "react";
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
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";

import OrderReview from "./OrderReview";
import ReviewedOrders from "./ReviewedOrders";
import CompletedFulfillmentOrders from "../components/fulfillment/CompletedFulfillmentOrders";
import DeliveryTrackingOrders from "../components/fulfillment/DeliveryTrackingOrders";
import PreparationEmployeeReceivingWorkspace from "../components/fulfillment/PreparationEmployeeReceivingWorkspace";
import PreparationFilesRegistry from "../components/fulfillment/PreparationFilesRegistry";
import PreparationWorkDashboard from "../components/fulfillment/PreparationWorkDashboard";
import ReadyToShipOrders from "../components/fulfillment/ReadyToShipOrders";
import StoreCourierDispatchWorkspace from "../components/fulfillment/StoreCourierDispatchWorkspace";
import StoreCourierMyShipments from "../components/fulfillment/StoreCourierMyShipments";
import SupplierReceivingWorkspace from "../components/fulfillment/SupplierReceivingWorkspace";
import { userHasPermission } from "../components/PermissionRoute";
import { useAuth } from "../context/AuthContext";


const STORE_COURIER_ASSIGN_PERMISSION = "fulfillment.store_courier.assign";
const STORE_COURIER_DELIVER_PERMISSION = "fulfillment.store_courier.deliver";
const COURIER_STAGE_KEYS = new Set(["courier_dispatch", "delivering", "delivered"]);


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
        label: "قيد التجهيز",
        shortLabel: "قيد التجهيز",
        Icon: Gear,
        description: "متابعة الملفات والقطع المسندة، الموظف المسؤول، الخدمات والموعد المتوقع.",
    },
    {
        key: "preparation",
        label: "استلام المورد",
        shortLabel: "استلام المورد",
        Icon: Queue,
        description: "إدارة مسارات المستودع والمورد والتصنيع الداخلي وملفات التجهيز.",
    },
    {
        key: "assembly",
        label: "الاستلام من التجهيز",
        shortLabel: "الاستلام من التجهيز",
        Icon: Cube,
        description: "البحث برقم الطلب أو تصوير المنتج، ثم استلامه جاهزًا من موظف التجهيز.",
    },
    {
        key: "ready_to_ship",
        label: "التجميع والعنونة",
        shortLabel: "التجميع والعنونة",
        Icon: Package,
        description: "تجميع الطلب وعنونته، والتغليف داخله فقط عندما يكون مطلوبًا.",
    },
    {
        key: "completed",
        label: "تم التنفيذ",
        shortLabel: "تم التنفيذ",
        Icon: CheckCircle,
        description: "طباعة بوليصة شركة الشحن أو بوليصة مندوب المتجر بعد اكتمال التجميع.",
    },
    {
        key: "courier_dispatch",
        label: "إدارة الموصلين",
        shortLabel: "إدارة الموصلين",
        Icon: UsersThree,
        description: "اختيار موصل المتجر ثم تصوير بوليصة أماسي لإسناد الشحنة إليه.",
    },
    {
        key: "delivering",
        label: "جاري التوصيل",
        shortLabel: "جاري التوصيل",
        Icon: Truck,
        description: "متابعة الشحنة والمسؤول عن توصيلها وحالة التسليم الحالية.",
    },
    {
        key: "delivered",
        label: "تم التوصيل",
        shortLabel: "تم التوصيل",
        Icon: CheckCircle,
        description: "الطلبات التي اكتملت دورة تنفيذها وتسليمها للعميل.",
    },
];


const MY_PRODUCTS_NAVIGATION_ITEM = {
    key: "my_products",
    label: "إدارة منتجاتي",
    shortLabel: "إدارة منتجاتي",
    Icon: Cube,
    workspace: "my-products",
};


export const FULFILLMENT_NAVIGATION_ITEMS = [
    ...FULFILLMENT_STAGES.slice(0, 3),
    MY_PRODUCTS_NAVIGATION_ITEM,
    ...FULFILLMENT_STAGES.slice(3),
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
                        <p className="mt-1 text-sm leading-6">تُبنى بعد اكتمال واعتماد المرحلة السابقة حتى لا تنتقل الطلبات إلى الأمام قبل استيفاء شروطها.</p>
                    </div>
                </div>
            </div>
        </section>
    );
}


export default function FulfillmentV2() {
    const { user } = useAuth();
    const [searchParams, setSearchParams] = useSearchParams();
    const searchKey = searchParams.toString();
    const isOwner = user?.is_owner === true || String(user?.role || "").toLowerCase() === "owner";
    const canAssignStoreCourier = isOwner || userHasPermission(user, STORE_COURIER_ASSIGN_PERMISSION);
    const canDeliverStoreCourier = isOwner || userHasPermission(user, STORE_COURIER_DELIVER_PERMISSION);
    const courierOnly = canDeliverStoreCourier && !canAssignStoreCourier;
    const visibleNavigationItems = useMemo(
        () => courierOnly
            ? FULFILLMENT_STAGES.filter((stage) => COURIER_STAGE_KEYS.has(stage.key))
            : FULFILLMENT_NAVIGATION_ITEMS,
        [courierOnly],
    );
    const myProductsWorkspace = !courierOnly && searchParams.get("workspace") === "my-products";
    const requestedStage = String(
        searchParams.get("stage") || (courierOnly ? "courier_dispatch" : "pending_review"),
    ).trim();
    const activeStage = useMemo(() => {
        const requested = FULFILLMENT_STAGES.find((stage) => stage.key === requestedStage);
        if (courierOnly && (!requested || !COURIER_STAGE_KEYS.has(requested.key))) {
            return FULFILLMENT_STAGES.find((stage) => stage.key === "courier_dispatch");
        }
        return requested || FULFILLMENT_STAGES[0];
    }, [courierOnly, requestedStage]);
    const reviewedView = activeStage.key === "reviewed" && searchParams.get("view") === "files"
        ? "files"
        : "products";
    const currentWindowLabel = activeStage.key === "reviewed" && reviewedView === "files"
        ? "سجل ملفات التجهيز"
        : activeStage.label;
    const ActiveStageIcon = activeStage.Icon;

    useEffect(() => {
        if (!courierOnly) return;
        const requested = String(searchParams.get("stage") || "").trim();
        if (COURIER_STAGE_KEYS.has(requested) && !searchParams.get("workspace")) return;
        const next = new URLSearchParams();
        next.set("stage", "courier_dispatch");
        setSearchParams(next, { replace: true });
    }, [courierOnly, searchKey, searchParams, setSearchParams]);

    useEffect(() => {
        if (activeStage.key !== "reviewed" || searchParams.get("view")) return;
        const next = new URLSearchParams(searchParams);
        next.set("view", "products");
        setSearchParams(next, { replace: true });
    }, [activeStage.key, searchKey, searchParams, setSearchParams]);

    const selectStage = (stageKey, params = {}) => {
        const next = new URLSearchParams(searchParams);
        next.delete("workspace");
        next.set("stage", stageKey);
        if (stageKey === "reviewed") next.set("view", params.view || "products");
        else next.delete("view");
        if (params.search) next.set("search", params.search);
        else next.delete("search");
        setSearchParams(next, { replace: true });
    };

    const selectNavigationItem = (item) => {
        if (item.workspace === "my-products") {
            setSearchParams(new URLSearchParams("workspace=my-products"), { replace: true });
            return;
        }
        selectStage(item.key);
    };

    const showFulfillmentOverview = () => {
        if (courierOnly) {
            setSearchParams(new URLSearchParams("stage=courier_dispatch"), { replace: true });
            return;
        }
        setSearchParams(new URLSearchParams(), { replace: true });
    };

    const stageContent = myProductsWorkspace ? (
        <PreparationWorkDashboard initialView="my-products" standalone />
    ) : activeStage.key === "pending_review" ? (
        <OrderReview embedded initialSearch={searchParams.get("search") || ""} />
    ) : activeStage.key === "reviewed" ? (
        reviewedView === "files" ? <PreparationFilesRegistry /> : <ReviewedOrders />
    ) : activeStage.key === "in_progress" ? (
        <PreparationWorkDashboard initialView="my-work" />
    ) : activeStage.key === "preparation" ? (
        <SupplierReceivingWorkspace />
    ) : activeStage.key === "assembly" ? (
        <PreparationEmployeeReceivingWorkspace />
    ) : activeStage.key === "ready_to_ship" ? (
        <ReadyToShipOrders />
    ) : activeStage.key === "completed" ? (
        <CompletedFulfillmentOrders />
    ) : activeStage.key === "courier_dispatch" ? (
        canAssignStoreCourier
            ? <StoreCourierDispatchWorkspace />
            : <StoreCourierMyShipments stage="waiting" />
    ) : activeStage.key === "delivering" || activeStage.key === "delivered" ? (
        courierOnly
            ? <StoreCourierMyShipments stage={activeStage.key} />
            : <DeliveryTrackingOrders stage={activeStage.key} />
    ) : (
        <PlannedStage stage={activeStage} />
    );

    return (
        <div className="space-y-5" dir="rtl" data-testid="fulfillment-v2-page">
            {!myProductsWorkspace && (
                <header className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm lg:hidden" data-testid="fulfillment-mobile-stage-header">
                    <div className="flex items-center gap-3 bg-emerald-800 px-3 py-3 text-white">
                        <button type="button" onClick={showFulfillmentOverview} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10" aria-label="العودة إلى بداية إدارة التجهيز">
                            <ArrowRight size={21} weight="bold" />
                        </button>
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10">
                            <ActiveStageIcon size={22} weight="duotone" />
                        </span>
                        <div className="min-w-0 flex-1">
                            <div className="text-[10px] font-black text-emerald-100">{courierOnly ? "توصيل مندوب المتجر" : "إدارة التجهيز"}</div>
                            <h1 className="truncate text-lg font-black">{currentWindowLabel}</h1>
                        </div>
                    </div>
                    <details className="group border-t border-emerald-100">
                        <summary className="cursor-pointer list-none px-4 py-2.5 text-center text-xs font-black text-emerald-800">الانتقال إلى مرحلة أخرى</summary>
                        <nav className="grid grid-cols-2 gap-2 border-t border-slate-100 bg-slate-50 p-3" aria-label="مراحل إدارة التجهيز للجوال">
                            {visibleNavigationItems.map((item) => (
                                <button
                                    key={item.key}
                                    type="button"
                                    onClick={() => selectNavigationItem(item)}
                                    className={`rounded-xl border px-3 py-2 text-xs font-black ${item.key === activeStage.key ? "border-emerald-500 bg-emerald-50 text-emerald-900" : "border-slate-200 bg-white text-slate-700"}`}
                                >
                                    {item.shortLabel}
                                </button>
                            ))}
                        </nav>
                    </details>
                </header>
            )}

            {!myProductsWorkspace && (
                <header className="hidden overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm lg:block">
                    <div className="bg-gradient-to-l from-violet-700 via-violet-600 to-indigo-700 px-5 py-6 text-white sm:px-7">
                        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                            <div>
                                <div className="text-sm font-bold text-violet-100">Mezan OS V2</div>
                                <h1 className="mt-1 text-2xl font-black sm:text-3xl">{courierOnly ? "توصيل مندوب المتجر" : "إدارة التجهيز"}</h1>
                                <p className="mt-2 max-w-3xl text-sm leading-6 text-violet-100">
                                    {courierOnly
                                        ? "استلام الشحنات المسندة لك، متابعة جاري التوصيل، ثم تسجيل التسليم للعميل داخل نفس المسار."
                                        : "إدارة دورة الطلب من المراجعة والرفع والتجهيز والاستلام، حتى الشحن والتوصيل، داخل صفحة تشغيل واحدة."}
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
                                <div className="text-sm font-extrabold text-slate-800">{courierOnly ? "مراحل التوصيل" : "تبويبات إدارة التجهيز"}</div>
                                <p className="mt-0.5 text-xs text-slate-500">كل تبويب يمثل مرحلة مستقلة من دورة الطلب.</p>
                            </div>
                            <span className="rounded-full border border-violet-200 bg-white px-3 py-1 text-xs font-bold text-violet-700">
                                {visibleNavigationItems.length} مراحل
                            </span>
                        </div>
                        <nav className="flex gap-2 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-2 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden" aria-label="مراحل إدارة التجهيز">
                            {visibleNavigationItems.map((item, index) => {
                                const active = item.key === activeStage.key;
                                const Icon = item.Icon;
                                return (
                                    <button
                                        type="button"
                                        key={item.key}
                                        data-testid={`fulfillment-stage-tab-${item.key}`}
                                        onClick={() => selectNavigationItem(item)}
                                        aria-current={active ? "page" : undefined}
                                        className={`group flex min-w-[170px] items-center gap-3 rounded-xl border px-4 py-3 text-right transition ${active ? "border-violet-500 bg-violet-50 text-violet-950 ring-2 ring-violet-100" : "border-transparent bg-white text-slate-600 hover:border-violet-200 hover:bg-slate-50"}`}
                                    >
                                        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${active ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-500 group-hover:bg-violet-100 group-hover:text-violet-700"}`}>
                                            <Icon size={19} weight="duotone" />
                                        </span>
                                        <span className="min-w-0">
                                            <span className="block text-[10px] font-bold opacity-60">المرحلة {index + 1}</span>
                                            <span className="block whitespace-nowrap text-sm font-extrabold">{item.shortLabel}</span>
                                        </span>
                                    </button>
                                );
                            })}
                        </nav>
                    </div>
                </header>
            )}

            {stageContent}
        </div>
    );
}

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useLocation } from "react-router-dom";
import {
    ChartLineUp,
    ChatsCircle,
    ClipboardText,
    Cube,
    House,
    List,
    MagnifyingGlass,
    Package,
    Plug,
    Queue,
    Robot,
    CalendarBlank,
    UsersThree,
    Warehouse,
} from "@phosphor-icons/react";
import Sidebar from "./Sidebar";
import GlobalSearch from "./GlobalSearch";
import { Toaster } from "../components/ui/sonner";
import { LogoIcon } from "./MezanLogo";
import NotificationBell from "./NotificationBell";
import GlobalIntegrationAlert from "./GlobalIntegrationAlert";
import QoyodUnsentHeaderAlert from "./QoyodUnsentHeaderAlert";
import OrderUiEnhancements from "./OrderUiEnhancements";
import DashboardAnalyticsPlacement from "./DashboardAnalyticsPlacement";
import DashboardSnapchatAccountsPlacement from "./DashboardSnapchatAccountsPlacement";
import GoogleAdsAllPlatformsCard from "./GoogleAdsAllPlatformsCard";
import WarehouseHierarchyWorkspace from "../pages/WarehouseHierarchyWorkspace";
import ProductIntakeWorkspace from "../pages/ProductIntakeWorkspace";
import StoreOperationsAccessWorkspace from "../pages/StoreOperationsAccessWorkspace";
import StoreDrivers from "../pages/StoreDrivers";
import StoreDriverHandover from "../pages/StoreDriverHandover";
import StoreDeliveryCustomerService from "../pages/StoreDeliveryCustomerService";
import StoreDeliveryPaymentReview from "../pages/StoreDeliveryPaymentReview";
import StoreDeliverySettlements from "../pages/StoreDeliverySettlements";
import MarketingPlatformWorkspace, {
    isMarketingPlatformProvider,
} from "../pages/MarketingPlatformWorkspace";
import MezanV2NavigationShell, {
    isMezanV2Route,
} from "./MezanV2NavigationShell";

// Compatibility contract for focused V2 workflows. The visible navigation is
// now rendered by MezanV2NavigationShell; these definitions remain only so
// existing route-governance checks keep proving that no V2 entry point vanished.
const V2_LINKS = [
    { to: "/dashboard-v2", label: "لوحة التحكم", Icon: House },
    { to: "/orders-v2", label: "الطلبات", Icon: Package },
    { to: "/fulfillment-v2", label: "إدارة التجهيز", Icon: Queue },
    { to: "/fulfillment-v2?workspace=store-driver-handover", label: "تسليم الشحنات للموصلين", Icon: Queue },
    { to: "/inventory-receiving-v2", label: "استلام المخزون", Icon: ClipboardText },
    { to: "/employees-v2", label: "الموظفون والرواتب", Icon: UsersThree },
    { to: "/employees-v2?workspace=drivers", label: "موصلو المتجر", Icon: UsersThree },
    { to: "/recurring-obligations", label: "الالتزامات والمصاريف الدورية", Icon: CalendarBlank },
    { to: "/employees-v2?workspace=permissions", label: "الصلاحيات وإدارة التجهيز", Icon: UsersThree },
    { to: "/products-v2", label: "المنتجات", Icon: Package },
    { to: "/products-v2?workspace=intake", label: "استقبال المنتجات", Icon: Robot },
    { to: "/products-v2?workspace=access", label: "الفريق والصلاحيات", Icon: UsersThree },
    { to: "/components-v2", label: "المكونات", Icon: Cube },
    { to: "/components-v2?workspace=warehouse", label: "الفروع والمخازن", Icon: Warehouse },
    { to: "/integrations-v2", label: "التطبيقات والتكاملات", Icon: Plug },
    { to: "/customer-intelligence", label: "ذكاء العملاء", Icon: ChatsCircle },
    { to: "/customer-intelligence?workspace=store-delivery", label: "تعليمات توصيل الموصلين", Icon: ChatsCircle },
    { to: "/ads-manager", label: "مدير الإعلانات", Icon: ChartLineUp },
];

function legacySpecificChildContract(location) {
    const pathname = location.pathname;
    const hasSpecificChild = V2_LINKS.some((item) => item.to.startsWith(`${pathname}?`));
    return hasSpecificChild && Boolean(location.search);
}

function WarehouseSidebarLink({ location, onNavigate }) {
    const [target, setTarget] = useState(null);

    useEffect(() => {
        let frame;
        const locate = () => {
            const componentsLink = document.querySelector('[data-testid="nav-mezan-os-components"]');
            if (componentsLink?.parentElement) {
                setTarget(componentsLink.parentElement);
                return;
            }
            frame = window.requestAnimationFrame(locate);
        };
        locate();
        return () => {
            if (frame) window.cancelAnimationFrame(frame);
        };
    }, []);

    if (!target) return null;
    const active = location.pathname === "/components-v2"
        && new URLSearchParams(location.search).get("workspace") === "warehouse";

    return createPortal(
        <NavLink
            to="/components-v2?workspace=warehouse"
            onClick={onNavigate}
            data-testid="nav-mezan-os-warehouses"
            className={`flex items-center gap-2.5 ps-4 pe-3 py-2 rounded-lg text-[13.5px] transition-colors ${active ? "bg-brand text-white font-semibold" : "text-foreground hover:bg-accent hover:text-brand"}`}
        >
            <Warehouse size={17} weight="duotone" />
            <span className="truncate flex-1">الفروع والمخازن</span>
        </NavLink>,
        target,
    );
}

export default function Layout({ children }) {
    const [legacyMenuOpen, setLegacyMenuOpen] = useState(false);
    const location = useLocation();
    const searchParams = new URLSearchParams(location.search);
    const workspace = searchParams.get("workspace");
    const marketingProvider = searchParams.get("provider");
    const isMezanV2 = isMezanV2Route(location.pathname);
    const hasLegacySpecificChild = legacySpecificChildContract(location);
    const isWarehouseV2 = location.pathname === "/components-v2" && workspace === "warehouse";
    const isProductIntake = location.pathname === "/products-v2" && workspace === "intake";
    const isStoreAccess = location.pathname === "/products-v2" && workspace === "access";
    const isStoreDrivers = location.pathname === "/employees-v2" && workspace === "drivers";
    const isStoreDriverHandover = location.pathname === "/fulfillment-v2" && workspace === "store-driver-handover";
    const isStoreDeliveryCustomerService = location.pathname === "/customer-intelligence" && workspace === "store-delivery";
    const isStoreDeliveryPaymentReview = location.pathname === "/bank-transfer-review" && workspace === "store-delivery";
    const isStoreDeliverySettlements = location.pathname === "/settlements-overview" && workspace === "store-delivery";
    const isMarketingPlatform = location.pathname === "/ads-manager"
        && isMarketingPlatformProvider(marketingProvider);
    const isLegacyDashboard = location.pathname === "/legacy-dashboard";
    const isMezanV2Dashboard = location.pathname === "/dashboard-v2";
    const showsDashboardAnalytics = isLegacyDashboard || isMezanV2Dashboard;
    const pageContent = isWarehouseV2
        ? <WarehouseHierarchyWorkspace />
        : isProductIntake
            ? <ProductIntakeWorkspace />
            : isStoreAccess
                ? <StoreOperationsAccessWorkspace />
                : isStoreDrivers
                    ? <StoreDrivers />
                    : isStoreDriverHandover
                        ? <StoreDriverHandover />
                        : isStoreDeliveryCustomerService
                            ? <StoreDeliveryCustomerService />
                            : isStoreDeliveryPaymentReview
                                ? <StoreDeliveryPaymentReview />
                                : isStoreDeliverySettlements
                                    ? <StoreDeliverySettlements />
                                    : isMarketingPlatform
                                        ? <MarketingPlatformWorkspace provider={marketingProvider} />
                                        : children;

    useEffect(() => {
        setLegacyMenuOpen(false);
    }, [location.pathname, location.search]);

    useEffect(() => {
        document.body.style.overflow = legacyMenuOpen ? "hidden" : "";
        return () => {
            document.body.style.overflow = "";
        };
    }, [legacyMenuOpen]);

    return (
        <div className="min-h-screen bg-background grain">
            {legacyMenuOpen && (
                <div
                    className="fixed inset-0 z-40 hidden bg-slate-950/45 backdrop-blur-[1px] lg:block"
                    onClick={() => setLegacyMenuOpen(false)}
                    data-testid="desktop-sidebar-backdrop"
                    aria-hidden="true"
                />
            )}

            {legacyMenuOpen && (
                <>
                    <Sidebar mobileOpen onMobileClose={() => setLegacyMenuOpen(false)} />
                    <WarehouseSidebarLink location={location} onNavigate={() => setLegacyMenuOpen(false)} />
                </>
            )}

            <OrderUiEnhancements />
            <GoogleAdsAllPlatformsCard location={location} />

            {!isMezanV2 && (
                <header className="sticky top-0 z-30 border-b border-border bg-white/95 backdrop-blur lg:hidden" data-testid="mobile-header">
                    <div className="flex h-14 items-center justify-between px-4">
                        <div className="flex items-center gap-2.5" data-testid="mobile-header-brand">
                            <LogoIcon size={32} />
                            <div>
                                <div className="text-brand text-base font-extrabold leading-tight tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }}>
                                    <span>MEZ</span><span className="text-accent-green">AN</span>
                                </div>
                                <div className="text-[10px] font-bold leading-tight text-muted-foreground">ميزان · تحليلات</div>
                            </div>
                        </div>
                        <button type="button" onClick={() => setLegacyMenuOpen(true)} className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-border px-3 text-sm font-bold text-foreground transition-colors hover:bg-accent" data-testid="mobile-menu-btn" aria-label="فتح قائمة كل صفحات ميزان"><List size={22} weight="bold" />الكل</button>
                    </div>
                    <div className="px-4 pb-3"><GlobalSearch compact /></div>
                </header>
            )}

            {!isMezanV2 && (
                <>
                    <div className="fixed top-3 end-3 z-40 hidden lg:block" data-testid="desktop-notification-bell-wrap"><NotificationBell /></div>
                    <div className="fixed top-2 end-24 z-40 lg:hidden" data-testid="mobile-notification-bell-wrap"><NotificationBell /></div>
                </>
            )}

            {isMezanV2 && (
                <div className="sticky top-0 z-40 border-b border-emerald-950/20 bg-background/95 backdrop-blur sm:px-4 sm:py-2 lg:px-6" data-testid="mezan-v2-unified-header">
                    <div className="mx-auto max-w-[1900px]">
                        <MezanV2NavigationShell location={location} onOpenAll={() => setLegacyMenuOpen(true)} searchForm={<GlobalSearch compact />} notificationControl={<NotificationBell />} />
                    </div>
                </div>
            )}

            <GlobalIntegrationAlert />
            <QoyodUnsentHeaderAlert />

            <main className="min-h-screen" data-testid="main-content" data-v2-specific-child={hasLegacySpecificChild ? "true" : "false"}>
                {!isMezanV2 && (
                    <div className="relative sticky top-0 z-20 hidden border-b border-slate-200 bg-white/95 px-6 py-3 backdrop-blur lg:flex lg:items-center lg:justify-center">
                        <button type="button" onClick={() => setLegacyMenuOpen(true)} className="absolute start-6 inline-flex h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-extrabold text-slate-800 shadow-sm transition hover:border-violet-200 hover:bg-violet-50 hover:text-violet-800" data-testid="legacy-open-all"><List size={22} weight="bold" />الكل</button>
                        <GlobalSearch />
                    </div>
                )}

                <div className="mx-auto max-w-[1600px] px-4 py-4 sm:px-6 sm:py-6 lg:px-10 lg:py-8">
                    {pageContent}
                    <DashboardAnalyticsPlacement active={showsDashboardAnalytics} />
                    <DashboardSnapchatAccountsPlacement active={showsDashboardAnalytics} />
                </div>
            </main>
            <Toaster richColors position="top-center" />
        </div>
    );
}

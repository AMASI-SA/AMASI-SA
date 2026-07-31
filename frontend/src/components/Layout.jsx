import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate, NavLink } from "react-router-dom";
import { List, MagnifyingGlass, House, Package, Queue, Cube, Warehouse, Plug, Robot, UsersThree, ChartLineUp, ChatsCircle, ClipboardText } from "@phosphor-icons/react";
import Sidebar from "./Sidebar";
import { Toaster } from "../components/ui/sonner";
import { LogoIcon } from "./MezanLogo";
import NotificationBell from "./NotificationBell";
import OrderUiEnhancements from "./OrderUiEnhancements";
import GoogleAnalyticsRealtimeCards from "./GoogleAnalyticsRealtimeCards";
import GoogleAnalyticsTrafficSourcesCard from "./GoogleAnalyticsTrafficSourcesCard";
import WarehouseHierarchyWorkspace from "../pages/WarehouseHierarchyWorkspace";
import ProductIntakeWorkspace from "../pages/ProductIntakeWorkspace";
import StoreOperationsAccessWorkspace from "../pages/StoreOperationsAccessWorkspace";

function GlobalOrderSearch({ compact = false }) {
    const navigate = useNavigate();
    const [orderNumber, setOrderNumber] = useState("");

    function submit(event) {
        event.preventDefault();
        const normalized = String(orderNumber || "").replace(/^#/, "").trim();
        if (!normalized) return;
        navigate(`/orders-v2/${encodeURIComponent(normalized)}`);
        setOrderNumber("");
    }

    return (
        <form onSubmit={submit} className={`flex items-center overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${compact ? "h-10 w-full" : "h-12 w-full max-w-2xl"}`} role="search" aria-label="البحث العام عن طلب" data-testid="global-order-search">
            <div className="relative min-w-0 flex-1">
                <MagnifyingGlass size={compact ? 18 : 21} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={orderNumber} onChange={(event) => setOrderNumber(event.target.value)} inputMode="numeric" placeholder="ابحث برقم الطلب من أي صفحة…" className="h-full w-full bg-transparent pr-10 pl-3 text-sm text-slate-800 outline-none placeholder:text-slate-400" />
            </div>
            <button type="submit" className={`inline-flex h-full shrink-0 items-center justify-center bg-violet-700 px-4 font-bold text-white transition hover:bg-violet-800 ${compact ? "text-xs" : "text-sm"}`}>بحث</button>
        </form>
    );
}

const V2_LINKS = [
    { to: "/dashboard-v2", label: "لوحة التحكم", Icon: House },
    { to: "/orders-v2", label: "الطلبات", Icon: Package },
    { to: "/fulfillment-v2", label: "إدارة التجهيز", Icon: Queue },
    { to: "/inventory-receiving-v2", label: "استلام المخزون", Icon: ClipboardText },
    { to: "/products-v2", label: "المنتجات", Icon: Package },
    { to: "/products-v2?workspace=intake", label: "استقبال المنتجات", Icon: Robot },
    { to: "/products-v2?workspace=access", label: "الفريق والصلاحيات", Icon: UsersThree },
    { to: "/components-v2", label: "المكونات", Icon: Cube },
    { to: "/components-v2?workspace=warehouse", label: "الفروع والمخازن", Icon: Warehouse },
    { to: "/integrations-v2", label: "التطبيقات والتكاملات", Icon: Plug },
    { to: "/customer-intelligence", label: "ذكاء العملاء", Icon: ChatsCircle },
    { to: "/ads-manager", label: "مدير الإعلانات", Icon: ChartLineUp },
];

function MezanV2Navigation({ location }) {
    const isV2 = ["/dashboard-v2", "/orders-v2", "/fulfillment-v2", "/inventory-receiving-v2", "/products-v2", "/components-v2", "/integrations-v2", "/customer-intelligence", "/ads-manager"].some((prefix) => location.pathname.startsWith(prefix));
    if (!isV2) return null;
    return (
        <nav className="mb-5 flex flex-wrap gap-2 rounded-xl border border-violet-100 bg-white p-2 shadow-sm" aria-label="صفحات Mezan OS V2">
            {V2_LINKS.map(({ to, label, Icon }) => {
                const [pathname, search = ""] = to.split("?");
                const hasSpecificChild = V2_LINKS.some((item) => item.to.startsWith(`${pathname}?`));
                const active = location.pathname === pathname
                    && (search ? location.search === `?${search}` : !hasSpecificChild || !location.search);
                return (
                    <NavLink key={to} to={to} className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold transition ${active ? "bg-violet-700 text-white" : "text-slate-700 hover:bg-violet-50"}`}>
                        <Icon size={18} weight="duotone" />
                        {label}
                    </NavLink>
                );
            })}
        </nav>
    );
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
    const [mobileOpen, setMobileOpen] = useState(false);
    const location = useLocation();
    const workspace = new URLSearchParams(location.search).get("workspace");
    const isWarehouseV2 = location.pathname === "/components-v2" && workspace === "warehouse";
    const isProductIntake = location.pathname === "/products-v2" && workspace === "intake";
    const isStoreAccess = location.pathname === "/products-v2" && workspace === "access";
    const isMainDashboard = location.pathname === "/";
    const pageContent = isWarehouseV2
        ? <WarehouseHierarchyWorkspace />
        : isProductIntake
            ? <ProductIntakeWorkspace />
            : isStoreAccess
                ? <StoreOperationsAccessWorkspace />
                : children;

    useEffect(() => { setMobileOpen(false); }, [location.pathname, location.search]);
    useEffect(() => {
        document.body.style.overflow = mobileOpen ? "hidden" : "";
        return () => { document.body.style.overflow = ""; };
    }, [mobileOpen]);

    return (
        <div className="min-h-screen bg-background grain">
            <Sidebar mobileOpen={mobileOpen} onMobileClose={() => setMobileOpen(false)} />
            <WarehouseSidebarLink location={location} onNavigate={() => setMobileOpen(false)} />
            <OrderUiEnhancements />

            <header className="lg:hidden sticky top-0 z-30 border-b border-border bg-white/95 backdrop-blur" data-testid="mobile-header">
                <div className="flex h-14 items-center justify-between px-4">
                    <div className="flex items-center gap-2.5" data-testid="mobile-header-brand">
                        <LogoIcon size={32} />
                        <div>
                            <div className="text-brand text-base font-extrabold leading-tight tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }}><span>MEZ</span><span className="text-accent-green">AN</span></div>
                            <div className="text-[10px] font-bold leading-tight text-muted-foreground">ميزان · تحليلات</div>
                        </div>
                    </div>
                    <button type="button" onClick={() => setMobileOpen(true)} className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border text-foreground transition-colors hover:bg-accent" data-testid="mobile-menu-btn" aria-label="فتح القائمة"><List size={22} weight="bold" /></button>
                </div>
                <div className="px-4 pb-3"><GlobalOrderSearch compact /></div>
            </header>

            <div className="fixed top-3 end-3 z-40 hidden lg:block" data-testid="desktop-notification-bell-wrap"><NotificationBell /></div>
            <div className="lg:hidden fixed top-2 end-16 z-40" data-testid="mobile-notification-bell-wrap"><NotificationBell /></div>

            <main className="min-h-screen lg:ps-64" data-testid="main-content">
                <div className="sticky top-0 z-20 hidden border-b border-slate-200 bg-white/95 px-6 py-3 backdrop-blur lg:flex lg:items-center lg:justify-center"><GlobalOrderSearch /></div>
                <div className="mx-auto max-w-7xl px-4 py-4 sm:px-6 sm:py-6 lg:px-10 lg:py-8">
                    <MezanV2Navigation location={location} />
                    {pageContent}
                    {isMainDashboard && (
                        <div className="mt-6 space-y-6" data-testid="dashboard-ga4-analytics-wrap">
                            <GoogleAnalyticsRealtimeCards />
                            <GoogleAnalyticsTrafficSourcesCard />
                        </div>
                    )}
                </div>
            </main>
            <Toaster richColors position="top-center" />
        </div>
    );
}

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { List, MagnifyingGlass, Warehouse } from "@phosphor-icons/react";
import Sidebar from "./Sidebar";
import { Toaster } from "../components/ui/sonner";
import { LogoIcon } from "./MezanLogo";
import NotificationBell from "./NotificationBell";
import OrderUiEnhancements from "./OrderUiEnhancements";
import WarehouseHierarchyWorkspace from "../pages/WarehouseHierarchyWorkspace";
import ProductIntakeWorkspace from "../pages/ProductIntakeWorkspace";
import StoreOperationsAccessWorkspace from "../pages/StoreOperationsAccessWorkspace";
import MezanV2NavigationShell, {
    isMezanV2Route,
} from "./MezanV2NavigationShell";

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
        <form
            onSubmit={submit}
            className={`flex items-center overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${compact ? "h-10 w-full" : "h-12 w-full max-w-2xl"}`}
            role="search"
            aria-label="البحث العام عن طلب"
            data-testid="global-order-search"
        >
            <div className="relative min-w-0 flex-1">
                <MagnifyingGlass
                    size={compact ? 18 : 21}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                />
                <input
                    value={orderNumber}
                    onChange={(event) => setOrderNumber(event.target.value)}
                    inputMode="numeric"
                    placeholder="ابحث برقم الطلب من أي صفحة…"
                    className="h-full w-full bg-transparent pr-10 pl-3 text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
            </div>
            <button
                type="submit"
                className={`inline-flex h-full shrink-0 items-center justify-center bg-violet-700 px-4 font-bold text-white transition hover:bg-violet-800 ${compact ? "text-xs" : "text-sm"}`}
            >
                بحث
            </button>
        </form>
    );
}

function WarehouseSidebarLink({ location, onNavigate }) {
    const [target, setTarget] = useState(null);

    useEffect(() => {
        let frame;
        const locate = () => {
            const componentsLink = document.querySelector(
                '[data-testid="nav-mezan-os-components"]',
            );
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
    const workspace = new URLSearchParams(location.search).get("workspace");
    const isMezanV2 = isMezanV2Route(location.pathname);
    const isWarehouseV2 = location.pathname === "/components-v2" && workspace === "warehouse";
    const isProductIntake = location.pathname === "/products-v2" && workspace === "intake";
    const isStoreAccess = location.pathname === "/products-v2" && workspace === "access";
    const pageContent = isWarehouseV2
        ? <WarehouseHierarchyWorkspace />
        : isProductIntake
            ? <ProductIntakeWorkspace />
            : isStoreAccess
                ? <StoreOperationsAccessWorkspace />
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
                    <Sidebar
                        mobileOpen
                        onMobileClose={() => setLegacyMenuOpen(false)}
                    />
                    <WarehouseSidebarLink
                        location={location}
                        onNavigate={() => setLegacyMenuOpen(false)}
                    />
                </>
            )}

            <OrderUiEnhancements />

            <header
                className="sticky top-0 z-30 border-b border-border bg-white/95 backdrop-blur lg:hidden"
                data-testid="mobile-header"
            >
                <div className="flex h-14 items-center justify-between px-4">
                    <div className="flex items-center gap-2.5" data-testid="mobile-header-brand">
                        <LogoIcon size={32} />
                        <div>
                            <div
                                className="text-brand text-base font-extrabold leading-tight tracking-wider"
                                style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }}
                            >
                                <span>MEZ</span><span className="text-accent-green">AN</span>
                            </div>
                            <div className="text-[10px] font-bold leading-tight text-muted-foreground">
                                ميزان · تحليلات
                            </div>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setLegacyMenuOpen(true)}
                        className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-border px-3 text-sm font-bold text-foreground transition-colors hover:bg-accent"
                        data-testid="mobile-menu-btn"
                        aria-label="فتح قائمة كل صفحات ميزان"
                    >
                        <List size={22} weight="bold" />
                        الكل
                    </button>
                </div>
                <div className="px-4 pb-3">
                    <GlobalOrderSearch compact />
                </div>
            </header>

            <div
                className="fixed top-3 end-3 z-40 hidden lg:block"
                data-testid="desktop-notification-bell-wrap"
            >
                <NotificationBell />
            </div>
            <div
                className="fixed top-2 end-24 z-40 lg:hidden"
                data-testid="mobile-notification-bell-wrap"
            >
                <NotificationBell />
            </div>

            <main className="min-h-screen" data-testid="main-content">
                <div className="relative sticky top-0 z-20 hidden border-b border-slate-200 bg-white/95 px-6 py-3 backdrop-blur lg:flex lg:items-center lg:justify-center">
                    {!isMezanV2 && (
                        <button
                            type="button"
                            onClick={() => setLegacyMenuOpen(true)}
                            className="absolute start-6 inline-flex h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-extrabold text-slate-800 shadow-sm transition hover:border-violet-200 hover:bg-violet-50 hover:text-violet-800"
                            data-testid="legacy-open-all"
                        >
                            <List size={22} weight="bold" />
                            الكل
                        </button>
                    )}
                    <GlobalOrderSearch />
                </div>

                <div className="mx-auto max-w-[1600px] px-4 py-4 sm:px-6 sm:py-6 lg:px-10 lg:py-8">
                    {isMezanV2 && (
                        <MezanV2NavigationShell
                            location={location}
                            onOpenAll={() => setLegacyMenuOpen(true)}
                        />
                    )}
                    {pageContent}
                </div>
            </main>
            <Toaster richColors position="top-center" />
        </div>
    );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    CaretDown,
    ChartLineUp,
    ChatsCircle,
    Cube,
    House,
    List,
    Megaphone,
    Package,
    Plug,
    Queue,
    Robot,
    Storefront,
    Warehouse,
} from "@phosphor-icons/react";

const FULFILLMENT_ITEMS = [
    { to: "/fulfillment-v2?stage=pending_review", label: "بانتظار المراجعة" },
    { to: "/fulfillment-v2?stage=reviewed", label: "تم المراجعة" },
    { to: "/fulfillment-v2?stage=in_progress", label: "قيد التنفيذ" },
    { to: "/fulfillment-v2?stage=preparation", label: "التجهيز" },
    { to: "/fulfillment-v2?stage=assembly", label: "الاستلام والتجميع" },
    { to: "/fulfillment-v2?stage=ready_to_ship", label: "جاهز للشحن" },
    { to: "/fulfillment-v2?stage=completed", label: "تم التنفيذ" },
    { to: "/fulfillment-v2?stage=delivering", label: "جاري التوصيل" },
    { to: "/fulfillment-v2?stage=delivered", label: "تم التوصيل" },
];

export const MEZAN_V2_NAV_SECTIONS = [
    {
        id: "home",
        label: "الرئيسية",
        Icon: House,
        items: [
            { to: "/dashboard-v2", label: "الرئيسية", exactSearch: true },
        ],
    },
    {
        id: "orders",
        label: "الطلبات",
        Icon: Package,
        items: [
            { to: "/orders-v2", label: "كل الطلبات", pathPrefix: "/orders-v2", exactSearch: true },
            ...FULFILLMENT_ITEMS,
            { to: "/inventory-receiving-v2", label: "استلام المخزون" },
        ],
    },
    {
        id: "products",
        label: "المنتجات",
        Icon: Cube,
        items: [
            { to: "/products-v2", label: "إدارة المنتجات", exactSearch: true },
            { to: "/products-v2?workspace=intake", label: "استقبال المنتجات" },
            { to: "/components-v2", label: "مكونات المنتجات", exactSearch: true },
            { to: "/components-v2?workspace=warehouse", label: "الفروع والمخازن" },
        ],
    },
    {
        id: "marketing",
        label: "التسويق",
        Icon: Megaphone,
        items: [
            { to: "/ads-manager", label: "الرئيسية الإعلانية", exactSearch: true },
            { to: "/integrations-v2?provider=snapchat_ads", label: "سناب شات" },
            { to: "/integrations-v2?provider=tiktok_ads", label: "تيك توك" },
            { to: "/integrations-v2?provider=meta_ads", label: "ميتا" },
            { to: "/integrations-v2?provider=google_analytics", label: "جوجل" },
        ],
    },
    {
        id: "apps",
        label: "التطبيقات",
        Icon: Plug,
        items: [
            { to: "/integrations-v2", label: "كل التطبيقات والتكاملات", exactSearch: true },
        ],
    },
    {
        id: "intelligence",
        label: "الذكاء الاصطناعي",
        Icon: Robot,
        items: [
            { to: "/customer-intelligence", label: "ذكاء العملاء", exactSearch: true },
        ],
    },
];

const MEZAN_V2_PATHS = [
    "/dashboard-v2",
    "/orders-v2",
    "/fulfillment-v2",
    "/inventory-receiving-v2",
    "/products-v2",
    "/components-v2",
    "/integrations-v2",
    "/customer-intelligence",
    "/ads-manager",
];

export function isMezanV2Route(pathname = "") {
    return MEZAN_V2_PATHS.some(
        (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
    );
}

function parseTarget(to) {
    const [pathname, query = ""] = String(to || "").split("?");
    return { pathname, params: new URLSearchParams(query) };
}

export function isNavigationItemActive(location, item) {
    const { pathname, params } = parseTarget(item.to);
    const currentPath = String(location?.pathname || "");
    const pathMatches = item.pathPrefix
        ? currentPath === item.pathPrefix || currentPath.startsWith(`${item.pathPrefix}/`)
        : currentPath === pathname;
    if (!pathMatches) return false;

    const currentParams = new URLSearchParams(location?.search || "");
    const expectedEntries = Array.from(params.entries());
    if (expectedEntries.length > 0) {
        return expectedEntries.every(([key, value]) => currentParams.get(key) === value);
    }
    if (item.exactSearch) return Array.from(currentParams.keys()).length === 0;
    return true;
}

export function activeNavigationSection(location) {
    return MEZAN_V2_NAV_SECTIONS.find(
        (section) => section.items.some((item) => isNavigationItemActive(location, item)),
    ) || null;
}

function SectionButton({ section, active, open, onToggle, onNavigate }) {
    const Icon = section.Icon;
    const singleItem = section.items.length === 1;
    const buttonClass = [
        "inline-flex h-12 shrink-0 items-center gap-2 rounded-xl px-4 text-sm font-extrabold transition",
        active
            ? "bg-emerald-200 text-slate-950 shadow-sm"
            : "text-slate-100 hover:bg-white/10 hover:text-white",
    ].join(" ");

    if (singleItem) {
        return (
            <Link
                to={section.items[0].to}
                className={buttonClass}
                onClick={onNavigate}
                data-testid={`mezan-v2-primary-${section.id}`}
            >
                <Icon size={22} weight="duotone" />
                <span>{section.label}</span>
            </Link>
        );
    }

    return (
        <button
            type="button"
            className={buttonClass}
            onClick={onToggle}
            aria-expanded={open}
            data-testid={`mezan-v2-primary-${section.id}`}
        >
            <Icon size={22} weight="duotone" />
            <span>{section.label}</span>
            <CaretDown
                size={14}
                weight="bold"
                className={`transition-transform ${open ? "rotate-180" : ""}`}
            />
        </button>
    );
}

export default function MezanV2NavigationShell({ location, onOpenAll }) {
    const [openSectionId, setOpenSectionId] = useState(null);
    const rootRef = useRef(null);
    const activeSection = useMemo(() => activeNavigationSection(location), [location]);

    useEffect(() => {
        setOpenSectionId(null);
    }, [location.pathname, location.search]);

    useEffect(() => {
        const closeOnOutsideClick = (event) => {
            if (rootRef.current && !rootRef.current.contains(event.target)) {
                setOpenSectionId(null);
            }
        };
        document.addEventListener("mousedown", closeOnOutsideClick);
        return () => document.removeEventListener("mousedown", closeOnOutsideClick);
    }, []);

    return (
        <div
            ref={rootRef}
            className="mb-6 overflow-visible rounded-2xl border border-slate-800 bg-slate-950 shadow-xl"
            dir="rtl"
            data-testid="mezan-v2-navigation-shell"
        >
            <div className="flex min-h-16 items-center gap-2 overflow-x-auto px-3 py-2 scrollbar-thin sm:px-4">
                <div className="flex shrink-0 items-center gap-2 border-l border-white/10 pl-3">
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-emerald-200 text-slate-950">
                        <Storefront size={24} weight="duotone" />
                    </span>
                    <div className="hidden sm:block">
                        <div className="text-sm font-black tracking-wide text-white">MEZAN 2</div>
                        <div className="text-[10px] font-bold text-slate-400">نظام تشغيل المتجر</div>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={onOpenAll}
                    className="inline-flex h-12 shrink-0 items-center gap-2 rounded-xl px-4 text-sm font-extrabold text-slate-100 transition hover:bg-white/10 hover:text-white"
                    data-testid="mezan-v2-open-all"
                >
                    <List size={24} weight="bold" />
                    <span>الكل</span>
                </button>

                {MEZAN_V2_NAV_SECTIONS.map((section) => {
                    const active = activeSection?.id === section.id;
                    const open = openSectionId === section.id;
                    return (
                        <div key={section.id} className="relative shrink-0">
                            <SectionButton
                                section={section}
                                active={active}
                                open={open}
                                onToggle={() => setOpenSectionId(open ? null : section.id)}
                                onNavigate={() => setOpenSectionId(null)}
                            />

                            {section.items.length > 1 && open && (
                                <div
                                    className="absolute right-0 top-[calc(100%+0.6rem)] z-50 max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-2 shadow-2xl"
                                    data-testid={`mezan-v2-dropdown-${section.id}`}
                                >
                                    <div className="mb-1 px-3 py-2 text-xs font-black text-emerald-200">
                                        صفحات {section.label}
                                    </div>
                                    {section.items.map((item) => {
                                        const itemActive = isNavigationItemActive(location, item);
                                        return (
                                            <Link
                                                key={item.to}
                                                to={item.to}
                                                onClick={() => setOpenSectionId(null)}
                                                className={[
                                                    "flex items-center justify-between rounded-xl px-4 py-3 text-sm font-bold transition",
                                                    itemActive
                                                        ? "bg-emerald-200 text-slate-950"
                                                        : "text-slate-100 hover:bg-white/10",
                                                ].join(" ")}
                                                data-testid={`mezan-v2-dropdown-link-${section.id}`}
                                            >
                                                <span>{item.label}</span>
                                                {itemActive && <span className="text-xs">الحالية</span>}
                                            </Link>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>

            {activeSection && activeSection.items.length > 1 && (
                <nav
                    className="flex items-center gap-1 overflow-x-auto border-t border-white/10 bg-slate-900/90 px-3 scrollbar-thin sm:px-5"
                    aria-label={`صفحات ${activeSection.label}`}
                    data-testid={`mezan-v2-secondary-${activeSection.id}`}
                >
                    {activeSection.items.map((item) => {
                        const active = isNavigationItemActive(location, item);
                        return (
                            <Link
                                key={item.to}
                                to={item.to}
                                className={[
                                    "relative shrink-0 px-4 py-4 text-sm font-extrabold transition",
                                    active
                                        ? "text-emerald-200"
                                        : "text-slate-400 hover:text-white",
                                ].join(" ")}
                                data-testid={`mezan-v2-secondary-link-${activeSection.id}`}
                            >
                                {item.label}
                                {active && (
                                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-emerald-200" />
                                )}
                            </Link>
                        );
                    })}
                </nav>
            )}
        </div>
    );
}

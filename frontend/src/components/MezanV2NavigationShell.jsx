import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    CaretDown,
    Cube,
    House,
    List,
    MagnifyingGlass,
    Megaphone,
    Package,
    Plug,
    Queue,
    Robot,
    Storefront,
    X,
} from "@phosphor-icons/react";

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
            { to: "/orders-v2", label: "كل الطلبات", pathPrefix: "/orders-v2" },
        ],
    },
    {
        id: "fulfillment",
        label: "إدارة التجهيز",
        Icon: Queue,
        items: [
            { to: "/fulfillment-v2", label: "إدارة التجهيز", pathPrefix: "/fulfillment-v2" },
        ],
    },
    {
        id: "products",
        label: "المنتجات",
        Icon: Cube,
        items: [
            { to: "/products-v2", label: "إدارة المنتجات", exactSearch: true },
            { to: "/products-v2?workspace=intake", label: "استقبال المنتجات" },
            { to: "/inventory-receiving-v2", label: "استلام المخزون" },
            { to: "/products-v2?workspace=access", label: "الفريق والصلاحيات" },
            { to: "/components-v2", label: "مكونات المنتجات", exactSearch: true },
            { to: "/components-v2?workspace=warehouse", label: "الفروع والمخازن" },
        ],
    },
    {
        id: "marketing",
        label: "التسويق",
        Icon: Megaphone,
        items: [
            { to: "/ads-manager", label: "جميع المنصات", exactSearch: true },
            { to: "/ads-manager?provider=snapchat", label: "سناب شات" },
            { to: "/ads-manager?provider=tiktok", label: "تيك توك" },
            { to: "/ads-manager?provider=meta", label: "ميتا" },
            { to: "/ads-manager?provider=google", label: "إعلانات Google" },
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
        "inline-flex h-10 shrink-0 items-center gap-1 whitespace-nowrap rounded-xl px-2 text-[11px] font-extrabold leading-none transition sm:h-11 sm:gap-1.5 sm:px-2.5 sm:text-xs xl:h-12 xl:px-3 2xl:px-4 2xl:text-sm",
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
                title={section.label}
            >
                <Icon size={21} weight="duotone" className="shrink-0" />
                <span className="whitespace-nowrap">{section.label}</span>
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
            title={section.label}
        >
            <Icon size={21} weight="duotone" className="shrink-0" />
            <span className="whitespace-nowrap">{section.label}</span>
            <CaretDown
                size={13}
                weight="bold"
                className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
            />
        </button>
    );
}

export default function MezanV2NavigationShell({
    location,
    onOpenAll,
    searchForm = null,
    notificationControl = null,
}) {
    const [openSectionId, setOpenSectionId] = useState(null);
    const [searchOpen, setSearchOpen] = useState(false);
    const rootRef = useRef(null);
    const activeSection = useMemo(() => activeNavigationSection(location), [location]);

    useEffect(() => {
        setOpenSectionId(null);
        setSearchOpen(false);
    }, [location.pathname, location.search]);

    useEffect(() => {
        const closeOnOutsideClick = (event) => {
            if (rootRef.current && !rootRef.current.contains(event.target)) {
                setOpenSectionId(null);
                setSearchOpen(false);
            }
        };
        document.addEventListener("mousedown", closeOnOutsideClick);
        return () => document.removeEventListener("mousedown", closeOnOutsideClick);
    }, []);

    return (
        <div
            ref={rootRef}
            className="relative overflow-visible border-y border-emerald-900/70 bg-brand shadow-xl sm:rounded-2xl sm:border"
            dir="rtl"
            data-testid="mezan-v2-navigation-shell"
        >
            <div
                className="relative flex min-h-14 flex-nowrap items-center gap-1.5 overflow-visible px-2 py-2 sm:min-h-16 sm:gap-2 sm:px-3 lg:px-4"
                data-testid="mezan-v2-unified-primary-row"
            >
                <div className="flex shrink-0 items-center gap-2 border-l border-white/10 pl-2 sm:pl-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-200 text-slate-950 sm:h-11 sm:w-11">
                        <Storefront size={23} weight="duotone" />
                    </span>
                    <div className="hidden min-[1700px]:block">
                        <div className="whitespace-nowrap text-sm font-black tracking-wide text-white">MEZAN 2</div>
                        <div className="whitespace-nowrap text-[10px] font-bold text-slate-400">نظام تشغيل المتجر</div>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={onOpenAll}
                    className="inline-flex h-10 shrink-0 items-center gap-1 whitespace-nowrap rounded-xl px-2 text-[11px] font-extrabold text-slate-100 transition hover:bg-white/10 hover:text-white sm:h-11 sm:px-2.5 sm:text-xs xl:h-12 xl:px-3 2xl:px-4 2xl:text-sm"
                    data-testid="mezan-v2-open-all"
                    aria-label="فتح كل صفحات ميزان"
                >
                    <List size={23} weight="bold" className="shrink-0" />
                    <span className="hidden whitespace-nowrap sm:inline">الكل</span>
                </button>

                <div
                    className="min-w-0 flex-1 overflow-x-auto overscroll-x-contain scrollbar-thin"
                    data-testid="mezan-v2-primary-scroll"
                >
                    <div className="flex w-max min-w-full flex-nowrap items-center gap-1 whitespace-nowrap sm:gap-1.5">
                        {MEZAN_V2_NAV_SECTIONS.map((section) => {
                            const active = activeSection?.id === section.id;
                            const open = openSectionId === section.id;
                            return (
                                <div key={section.id} className="relative shrink-0">
                                    <SectionButton
                                        section={section}
                                        active={active}
                                        open={open}
                                        onToggle={() => {
                                            setSearchOpen(false);
                                            setOpenSectionId(open ? null : section.id);
                                        }}
                                        onNavigate={() => {
                                            setOpenSectionId(null);
                                            setSearchOpen(false);
                                        }}
                                    />

                                    {section.items.length > 1 && open && (
                                        <div
                                            className="absolute right-0 top-[calc(100%+0.6rem)] z-[70] max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-emerald-800 bg-[#0B4A38] p-2 shadow-2xl"
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
                                                        onClick={() => {
                                                            setOpenSectionId(null);
                                                            setSearchOpen(false);
                                                        }}
                                                        className={[
                                                            "flex items-center justify-between rounded-xl px-4 py-3 text-sm font-bold transition",
                                                            itemActive
                                                                ? "bg-emerald-200 text-slate-950"
                                                                : "text-slate-100 hover:bg-white/10",
                                                        ].join(" ")}
                                                        data-testid={`mezan-v2-dropdown-link-${section.id}`}
                                                    >
                                                        <span className="whitespace-nowrap">{item.label}</span>
                                                        {itemActive && <span className="whitespace-nowrap text-xs">الحالية</span>}
                                                    </Link>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </div>

                <div className="flex shrink-0 items-center gap-1.5">
                    <div className="relative shrink-0">
                        <button
                            type="button"
                            onClick={() => {
                                setOpenSectionId(null);
                                setSearchOpen((value) => !value);
                            }}
                            className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-slate-100 transition hover:border-emerald-300 hover:bg-white/10 hover:text-emerald-200 sm:h-11 sm:w-11"
                            aria-expanded={searchOpen}
                            aria-controls="mezan-v2-search-dropdown"
                            aria-label={searchOpen ? "إغلاق بحث الطلبات" : "فتح بحث الطلبات"}
                            data-testid="mezan-v2-search-trigger"
                        >
                            {searchOpen
                                ? <X size={21} weight="bold" />
                                : <MagnifyingGlass size={21} weight="bold" />}
                        </button>

                        {searchOpen && searchForm && (
                            <div
                                id="mezan-v2-search-dropdown"
                                className="absolute left-0 top-[calc(100%+0.65rem)] z-[80] w-[calc(100vw-1rem)] max-w-[34rem] rounded-2xl border border-slate-200 bg-white p-3 shadow-2xl sm:w-[32rem]"
                                data-testid="mezan-v2-search-dropdown"
                            >
                                {searchForm}
                            </div>
                        )}
                    </div>

                    {notificationControl && (
                        <div className="shrink-0" data-testid="mezan-v2-notification-control">
                            {notificationControl}
                        </div>
                    )}
                </div>
            </div>

            {activeSection && activeSection.items.length > 1 && (
                <nav
                    className="flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-[#0B4A38]/95 px-2 scrollbar-thin sm:px-5"
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
                                    "relative shrink-0 whitespace-nowrap px-3 py-3 text-xs font-extrabold transition sm:px-4 sm:py-4 sm:text-sm",
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

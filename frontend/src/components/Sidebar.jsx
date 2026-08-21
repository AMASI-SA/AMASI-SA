import { NavLink, useLocation } from "react-router-dom";
import {
    House, UploadSimple, Gear, ClockCounterClockwise, Receipt, SignOut,
    ChartPieSlice, ChartLineUp, Truck, Plug, Wallet, Package, Image, UserCircle,
    UsersThree, MagnifyingGlass, Queue, ArrowsLeftRight, Scales, Storefront,
    CurrencyDollar, LinkSimple, GearSix, CaretDown, X, PaperPlaneRight,
    HandCoins, Coin, Briefcase, Lightning, Cube, ChatsCircle,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { LogoIcon } from "./MezanLogo";
import SidebarVisibilityDialog from "./SidebarVisibilityDialog";
import {
    loadHiddenPages, SIDEBAR_VISIBILITY_EVENT,
} from "../lib/sidebarVisibility";


// Normalize Arabic text for search — strip tashkeel and unify variants
// of ا/أ/إ/آ + ة/ه + ى/ي so typing "سله" still matches "سلّة".
const TASHKEEL_RE = /[\u064B-\u0652\u0670\u0640]/g;
function normalizeAr(s) {
    if (!s) return "";
    return String(s)
        .toLowerCase()
        .replace(TASHKEEL_RE, "")
        .replace(/[أإآا]/g, "ا")
        .replace(/ة/g, "ه")
        .replace(/ى/g, "ي")
        .trim();
}


// ── Section definitions ───────────────────────────────────────────────
// The user requested 3 collapsible groups. Pages that previously lived
// in a flat list are kept as-is — only their grouping changes. No page
// is removed.
const SECTIONS = [
    {
        id: "customer_service",
        label: "خدمة العملاء",
        icon: ChatsCircle,
        requiredPermission: "customer_intelligence.inbox.read",
        items: [
            {
                to: "/order-tracking-notes",
                label: "تتبع الطلب وملاحظاته",
                icon: ClockCounterClockwise,
                testid: "nav-order-tracking-notes",
            },
            {
                to: "/customer-intelligence?tab=conversations",
                label: "ذكاء العملاء والمبيعات",
                icon: ChatsCircle,
                testid: "nav-mezan-os-customer-intelligence",
            },
        ],
    },
    {
        id: "finance",
        label: "العمليات المالية",
        icon: CurrencyDollar,
        items: [
            { to: "/legacy-dashboard", label: "لوحة التحكم القديمة", icon: House, testid: "nav-dashboard" },
            { to: "/orders", label: "الطلبات", icon: Package, testid: "nav-orders" },
            { to: "/accounts", label: "الأصول والحسابات", icon: Wallet, testid: "nav-accounts" },
            { to: "/new-transaction", label: "➕ حركة مالية جديدة (موحدة)", icon: PaperPlaneRight, testid: "nav-new-transaction" },
            { to: "/transactions", label: "📜 سجل الحركات المالية", icon: PaperPlaneRight, testid: "nav-ledger-transactions" },
            { to: "/employees-ledger", label: "👥 الموظفون (Ledger)", icon: PaperPlaneRight, testid: "nav-employees-ledger" },
            { to: "/employee-corrections", label: "🔄 تصحيح عملية موظف", icon: ArrowsLeftRight, testid: "nav-employee-corrections" },
            { to: "/salary-reversals", label: "↩️ عكس صرف راتب", icon: ArrowsLeftRight, testid: "nav-salary-reversals" },
            { to: "/expense-reversals", label: "↩️ عكس مصروف", icon: ArrowsLeftRight, testid: "nav-expense-reversals" },
            { to: "/employees/custody-balances", label: "🎒 أرصدة العهد المفتوحة", icon: PaperPlaneRight, testid: "nav-custody-open-balances" },
            { to: "/suppliers-ledger", label: "🏭 الموردون (Ledger)", icon: PaperPlaneRight, testid: "nav-suppliers-ledger" },
            { to: "/externals-ledger", label: "🤝 خارجيون (Ledger)", icon: PaperPlaneRight, testid: "nav-externals-ledger" },
            { to: "/couriers-ledger", label: "📦 شركات شحن (Ledger)", icon: PaperPlaneRight, testid: "nav-couriers-ledger" },
            { to: "/financial-position-ledger", label: "💰 المركز المالي (Ledger)", icon: PaperPlaneRight, testid: "nav-financial-position-ledger" },
            { to: "/accounting/reconciliation", label: "🔍 تقرير المطابقة", icon: ArrowsLeftRight, testid: "nav-reconciliation" },
            { to: "/payment-settlements", label: "فواتير وتسويات بوابات الدفع", icon: Receipt, testid: "nav-payment-settlements" },
            { to: "/bank-transfer-review", label: "🏦 مراجعة التحويلات البنكية", icon: Receipt, testid: "nav-bank-transfer-review" },
            { to: "/ai/control-center", label: "🧠 مركز الذكاء والتحقق", icon: Lightning, testid: "nav-ai-control-center" },
            { to: "/settlement-engine", label: "🔬 محرّك التسويات (Dry Run)", icon: Receipt, testid: "nav-settlement-engine" },
            { to: "/bnpl-settlements", label: "تسويات Tabby و Tamara", icon: Receipt, testid: "nav-bnpl-settlements" },
            { to: "/bnpl-settlements/register", label: "📝 تسجيل تسويات Tabby و Tamara", icon: Receipt, testid: "nav-bnpl-register" },
            { to: "/audit/employee-orphans", label: "🩺 تشخيص قيود الموظفين اليتيمة", icon: Receipt, testid: "nav-employee-orphans" },
            { to: "/audit/ad-debt", label: "📊 تشخيص فرق المديونيات الإعلانية", icon: Receipt, testid: "nav-ad-debt-diagnostic" },
            { to: "/audit/ad-account-forensic", label: "🔬 Forensic للحسابات الإعلانية", icon: Receipt, testid: "nav-ad-account-forensic" },
            { to: "/audit/balance-drift", label: "🩺 انحراف الأرصدة (Iter-250b P1.5)", icon: Receipt, testid: "nav-balance-drift" },
            { to: "/audit/ledger-health", label: "🩺 صحة الـ Ledger (Iter-240)", icon: Receipt, testid: "nav-ledger-health" },
            { to: "/settings/ads-currencies", label: "💱 عملات وعمولة بنكية للحسابات الإعلانية", icon: Gear, testid: "nav-ads-currencies" },
            { to: "/salla-settlements", label: "تسويات سلة 🟧", icon: Receipt, testid: "nav-salla-settlements" },
            { to: "/settlements-overview", label: "📑 جميع التسويات", icon: Receipt, testid: "nav-settlements-overview" },
            { to: "/bnpl-balances", label: "أرصدة BNPL (المصدر الموحَّد)", icon: Receipt, testid: "nav-bnpl-balances" },
            { to: "/diagnostics/cod-source", label: "🔍 تشخيص مصدر COD", icon: MagnifyingGlass, testid: "nav-cod-diagnostic" },
            { to: "/audit/post-migration", label: "🔬 فحص ما بعد الترحيل", icon: MagnifyingGlass, testid: "nav-post-migration-audit" },
            { to: "/diagnostics", label: "تشخيص فروقات الطلبات", icon: MagnifyingGlass, testid: "nav-diagnostics" },
            { to: "/refund-audit", label: "تدقيق المسترجعات (BNPL)", icon: MagnifyingGlass, testid: "nav-refund-audit" },
            { to: "/alerts", label: "🔔 التنبيهات الذكية", icon: Lightning, testid: "nav-alerts" },
            { to: "/accounting/migration", label: "🚀 ترحيل الأرصدة (Phase 2)", icon: ArrowsLeftRight, testid: "nav-migration" },
            { to: "/diagnostics/api-permissions", label: "🩺 فحص صلاحيات API", icon: Plug, testid: "nav-api-diagnostics" },
        ],
    },
    // Iter-246 — Dedicated section for the unified procurement /
    // expense system (Iter-244 → Iter-246).  Keeps the new entry
    // points discoverable without duplicating items in «العمليات
    // المالية».
    {
        id: "purchases_expenses",
        label: "🛒 المشتريات والمصاريف",
        icon: Briefcase,
        items: [
            { to: "/financial-movements", label: "📑 قائمة الحركات المالية", icon: Receipt, testid: "nav-financial-movements-list" },
            { to: "/suppliers-new", label: "🏷️ الموردون", icon: UsersThree, testid: "nav-suppliers-new" },
            { to: "/expense-categories-tree", label: "🗂️ شجرة التصنيفات", icon: Receipt, testid: "nav-expense-categories-tree" },
            { to: "/reports/suppliers", label: "📊 تقرير الموردين", icon: ChartPieSlice, testid: "nav-suppliers-report" },
        ],
    },
    {
        id: "procurement",
        label: "إدارة العهد والتحصيلات",
        icon: Briefcase,
        items: [
            { to: "/operations-dashboard", label: "لوحة العمليات", icon: Briefcase, testid: "nav-operations-dashboard" },
            { to: "/receivables", label: "الذمم والتحصيلات", icon: Coin, testid: "nav-receivables" },
            { to: "/ad-accounts", label: "الحسابات الإعلانية والمديونية", icon: ChartLineUp, testid: "nav-ad-accounts" },
            { to: "/reports/advertising-expenses", label: "📊 تقرير المصروفات الإعلانية", icon: ChartLineUp, testid: "nav-advertising-expenses" },
        ],
    },
    {
        id: "shipping",
        label: "🚚 شركات الشحن",
        icon: Truck,
        items: [
            { to: "/shipping/orders-ledger", label: "🚚 دفتر الشحن التفصيلي", icon: Receipt, testid: "nav-shipping-orders-ledger" },
            { to: "/shipping/transfers", label: "تحويلات شركات الشحن", icon: Coin, testid: "nav-shipping-transfers" },
            { to: "/shipping/settings", label: "إعدادات شركات الشحن", icon: GearSix, testid: "nav-shipping-settings" },
            // Future SMSA / iMile integrations land here.
        ],
    },
    {
        id: "import",
        label: "الاستيراد والربط",
        icon: LinkSimple,
        items: [
            { to: "/upload", label: "رفع ملف Excel", icon: UploadSimple, testid: "nav-upload" },
            { to: "/import-jobs", label: "حالة الاستيراد", icon: Queue, testid: "nav-import-jobs" },
            { to: "/make-webhook", label: "ربط Make.com", icon: Plug, testid: "nav-make-webhook" },
            { to: "/integrations/custom-app", label: "ربط تطبيقي الخاص", icon: Plug, testid: "nav-custom-app" },
            { to: "/integrations/bnpl", label: "ربط تمارا وتابي", icon: Lightning, testid: "nav-bnpl-integrations" },
            { to: "/history", label: "سجل التحليلات", icon: ClockCounterClockwise, testid: "nav-history" },
        ],
    },
    // ── Integrations Hub ─────────────────────────────────────────────
    // Dedicated, top-level section per Pre-Day 3 user request.
    // Sub-grouped per upstream platform (Salla, Qoyod, …) so the
    // operator knows where to look without scanning a flat list.
    // Pages not yet built render the IntegrationPlaceholder stub.
    {
        id: "integrations",
        label: "التكاملات (Integrations)",
        icon: Plug,
        subgroups: [
            {
                id: "salla",
                label: "سلة",
                items: [
                    { to: "/settings/salla", label: "إعدادات سلة", icon: Storefront, testid: "nav-salla-settings" },
                    { to: "/make-webhook", label: "Webhooks", icon: Plug, testid: "nav-salla-webhooks" },
                    { to: "/integrations/salla/orders", label: "مراقبة الطلبات", icon: Package, testid: "nav-salla-orders" },
                    { to: "/integrations/salla/events", label: "سجل الأحداث", icon: ClockCounterClockwise, testid: "nav-salla-events" },
                    { to: "/salla-sources", label: "مقارنة مصادر البيانات", icon: ChartPieSlice, testid: "nav-salla-sources" },
                ],
            },
        ],
    },
    {
        id: "mezan_os",
        label: "Mezan OS",
        icon: Lightning,
        ownerOnly: true,
        items: [
            {
                to: "/dashboard-v2",
                label: "لوحة التحكم",
                icon: House,
                testid: "nav-mezan-os-dashboard",
            },
            {
                to: "/orders-v2",
                label: "الطلبات",
                icon: Package,
                testid: "nav-mezan-os-orders",
            },
            {
                to: "/fulfillment-v2",
                label: "إدارة التجهيز",
                icon: Queue,
                testid: "nav-mezan-os-fulfillment",
            },
            {
                to: "/products-v2",
                label: "المنتجات",
                icon: Package,
                testid: "nav-mezan-os-products",
            },
            {
                to: "/components-v2",
                label: "المكونات",
                icon: Cube,
                testid: "nav-mezan-os-components",
            },
            {
                to: "/integrations-v2",
                label: "التطبيقات والتكاملات",
                icon: Plug,
                testid: "nav-mezan-os-integrations",
            },
            {
                to: "/integrations-v2/qoyod",
                label: "قيود — التشغيل التلقائي",
                icon: Receipt,
                testid: "nav-mezan-os-qoyod",
            },
            {
                to: "/ads-manager",
                label: "مدير الإعلانات",
                icon: ChartLineUp,
                testid: "nav-mezan-os-ads-manager",
            },
        ],
    },
    {
        id: "operations",
        label: "إدارة التشغيل",
        icon: GearSix,
        items: [
            { to: "/order-review", label: "بانتظار المراجعة", icon: MagnifyingGlass, testid: "nav-order-review" },
            { to: "/operational-reports", label: "التقارير التشغيلية", icon: Wallet, testid: "nav-operational-reports" },
            { to: "/reports", label: "التقارير", icon: ChartPieSlice, testid: "nav-reports" },
            { to: "/ads-v2/settings", label: "📐 إعدادات الإعلانات V2", icon: Gear, testid: "nav-ads-v2-settings" },
            { to: "/ads-v2/report", label: "📊 تقرير الإعلانات V2", icon: ChartPieSlice, testid: "nav-ads-v2-report" },
            { to: "/products", label: "المنتجات", icon: Package, testid: "nav-products" },
            { to: "/product-costs", label: "تكاليف المنتجات", icon: Package, testid: "nav-product-costs" },
            { to: "/product-preparation", label: "تجهيز المنتجات", icon: Package, testid: "nav-product-preparation" },
            { to: "/image-catalog", label: "إدارة صور المنتجات", icon: Image, testid: "nav-image-catalog" },
            { to: "/profile", label: "حسابي", icon: UserCircle, testid: "nav-profile" },
            { to: "/settings/accounting-cutoffs", label: "تواريخ بدء المحاسبة", icon: Gear, testid: "nav-accounting-cutoffs" },
            { to: "/settings/operation-account-bindings", label: "🔗 ربط العمليات بالحسابات", icon: Gear, testid: "nav-op-account-bindings" },
            // Owner-only: pushed in at render time.
            { to: "/settings", label: "الإعدادات", icon: Gear, testid: "nav-settings" },
        ],
    },
    // Iter-246 — Legacy section.  The merchant explicitly asked us to
    // KEEP these screens working (for read access + historical data)
    // even after their replacements landed in «المشتريات والمصاريف».
    // They are grouped here with a 🕰️ badge so the new system is the
    // obvious default.
    {
        id: "legacy",
        label: "🕰️ الأنظمة القديمة",
        icon: ClockCounterClockwise,
        items: [
            { to: "/legacy-usage-report", label: "📊 تقرير الاستخدام", icon: ChartPieSlice, testid: "nav-legacy-usage-report" },
            { to: "/purchase-invoices", label: "فواتير المشتريات 🕰️ Legacy", icon: Receipt, testid: "nav-purchase-invoices" },
            { to: "/operating-expenses", label: "المصروفات التشغيلية 🕰️ Legacy", icon: Wallet, testid: "nav-operating-expenses" },
            { to: "/daily-costs", label: "التكاليف اليومية 🕰️ Legacy", icon: Receipt, testid: "nav-daily-costs" },
        ],
    },
];


const OPEN_SECTION_STORAGE_KEY = "hesab.sidebar.openSection";

export function sidebarSectionsForUser(user) {
    const visibleSections = SECTIONS.filter(
        (section) => (
            (!section.ownerOnly || user?.is_owner)
            && (!section.requiredPermission
                || user?.is_owner
                || user?.permissions?.includes(section.requiredPermission))
        ),
    );

    if (!user?.is_owner) return visibleSections;

    return visibleSections.map((section) => {
        if (section.id !== "operations") return section;

        const items = section.items.filter((item) => item.to !== "/order-review");
        const settingsIdx = items.findIndex((item) => item.to === "/settings");
        const teamLink = {
            to: "/team",
            label: "إدارة الفريق",
            icon: UsersThree,
            testid: "nav-team",
        };
        items.splice(settingsIdx >= 0 ? settingsIdx : items.length, 0, teamLink);
        return { ...section, items };
    });
}


// Flatten any section into a flat list of items, walking subgroups when
// present. Used by path-matching, search filtering, and the visibility
// dialog so a single function is the source of truth.
function flattenSectionItems(section) {
    if (Array.isArray(section.items) && section.items.length) {
        return section.items;
    }
    if (Array.isArray(section.subgroups)) {
        return section.subgroups.flatMap((g) => g.items || []);
    }
    return [];
}


function findSectionFor(pathname) {
    for (const sec of SECTIONS) {
        for (const item of flattenSectionItems(sec)) {
            const itemPath = String(item.to || "").split("?")[0];
            if (itemPath === "/" ? pathname === "/" : pathname.startsWith(itemPath)) {
                return sec.id;
            }
        }
    }
    return null;
}


export default function Sidebar({ mobileOpen = false, onMobileClose = () => {} }) {
    const { user, logout } = useAuth();
    const location = useLocation();

    // Owner-only workspaces are hidden completely from non-owner users.
    // The team-management link remains inside إدارة التشغيل.
    const sections = useMemo(
        () => sidebarSectionsForUser(user),
        [user],
    );

    // ── Iter-124 → Iter-141 — User-hidden pages (now cross-device) ──
    const [hiddenSet, setHiddenSet] = useState(() => loadHiddenPages());
    const [visibilityDialogOpen, setVisibilityDialogOpen] = useState(false);
    useEffect(() => {
        const handler = (e) => setHiddenSet(new Set(e.detail?.hidden || []));
        window.addEventListener(SIDEBAR_VISIBILITY_EVENT, handler);
        return () => window.removeEventListener(SIDEBAR_VISIBILITY_EVENT, handler);
    }, []);

    // Iter-141 — pull the canonical list from the server on first
    // mount so a sidebar layout hidden on one device shows up
    // immediately on every other device the merchant uses.
    useEffect(() => {
        let mounted = true;
        import("../lib/sidebarVisibility").then(({ refreshHiddenPagesFromServer }) => {
            refreshHiddenPagesFromServer().then((s) => {
                if (mounted) setHiddenSet(s);
            });
        });
        return () => { mounted = false; };
    }, []);

    // ── Open-section state ──────────────────────────────────────────
    // We derive the open section from (in order):
    //   1. User's explicit toggle for the current path (last clicked)
    //   2. The section that contains the current path
    //   3. The last persisted choice in localStorage
    //   4. Fallback: "finance"
    // A `pathKey` is bumped on every navigation so user overrides only
    // apply until the next navigation (matches the user's spec: "عند
    // الانتقال بين الصفحات يبقى القسم مفتوحاً").
    const [override, setOverride] = useState(null); // { id, path }

    const autoSection = findSectionFor(location.pathname);
    const storedSection = (() => {
        try { return localStorage.getItem(OPEN_SECTION_STORAGE_KEY); }
        catch { return null; }
    })();
    const openId =
        (override && override.path === location.pathname ? override.id : null)
        ?? autoSection
        ?? storedSection
        ?? "finance";

    const handleToggle = (id) => {
        const next = openId === id ? null : id;
        setOverride({ id: next, path: location.pathname });
        try {
            if (next) localStorage.setItem(OPEN_SECTION_STORAGE_KEY, next);
            else localStorage.removeItem(OPEN_SECTION_STORAGE_KEY);
        } catch { /* private mode etc. */ }
    };

    // ── Search filter ───────────────────────────────────────────────
    // When a query is present, we (a) filter items by normalized
    // Arabic match, (b) force all sections open so the merchant sees
    // every result, and (c) hide section headers whose items all got
    // filtered out.
    const [search, setSearch] = useState("");
    const searchActive = search.trim().length > 0;
    const normalizedQuery = normalizeAr(search);

    const filteredSections = useMemo(() => {
        // Helper: filter a single bag of items by hidden + (optional) search.
        const filterItems = (items) => {
            let out = items.filter((it) => !hiddenSet.has(it.testid));
            if (searchActive) {
                out = out.filter((it) => normalizeAr(it.label).includes(normalizedQuery));
            }
            return out;
        };
        return sections
            .map((s) => {
                if (Array.isArray(s.subgroups)) {
                    // Subgroup-aware filtering. Drop empty subgroups and
                    // drop the whole section if every subgroup is empty.
                    const subgroups = s.subgroups
                        .map((g) => ({ ...g, items: filterItems(g.items || []) }))
                        .filter((g) => g.items.length > 0);
                    return { ...s, subgroups };
                }
                return { ...s, items: filterItems(s.items || []) };
            })
            .filter((s) => {
                if (Array.isArray(s.subgroups)) return s.subgroups.length > 0;
                return (s.items || []).length > 0;
            });
    }, [sections, hiddenSet, searchActive, normalizedQuery]);

    const totalMatches = useMemo(
        () => filteredSections.reduce(
            (n, s) => n + (Array.isArray(s.subgroups)
                ? s.subgroups.reduce((m, g) => m + g.items.length, 0)
                : (s.items || []).length),
            0,
        ),
        [filteredSections],
    );

    const onLogout = async () => { await logout(); };

    return (
        <>
            {/* Mobile backdrop overlay */}
            {mobileOpen && (
                <div
                    className="fixed inset-0 bg-black/50 z-40 lg:hidden"
                    onClick={onMobileClose}
                    data-testid="sidebar-backdrop"
                    aria-hidden="true"
                />
            )}

            <aside
                className={[
                    "fixed top-0 right-0 h-screen w-64 bg-white border-l border-border flex flex-col z-50",
                    "transition-transform duration-300 ease-out",
                    "lg:translate-x-0",
                    mobileOpen ? "translate-x-0" : "translate-x-full lg:translate-x-0",
                ].join(" ")}
                data-testid="sidebar"
                aria-label="القائمة الجانبية"
            >
                {/* Brand + Mobile close */}
                <div className="px-6 py-5 border-b border-border flex items-center justify-between">
                    <div className="flex items-center gap-3" data-testid="sidebar-brand">
                        <LogoIcon size={42} />
                        <div>
                            <div className="text-brand text-xl font-extrabold tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }} data-testid="sidebar-brand-en">
                                <span>MEZ</span><span className="text-accent-green">AN</span>
                            </div>
                            <div className="text-xs text-muted-foreground font-bold" data-testid="sidebar-brand-ar">ميزان · تحليلات التجارة</div>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onMobileClose}
                        className="lg:hidden p-1.5 rounded-md hover:bg-accent text-muted-foreground"
                        data-testid="sidebar-close-btn"
                        aria-label="إغلاق القائمة"
                    >
                        <X size={22} weight="bold" />
                    </button>
                </div>

                {/* Search bar (Iter-80) */}
                <div className="px-3 pt-3 pb-1" data-testid="sidebar-search-wrapper">
                    <div className="relative">
                        <MagnifyingGlass
                            size={15}
                            weight="bold"
                            className="absolute top-1/2 -translate-y-1/2 right-3 text-muted-foreground"
                        />
                        <input
                            type="text"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="ابحث في القائمة…"
                            className="w-full ps-3 pe-9 py-2 text-[13px] rounded-lg border border-border bg-slate-50 focus:bg-white focus:border-brand outline-none transition-colors"
                            data-testid="sidebar-search-input"
                            aria-label="ابحث في القائمة"
                        />                        {searchActive && (
                            <button
                                type="button"
                                onClick={() => setSearch("")}
                                className="absolute top-1/2 -translate-y-1/2 left-2 p-0.5 rounded hover:bg-accent text-muted-foreground"
                                data-testid="sidebar-search-clear"
                                aria-label="مسح البحث"
                            >
                                <X size={13} weight="bold" />
                            </button>
                        )}
                    </div>
                    {searchActive && (
                        <p className="text-[10px] text-muted-foreground mt-1.5 px-1" data-testid="sidebar-search-count">
                            {totalMatches > 0 ? `${totalMatches} نتيجة` : "لا توجد نتائج"}
                        </p>
                    )}
                </div>

                {/* Iter-124 — Sidebar visibility toggle */}
                <div className="px-3 pb-2">
                    <button
                        type="button"
                        onClick={() => setVisibilityDialogOpen(true)}
                        className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-[11px] font-bold rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 transition"
                        data-testid="sidebar-visibility-btn"
                        title="اختر الصفحات المرئية في القائمة"
                    >
                        <GearSix size={13} weight="bold" />
                        إعدادات إظهار الصفحات
                        {hiddenSet.size > 0 && (
                            <span className="bg-rose-500 text-white px-1.5 py-0.5 rounded-full text-[9px] num" data-testid="sidebar-hidden-count">
                                {hiddenSet.size}
                            </span>
                        )}
                    </button>
                </div>

                {/* Accordion nav */}
                <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-1 scrollbar-thin" data-testid="sidebar-nav">
                    {filteredSections.map((section) => {
                        const SectionIcon = section.icon;
                        // When searching, force every section open so all
                        // matches are visible without clicking.
                        const isOpen = searchActive || openId === section.id;
                        const sectionItems = Array.isArray(section.subgroups)
                            ? section.subgroups.flatMap((g) => g.items)
                            : (section.items || []);
                        const containsActive = sectionItems.some(
                            (i) => {
                                const itemPath = String(i.to || "").split("?")[0];
                                return itemPath === "/"
                                    ? location.pathname === "/"
                                    : location.pathname.startsWith(itemPath);
                            },
                        );
                        const renderItem = ({ to, label, icon: Icon, testid }) => {
                            return (
                            <NavLink
                                key={to}
                                to={to}
                                end={to === "/"}
                                onClick={onMobileClose}
                                data-testid={testid}
                                className={({ isActive }) =>
                                    [
                                        "flex items-center gap-2.5 ps-4 pe-3 py-2 rounded-lg text-[13.5px] transition-colors",
                                        isActive
                                            ? "bg-brand text-white font-semibold"
                                            : "text-foreground hover:bg-accent hover:text-brand",
                                    ].join(" ")
                                }
                            >
                                <Icon size={17} weight="duotone" />
                                <span className="truncate flex-1">{label}</span>
                            </NavLink>
                            );
                        };
                        return (
                            <div key={section.id} className="select-none" data-testid={`sidebar-section-${section.id}`}>
                                <button
                                    type="button"
                                    onClick={() => !searchActive && handleToggle(section.id)}
                                    disabled={searchActive}
                                    className={[
                                        "w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg text-[14px] font-bold transition-colors",
                                        searchActive ? "cursor-default" : "",
                                        isOpen
                                            ? "bg-brand/10 text-brand"
                                            : containsActive
                                                ? "text-brand hover:bg-accent"
                                                : "text-foreground hover:bg-accent hover:text-brand",
                                    ].join(" ")}
                                    aria-expanded={isOpen}
                                    aria-controls={`sidebar-panel-${section.id}`}
                                    data-testid={`sidebar-section-toggle-${section.id}`}
                                >
                                    <span className="flex items-center gap-2">
                                        <SectionIcon size={20} weight="duotone" />
                                        <span>{section.label}</span>
                                    </span>
                                    <CaretDown
                                        size={14}
                                        weight="bold"
                                        className={`transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
                                    />
                                </button>

                                <div
                                    id={`sidebar-panel-${section.id}`}
                                    role="region"
                                    className={`overflow-hidden transition-[max-height] duration-300 ease-out ${
                                        isOpen ? "max-h-[1200px]" : "max-h-0"
                                    }`}
                                >
                                    <div className="ps-2 pt-1 pb-2 space-y-0.5">
                                        {Array.isArray(section.subgroups) ? (
                                            section.subgroups.map((g) => (
                                                <div key={g.id} className="mb-2" data-testid={`sidebar-subgroup-${g.id}`}>
                                                    <div className="px-3 pt-2 pb-1 text-[11px] font-extrabold uppercase tracking-wider text-slate-500 border-b border-slate-100 mx-2">
                                                        {g.label}
                                                    </div>
                                                    <div className="space-y-0.5 mt-1">
                                                        {g.items.map(renderItem)}
                                                    </div>
                                                </div>
                                            ))
                                        ) : (
                                            (section.items || []).map(renderItem)
                                        )}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </nav>

                {/* User block */}
                <div className="border-t border-border p-4">
                    <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 rounded-full bg-accent flex items-center justify-center text-brand font-bold">
                            {(user?.name || user?.email || "ض").slice(0, 1).toUpperCase()}
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="text-sm font-semibold truncate">{user?.name || "مستخدم"}</div>
                            <div className="text-xs text-muted-foreground truncate">{user?.email}</div>
                        </div>
                    </div>
                    <button
                        onClick={onLogout}
                        data-testid="logout-btn"
                        className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-border text-sm font-medium hover:bg-accent transition-colors"
                    >
                        <SignOut size={18} />
                        تسجيل الخروج
                    </button>
                </div>
            </aside>

            {/* Iter-124 — Visibility dialog (mounted regardless of mobile/desktop) */}
            <SidebarVisibilityDialog
                open={visibilityDialogOpen}
                onClose={() => setVisibilityDialogOpen(false)}
                sections={sections}
            />
        </>
    );
}

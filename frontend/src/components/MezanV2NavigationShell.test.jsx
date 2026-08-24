import { renderToStaticMarkup } from "react-dom/server";

jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => (
        <a href={to} {...props}>{children}</a>
    ),
}));

jest.mock("../context/AuthContext", () => ({
    useOptionalAuth: () => ({
        user: { id: "owner-1", role: "owner", is_owner: true },
    }),
}));

import MezanV2NavigationShell, {
    MEZAN_V2_NAV_SECTIONS,
    activeNavigationSection,
    isMezanV2Route,
    isNavigationItemActive,
    navigationSectionsForAccountingAccess,
    navigationSectionsForDisplay,
} from "./MezanV2NavigationShell";

const DASHBOARD_LOCATION = {
    pathname: "/dashboard-advanced",
    search: "",
};

const PRODUCTS_LOCATION = {
    pathname: "/products-v2",
    search: "?workspace=intake",
};

const MARKETING_LOCATION = {
    pathname: "/ads-manager",
    search: "?provider=meta",
};

const FULFILLMENT_LOCATION = {
    pathname: "/fulfillment-v2",
    search: "?stage=reviewed&view=products",
};

const ACCOUNTING_LOCATION = {
    pathname: "/integrations-v2",
    search: "?workspace=financial&page=shipping-cod",
};

test("Mezan 2 shell is limited to Mezan 2 routes including the advanced dashboard", () => {
    [
        "/dashboard-advanced",
        "/dashboard-v2",
        "/orders-v2/280001234",
        "/fulfillment-v2",
        "/inventory-receiving-v2",
        "/recurring-obligations",
        "/products-v2",
        "/components-v2",
        "/suppliers-v2",
        "/integrations-v2",
        "/assistant",
        "/customer-intelligence",
        "/ads-manager",
        "/ads-manager/cost-settings",
        "/snapchat-accounts",
    ].forEach((pathname) => expect(isMezanV2Route(pathname)).toBe(true));

    [
        "/",
        "/orders",
        "/reports",
        "/transactions",
        "/settings",
    ].forEach((pathname) => expect(isMezanV2Route(pathname)).toBe(false));
});

test("advanced dashboard is the Mezan 2 home target and renders the unified header", () => {
    const home = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "home");
    expect(home.items).toEqual([
        { to: "/dashboard-advanced", label: "الرئيسية", exactSearch: true },
    ]);
    expect(activeNavigationSection(DASHBOARD_LOCATION)?.id).toBe("home");
    expect(activeNavigationSection({ pathname: "/dashboard-v2", search: "" })?.id).toBe("home");

    const markup = renderToStaticMarkup(
        <MezanV2NavigationShell
            location={DASHBOARD_LOCATION}
            onOpenAll={() => {}}
        />,
    );
    expect(markup).toContain('data-testid="mezan-v2-navigation-shell"');
    expect(markup).toContain('data-testid="mezan-v2-primary-home"');
    expect(markup).toContain('href="/dashboard-advanced"');
});

test("orders and fulfillment are independent top-level sections", () => {
    const orders = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "orders");
    const fulfillment = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "fulfillment");

    expect(orders.items.map((item) => item.to)).toEqual(["/orders-v2"]);
    expect(fulfillment.items.map((item) => item.to)).toEqual([
        "/fulfillment-v2",
        "/fulfillment-v2?workspace=my-products",
        "/fulfillment-v2?stage=reviewed&view=products",
        "/fulfillment-v2?stage=reviewed&view=files",
        "/fulfillment-v2?stage=assembly",
        "/fulfillment-v2?stage=ready_to_ship",
        "/fulfillment-v2?workspace=store-driver-handover",
    ]);
    expect(orders.items.some((item) => item.to.startsWith("/fulfillment-v2"))).toBe(false);
    expect(orders.items.some((item) => item.to === "/inventory-receiving-v2")).toBe(false);

    const active = activeNavigationSection(FULFILLMENT_LOCATION);
    expect(active?.id).toBe("fulfillment");

    const markup = renderToStaticMarkup(
        <MezanV2NavigationShell
            location={FULFILLMENT_LOCATION}
            onOpenAll={() => {}}
        />,
    );
    expect(markup).toContain('data-testid="mezan-v2-primary-orders"');
    expect(markup).toContain('data-testid="mezan-v2-primary-fulfillment"');
    expect(markup).toContain('data-testid="mezan-v2-secondary-fulfillment"');
    expect(markup).toContain("تم المراجعة");
    expect(markup).toContain("سجل ملفات التجهيز");
    expect(markup).toContain("تسليم الشحنات للموصلين");
});

test("products section exposes a Salla-style primary group and secondary page rail", () => {
    const markup = renderToStaticMarkup(
        <MezanV2NavigationShell
            location={PRODUCTS_LOCATION}
            onOpenAll={() => {}}
        />,
    );

    expect(markup).toContain('data-testid="mezan-v2-navigation-shell"');
    expect(markup).toContain('data-testid="mezan-v2-primary-products"');
    expect(markup).toContain('data-testid="mezan-v2-secondary-products"');
    expect(markup).toContain("إدارة المنتجات");
    expect(markup).toContain("استقبال المنتجات");
    expect(markup).toContain("استلام المخزون");
    expect(markup).not.toContain("الفريق والصلاحيات");
    expect(markup).toContain("مكونات المنتجات");
    expect(markup).toContain("الفروع والمخازن");
    expect(markup).toContain('data-testid="mezan-v2-open-all"');
});

test("query-specific product page is the active child", () => {
    const section = activeNavigationSection(PRODUCTS_LOCATION);
    expect(section?.id).toBe("products");

    const activeItems = section.items.filter(
        (item) => isNavigationItemActive(PRODUCTS_LOCATION, item),
    );
    expect(activeItems.map((item) => item.label)).toEqual(["استقبال المنتجات"]);
});

test("inventory receiving belongs to products rather than orders", () => {
    const section = activeNavigationSection({
        pathname: "/inventory-receiving-v2",
        search: "",
    });
    expect(section?.id).toBe("products");
});

test("AI section exposes the conversational Mezan assistant and delivery customer-service workspace", () => {
    const section = MEZAN_V2_NAV_SECTIONS.find(
        (item) => item.id === "intelligence",
    );

    expect(section.items.map((item) => item.to)).toEqual([
        "/assistant",
        "/customer-intelligence",
        "/customer-intelligence?workspace=store-delivery",
    ]);
    expect(isMezanV2Route("/assistant")).toBe(true);
    expect(activeNavigationSection({
        pathname: "/assistant",
        search: "",
    })?.id).toBe("intelligence");
});

test("Mezan 2 exposes an independent suppliers section", () => {
    const suppliers = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "suppliers");
    expect(suppliers.label).toBe("الموردون والفواتير");
    expect(suppliers.items).toEqual([
        { to: "/suppliers-v2", label: "الموردون والفواتير", exactSearch: true },
    ]);
    expect(activeNavigationSection({ pathname: "/suppliers-v2", search: "" })?.id).toBe("suppliers");
});

test("accounting owns exactly the approved eight pages and is removed from apps", () => {
    const accounting = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "accounting");
    expect(accounting.label).toBe("المحاسبة");
    expect(accounting.items.map((item) => item.label)).toEqual([
        "الرئيسية المحاسبية",
        "التسويات",
        "الشحن والتحصيل",
        "المخزون والمشتريات",
        "الحركات المالية",
        "الرواتب والالتزامات",
        "الأرصدة الافتتاحية",
        "القيود والتقارير",
    ]);
    expect(accounting.items.every((item) => item.to.includes("workspace=financial"))).toBe(true);
    expect(activeNavigationSection(ACCOUNTING_LOCATION)?.id).toBe("accounting");

    const activeItems = accounting.items.filter(
        (item) => isNavigationItemActive(ACCOUNTING_LOCATION, item),
    );
    expect(activeItems.map((item) => item.label)).toEqual(["الشحن والتحصيل"]);

    const apps = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "apps");
    expect(apps.items.some((item) => item.to.includes("workspace=financial"))).toBe(false);
});

test("employees see only explicitly assigned accounting pages", () => {
    const none = navigationSectionsForAccountingAccess({
        is_owner: false,
        permissions: [],
    });
    expect(none.some((section) => section.id === "accounting")).toBe(false);

    const assigned = navigationSectionsForAccountingAccess({
        is_owner: false,
        permissions: ["accounting.settlements.view", "accounting.payroll.view"],
    });
    const accounting = assigned.find((section) => section.id === "accounting");
    expect(accounting.items.map((item) => item.label)).toEqual([
        "التسويات",
        "الرواتب والالتزامات",
    ]);
});

test("marketing report and cost routes are separate from app integration routes", () => {
    const section = activeNavigationSection(MARKETING_LOCATION);
    expect(section?.id).toBe("marketing");
    expect(section.items.map((item) => item.to)).toEqual([
        "/ads-manager",
        "/ads-manager/recommendations",
        "/snapchat-accounts",
        "/ads-manager?provider=tiktok",
        "/ads-manager?provider=meta",
        "/ads-manager?provider=google",
        "/ads-manager/cost-settings",
    ]);
    expect(section.items.some((item) => item.to.startsWith("/integrations-v2"))).toBe(false);

    const activeItems = section.items.filter(
        (item) => isNavigationItemActive(MARKETING_LOCATION, item),
    );
    expect(activeItems.map((item) => item.label)).toEqual(["ميتا"]);
});

test("opening marketing selects a visible secondary rail outside the primary scroller", () => {
    const state = navigationSectionsForDisplay(DASHBOARD_LOCATION, "marketing");
    expect(state.activeSection?.id).toBe("home");
    expect(state.openSection?.id).toBe("marketing");
    expect(state.visibleSection?.id).toBe("marketing");
    expect(state.visibleSection.items.map((item) => item.label)).toEqual([
        "جميع المنصات",
        "توصيات الحملات",
        "سناب شات",
        "تيك توك",
        "ميتا",
        "إعلانات Google",
        "العمولات وسعر الصرف",
    ]);
});

test("new navigation does not route to legacy Mezan pages", () => {
    const targets = MEZAN_V2_NAV_SECTIONS.flatMap((section) => (
        section.items.map((item) => item.to.split("?")[0])
    ));

    expect(targets).not.toContain("/");
    expect(targets).not.toContain("/dashboard-v2");
    expect(targets).not.toContain("/orders");
    expect(targets).not.toContain("/reports");
    expect(targets).not.toContain("/transactions");
    expect(targets).not.toContain("/settings");
});

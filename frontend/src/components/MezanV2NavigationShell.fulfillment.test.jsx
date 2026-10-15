jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import {
    activeNavigationSection,
    isNavigationItemActive,
    MEZAN_V2_NAV_SECTIONS,
} from "./MezanV2NavigationShell";
import {
    removeReviewedProductsSecondaryTab,
    REVIEWED_PRODUCTS_SECONDARY_TARGET,
} from "../reviewHideReviewedSecondaryTab";

const fulfillment = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "fulfillment");

test("fulfillment routes keep reviewed products and driver handover available internally", () => {
    expect(fulfillment.items).toEqual([
        { to: "/fulfillment-v2", label: "إدارة التجهيز", exactSearch: true },
        { to: "/fulfillment-v2?workspace=my-products", label: "إدارة منتجاتي" },
        { to: "/fulfillment-v2?stage=reviewed&view=products", label: "تم المراجعة" },
        { to: "/fulfillment-v2?stage=reviewed&view=files", label: "سجل ملفات التجهيز" },
        { to: "/fulfillment-v2?stage=assembly", label: "الاستلام من التجهيز" },
        { to: "/fulfillment-v2?stage=ready_to_ship", label: "التجميع والعنونة" },
        { to: "/fulfillment-v2?workspace=store-driver-handover", label: "تسليم الشحنات للموصلين" },
    ]);
});

test("duplicate reviewed products entry is removed from the upper submenu", () => {
    document.body.innerHTML = `
        <nav data-testid="mezan-v2-secondary-fulfillment">
            <a href="/fulfillment-v2">إدارة التجهيز</a>
            <a href="/fulfillment-v2?workspace=my-products">إدارة منتجاتي</a>
            <a href="${REVIEWED_PRODUCTS_SECONDARY_TARGET}">تم المراجعة</a>
            <a href="/fulfillment-v2?stage=reviewed&view=files">سجل ملفات التجهيز</a>
            <a href="/fulfillment-v2?stage=assembly">الاستلام من التجهيز</a>
            <a href="/fulfillment-v2?stage=ready_to_ship">التجميع والعنونة</a>
            <a href="/fulfillment-v2?workspace=store-driver-handover">تسليم الشحنات للموصلين</a>
        </nav>
    `;

    expect(removeReviewedProductsSecondaryTab(document)).toBe(1);
    expect(Array.from(document.querySelectorAll("nav a")).map((link) => link.textContent)).toEqual([
        "إدارة التجهيز",
        "إدارة منتجاتي",
        "سجل ملفات التجهيز",
        "الاستلام من التجهيز",
        "التجميع والعنونة",
        "تسليم الشحنات للموصلين",
    ]);
});

test("only the reviewed products navigation item is active in products view", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?stage=reviewed&view=products",
    };

    expect(isNavigationItemActive(location, fulfillment.items[0])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[1])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[2])).toBe(true);
    expect(isNavigationItemActive(location, fulfillment.items[3])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[4])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[5])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[6])).toBe(false);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

test("only the file registry navigation item is active in files view", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?stage=reviewed&view=files",
    };

    expect(isNavigationItemActive(location, fulfillment.items[0])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[1])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[2])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[3])).toBe(true);
    expect(isNavigationItemActive(location, fulfillment.items[4])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[5])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[6])).toBe(false);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

test("my products is an independent navigation item beside fulfillment", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?workspace=my-products",
    };

    expect(isNavigationItemActive(location, fulfillment.items[0])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[1])).toBe(true);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

test("driver handover is an independent fulfillment navigation item", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?workspace=store-driver-handover",
    };

    expect(isNavigationItemActive(location, fulfillment.items[0])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[6])).toBe(true);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

test("fulfillment parent remains active on other governed stages", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?stage=preparation",
    };

    expect(fulfillment.items.some((item) => isNavigationItemActive(location, item))).toBe(false);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

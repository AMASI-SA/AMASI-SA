import {
    activeNavigationSection,
    isNavigationItemActive,
    MEZAN_V2_NAV_SECTIONS,
} from "./MezanV2NavigationShell";

const fulfillment = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "fulfillment");

test("fulfillment navigation exposes reviewed products and file registry as separate windows", () => {
    expect(fulfillment.items).toEqual([
        { to: "/fulfillment-v2", label: "إدارة التجهيز", exactSearch: true },
        { to: "/fulfillment-v2?stage=reviewed&view=products", label: "تم المراجعة" },
        { to: "/fulfillment-v2?stage=reviewed&view=files", label: "سجل ملفات التجهيز" },
    ]);
});

test("only the reviewed products navigation item is active in products view", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?stage=reviewed&view=products",
    };

    expect(isNavigationItemActive(location, fulfillment.items[0])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[1])).toBe(true);
    expect(isNavigationItemActive(location, fulfillment.items[2])).toBe(false);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

test("only the file registry navigation item is active in files view", () => {
    const location = {
        pathname: "/fulfillment-v2",
        search: "?stage=reviewed&view=files",
    };

    expect(isNavigationItemActive(location, fulfillment.items[0])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[1])).toBe(false);
    expect(isNavigationItemActive(location, fulfillment.items[2])).toBe(true);
    expect(activeNavigationSection(location)?.id).toBe("fulfillment");
});

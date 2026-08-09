jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import {
    activeNavigationSection,
    isNavigationItemActive,
    MEZAN_V2_NAV_SECTIONS,
} from "./MezanV2NavigationShell";

const employees = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "employees");
const products = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "products");

test("employees own salary and fulfillment permissions in Mezan V2 navigation", () => {
    expect(employees.items).toEqual([
        { to: "/employees-v2", label: "الموظفون والرواتب", exactSearch: true },
        { to: "/employees-v2?workspace=permissions", label: "الصلاحيات وإدارة التجهيز" },
    ]);
    expect(products.items.some((item) => item.to === "/products-v2?workspace=access")).toBe(false);
});

test("employee overview and permissions have distinct active navigation items", () => {
    const overview = { pathname: "/employees-v2", search: "" };
    const permissions = { pathname: "/employees-v2", search: "?workspace=permissions" };

    expect(isNavigationItemActive(overview, employees.items[0])).toBe(true);
    expect(isNavigationItemActive(overview, employees.items[1])).toBe(false);
    expect(isNavigationItemActive(permissions, employees.items[0])).toBe(false);
    expect(isNavigationItemActive(permissions, employees.items[1])).toBe(true);
    expect(activeNavigationSection(permissions)?.id).toBe("employees");
});

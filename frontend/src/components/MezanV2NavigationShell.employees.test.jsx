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

test("employees own management, store drivers, salary migration, and fulfillment permissions in Mezan V2 navigation", () => {
    expect(employees.items).toEqual([
        { to: "/employees-v2", label: "إدارة الموظفين", exactSearch: true },
        { to: "/employees-v2?workspace=drivers", label: "موصلو المتجر" },
        { to: "/employees-v2?workspace=migration", label: "تقرير الترحيل والرواتب" },
        { to: "/employees-v2?workspace=permissions", label: "الصلاحيات وإدارة التجهيز" },
    ]);
    expect(products.items.some((item) => item.to === "/products-v2?workspace=access")).toBe(false);
});

test("employee overview, drivers, and permissions have distinct active navigation items", () => {
    const overview = { pathname: "/employees-v2", search: "" };
    const drivers = { pathname: "/employees-v2", search: "?workspace=drivers" };
    const permissions = { pathname: "/employees-v2", search: "?workspace=permissions" };

    expect(isNavigationItemActive(overview, employees.items[0])).toBe(true);
    expect(isNavigationItemActive(overview, employees.items[1])).toBe(false);
    expect(isNavigationItemActive(overview, employees.items[2])).toBe(false);
    expect(isNavigationItemActive(overview, employees.items[3])).toBe(false);

    expect(isNavigationItemActive(drivers, employees.items[0])).toBe(false);
    expect(isNavigationItemActive(drivers, employees.items[1])).toBe(true);
    expect(isNavigationItemActive(drivers, employees.items[2])).toBe(false);
    expect(isNavigationItemActive(drivers, employees.items[3])).toBe(false);
    expect(activeNavigationSection(drivers)?.id).toBe("employees");

    expect(isNavigationItemActive(permissions, employees.items[0])).toBe(false);
    expect(isNavigationItemActive(permissions, employees.items[1])).toBe(false);
    expect(isNavigationItemActive(permissions, employees.items[2])).toBe(false);
    expect(isNavigationItemActive(permissions, employees.items[3])).toBe(true);
    expect(activeNavigationSection(permissions)?.id).toBe("employees");
});

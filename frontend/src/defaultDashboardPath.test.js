import { getDefaultDashboardPath } from "./defaultDashboardPath";

test("the approved Preview host lands on the design preview", () => {
    expect(getDefaultDashboardPath("salla-analytics.preview.emergentagent.com"))
        .toBe("/dashboard-design-preview");
});

test("Production remains on the current Mezan 2 dashboard", () => {
    expect(getDefaultDashboardPath("mezansalla.com")).toBe("/dashboard-v2");
});

test("unknown hosts fail closed to the current Mezan 2 dashboard", () => {
    expect(getDefaultDashboardPath("example.com")).toBe("/dashboard-v2");
});

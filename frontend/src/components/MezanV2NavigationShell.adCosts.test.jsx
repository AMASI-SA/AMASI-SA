import fs from "fs";
import path from "path";

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import {
    activeNavigationSection,
    isMezanV2Route,
    isNavigationItemActive,
    MEZAN_V2_NAV_SECTIONS,
} from "./MezanV2NavigationShell";

const marketing = MEZAN_V2_NAV_SECTIONS.find((section) => section.id === "marketing");

test("marketing navigation owns bank commission and exchange rate settings", () => {
    const item = marketing.items.find(
        (row) => row.to === "/settings/ads-currencies",
    );

    expect(item).toEqual({
        to: "/settings/ads-currencies",
        label: "العمولات وسعر الصرف",
        exactSearch: true,
    });
    expect(isMezanV2Route("/settings/ads-currencies")).toBe(true);
    expect(isNavigationItemActive(
        { pathname: "/settings/ads-currencies", search: "" },
        item,
    )).toBe(true);
    expect(activeNavigationSection({
        pathname: "/settings/ads-currencies",
        search: "",
    })?.id).toBe("marketing");
});

test("legacy component is only a compatibility export to the Mezan 2 page", () => {
    const root = path.resolve(__dirname, "..");
    const legacyPage = fs.readFileSync(
        path.join(root, "pages", "AdsCurrencySettings.jsx"),
        "utf8",
    );
    const v2Page = fs.readFileSync(
        path.join(root, "pages", "AdsCostSettingsV2.jsx"),
        "utf8",
    );
    const v2Service = fs.readFileSync(
        path.join(root, "services", "adsAccountCostSettingsV2.js"),
        "utf8",
    );

    expect(legacyPage).toContain('export { default } from "./AdsCostSettingsV2";');
    expect(legacyPage).not.toContain("/ads-currency-settings");
    expect(v2Page).toContain("mezan2-ad-cost-settings-page");
    expect(v2Service).toContain("/ads-manager/account-cost-settings");
    expect(v2Page).not.toContain('api.get("/counterparties');
    expect(v2Page).not.toContain('api.put("/ads-currency-settings');
    expect(v2Service).not.toContain("/counterparties");
    expect(v2Service).not.toContain("/ads-currency-settings");
});

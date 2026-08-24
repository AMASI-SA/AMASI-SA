jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => (
        <a href={to} {...props}>{children}</a>
    ),
    useNavigate: () => jest.fn(),
}));

import fs from "fs";
import path from "path";

import {
    buildGlobalSearchResults,
    normalizeGlobalSearch,
} from "./GlobalSearch";

test("Arabic normalization matches common spelling variants", () => {
    expect(normalizeGlobalSearch("إعدادات سَلّة")).toBe("اعدادات سله");
    expect(normalizeGlobalSearch("ادارة المنتجات")).toBe("اداره المنتجات");
});

test("numeric query keeps the existing order navigation behavior", () => {
    const results = buildGlobalSearchResults("#276936126", { isOwner: true });
    expect(results[0]).toMatchObject({
        type: "order",
        to: "/orders-v2/276936126",
        orderNumber: "276936126",
    });
});

test("Qoyod query exposes all unified control-center tabs", () => {
    const pages = buildGlobalSearchResults("قيود", { isOwner: true })
        .filter((result) => result.type === "page");

    expect(pages).toHaveLength(4);
    expect(pages.map((page) => page.to)).toEqual(expect.arrayContaining([
        "/integrations-v2/qoyod?tab=status",
        "/integrations-v2/qoyod?tab=settings",
        "/integrations-v2/qoyod?tab=exceptions",
        "/integrations-v2/qoyod?tab=reconciliation",
    ]));
    expect(pages.every((page) => page.label.startsWith("قيود"))).toBe(true);
});

test("navigation aliases find nested Mezan 2 destinations", () => {
    expect(
        buildGlobalSearchResults("ادارة منتجاتي", { isOwner: true })[0]?.to,
    ).toBe("/fulfillment-v2?workspace=my-products");

    expect(
        buildGlobalSearchResults("سناب", { isOwner: true })[0]?.to,
    ).toBe("/snapchat-accounts");
});

test("owner-only Mezan 2 pages stay hidden from non-owner users", () => {
    expect(
        buildGlobalSearchResults("قيود", { isOwner: false }),
    ).toEqual([]);
});


test("Layout imports the unified search component before rendering it", () => {
    const layoutSource = fs.readFileSync(path.join(__dirname, "Layout.jsx"), "utf8");
    expect(layoutSource).toContain('import GlobalSearch from "./GlobalSearch";');
    expect(layoutSource).toContain("<GlobalSearch");
});

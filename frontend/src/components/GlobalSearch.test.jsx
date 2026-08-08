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
    ).toBe("/ads-manager?provider=snapchat");
});

test("owner-only Mezan 2 pages stay hidden from non-owner users", () => {
    expect(
        buildGlobalSearchResults("قيود", { isOwner: false }),
    ).toEqual([]);
});

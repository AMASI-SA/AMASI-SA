import {
    buildMezanProductCostHref,
    buildMissingMezanCostHref,
    isValidSoldMissingCostResult,
    resolveInitialProductsView,
    resolveInitialSelectedProduct,
} from "./mezanV2CostLinks";


test("one missing product opens its Mezan V2 editor directly", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [{ mezan_product_id: "m-1", salla_product_id: "p-1" }],
    }, { from: "2026-08-01", to: "2026-08-01" });

    expect(href).toContain("/products-v2?");
    expect(href).not.toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
    expect(href).toContain("product=m-1");
    expect(href).toContain("focus=cost");
    expect(decodeURIComponent(href)).toContain("product_ids=p-1");
    expect(href).toContain("from=2026-08-01");
});


test("multiple missing products open the filtered sold-products list with dashboard filters", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [
            { mezan_product_id: "m-1", salla_product_id: "p-1" },
            { mezan_product_id: "m-2", salla_product_id: "p-2" },
        ],
    }, {
        from: "2026-08-01",
        to: "2026-08-02",
        payment_methods: ["مدى", "Apple Pay"],
        shipping_companies: ["سمسا"],
    });

    expect(href).not.toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
    expect(href).toContain("view=list");
    expect(decodeURIComponent(href)).toContain("payment_methods=مدى,Apple+Pay");
    expect(decodeURIComponent(href)).toContain("shipping_companies=سمسا");
    expect(decodeURIComponent(href)).toContain("product_ids=p-1,p-2");
    expect(href).not.toContain("product=");
});


test("dashboard link preserves the complete missing-Mezan sold cohort", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [
            { salla_product_id: "p-fallback-1", uses_salla_fallback: true, missing_everywhere: false },
            { salla_product_id: "p-hard-1", missing_everywhere: true },
            { salla_product_id: "p-fallback-2", uses_salla_fallback: true, missing_everywhere: false },
            { salla_product_id: "p-hard-2", missing_everywhere: true },
        ],
    }, { from: "2026-08-16", to: "2026-08-16" });

    expect(href).not.toContain("missing_all_cost");
    expect(decodeURIComponent(href)).toContain(
        "product_ids=p-fallback-1,p-hard-1,p-fallback-2,p-hard-2",
    );
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
});


test("an unmapped order line opens the filtered list instead of a missing editor", () => {
    const href = buildMissingMezanCostHref({
        missing_products: [{
            salla_product_id: "not-in-catalog",
            catalog_product_found: false,
        }],
    });

    expect(href).not.toContain("product=");
    expect(href).not.toContain("focus=cost");
});


test("a fallback-cost product opens its Mezan editor with the sold-missing filter", () => {
    const href = buildMezanProductCostHref({
        mezan_product_id: "m-fallback",
        salla_product_id: "p-fallback",
        catalog_product_found: true,
        cost_status: "salla_fallback",
    }, { from: "2026-08-01", to: "2026-08-02" });

    expect(href).not.toContain("workspace=intake");
    expect(href).toContain("missing_mezan_cost=1");
    expect(href).toContain("sold_only=1");
    expect(href).toContain("product=m-fallback");
    expect(decodeURIComponent(href)).toContain("product_ids=p-fallback");
    expect(href).toContain("focus=cost");
    expect(href).toContain("from=2026-08-01");
    expect(href).toContain("to=2026-08-02");
});


test("a costed product can still open its product cost editor without a missing filter", () => {
    const href = buildMezanProductCostHref({
        salla_product_id: "p-complete",
        catalog_product_found: true,
        cost_status: "complete",
    });

    expect(href).toContain("product=p-complete");
    expect(href).toContain("focus=cost");
    expect(href).not.toContain("missing_mezan_cost");
    expect(href).not.toContain("sold_only");
});


test("sold-missing list ignores the previously stored product", () => {
    const search = "?missing_mezan_cost=1&sold_only=1&view=list";

    expect(resolveInitialSelectedProduct(search, "old-product")).toBe("");
    expect(resolveInitialProductsView(search, "old-product")).toBe("list");
});

test("campaign lookup ignores the previously stored product until resolution finishes", () => {
    const search = "?focus=cost&lookup_sku=TARGET-SKU&lookup_name=Target";

    expect(resolveInitialSelectedProduct(search, "old-product")).toBe("");
    expect(resolveInitialProductsView(search, "old-product")).toBe("list");
});


test("a direct cost link still opens the requested product details", () => {
    const search = "?product=m-7&focus=cost";

    expect(resolveInitialSelectedProduct(search, "old-product")).toBe("m-7");
    expect(resolveInitialProductsView(search, "old-product")).toBe("detail");
});


test("rejects an all-products response while the sold-missing filter is active", () => {
    expect(isValidSoldMissingCostResult({
        items: [{ salla_product_id: "p-unsold" }],
        pagination: { total: 2006 },
        meta: {
            contract_version: "sold-missing-cost-v3",
            missing_mezan_cost: true,
            sold_only: true,
            cost_semantics: {
                missing_mezan_cost: "explicit_mezan_cost_only",
                calculation_cost: "mezan_then_salla_fallback",
            },
        },
    }, "p-1,p-2")).toBe(false);
});


test("accepts only marked sold-missing products inside the dashboard cohort", () => {
    expect(isValidSoldMissingCostResult({
        items: [
            { salla_product_id: "p-1", mezan_cost_missing: true },
            { salla_product_id: "p-2", mezan_cost_missing: true },
        ],
        pagination: { total: 2 },
        meta: {
            contract_version: "sold-missing-cost-v3",
            missing_mezan_cost: true,
            sold_only: true,
            cost_semantics: {
                missing_mezan_cost: "explicit_mezan_cost_only",
                calculation_cost: "mezan_then_salla_fallback",
            },
        },
    }, "p-1,p-2")).toBe(true);
});


test("rejects a response from the legacy generic products endpoint", () => {
    expect(isValidSoldMissingCostResult({
        items: [],
        pagination: { total: 0 },
        meta: { missing_mezan_cost: true, sold_only: true },
    })).toBe(false);
});

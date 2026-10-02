import { optionSummary, requiresStockVariant, searchProducts } from "./inventoryCatalogPresentation";

const product = {
    product_v2_id: "product-1", name: "عباية", options: [
        { id: "color", name: "اللون", values: [{ id: "black", name: "أسود" }] },
        { id: "size", name: "المقاس", values: [{ id: "m", name: "متوسط" }, { id: "l", name: "كبير" }] },
    ], variants: [
        { id: "stock-black-m", options: [{ option_id: "color", value_id: "black" }, { option_id: "size", value_id: "m" }] },
        { id: "stock-black-l", options: [{ option_id: "color", value_id: "black" }, { option_id: "size", value_id: "l" }] },
    ],
};

test("existing combination IDs display every option and remain searchable", () => {
    expect(optionSummary(product, product.variants[0])).toBe("اللون: أسود · المقاس: متوسط");
    expect(optionSummary(product, product.variants[1])).toBe("اللون: أسود · المقاس: كبير");
    for (const query of ["stock-black-m", "كبير", "product-1"]) expect(searchProducts([product], query)).toEqual([product]);
});

test("customization metadata alone cannot create a stock variant requirement", () => {
    expect(requiresStockVariant({ options: [{ name: "تطريز", type: "text" }], variants: [], variants_required: false })).toBe(false);
    expect(requiresStockVariant({ variants: [], variants_required: true })).toBe(true);
    expect(requiresStockVariant(product)).toBe(true);
});

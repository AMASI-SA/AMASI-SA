import {
    reviewedPieceCustomerOptions,
    reviewedPieceOrderNumber,
} from "./reviewedPieceCard";

test("reads the order number from the exact physical piece", () => {
    expect(reviewedPieceOrderNumber({
        source_order_numbers: ["fallback"],
        source_lines: [{ order_number: "280829510" }],
    })).toBe("280829510");
});

test("shows customer options without relying on product name or SKU", () => {
    expect(reviewedPieceCustomerOptions({
        name: "اسم المنتج لا يعرض في البطاقة",
        sku: "AMS13067",
        source_lines: [{
            options_normalized: {
                "اسم الطفل": "خالد",
                "المقاس": { value: "4 سنوات" },
            },
        }],
    })).toEqual([
        { label: "اسم الطفل", value: "خالد" },
        { label: "المقاس", value: "4 سنوات" },
    ]);
});

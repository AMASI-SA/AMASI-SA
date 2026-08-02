import {
    findReviewedProductForCard,
    reviewedProductSortButtonLabel,
    reviewedProductSortCandidateSummary,
    updateReviewedProductSortPreference,
} from "./reviewedProductSortUi";
import { isReviewedProductsWindow } from "./reviewedProductSortEnhancer";
import { reviewedProductThumbnailUrl } from "./reviewedProductSortThumbnailEnhancer";

const products = [
    { group_key: "product:1", name: "سلسال", sku: "N-1" },
    { group_key: "product:2", name: "شنطة", sku: "B-2" },
];

test("sort candidate summary shows highest piece demand", () => {
    expect(reviewedProductSortCandidateSummary({
        values: [
            { value: "5 سنوات", quantity: 30 },
            { value: "6 سنوات", quantity: 12 },
        ],
    })).toBe("5 سنوات (30 قطعة)، 6 سنوات (12 قطعة)");
});

test("sort button reflects saved preference", () => {
    expect(reviewedProductSortButtonLabel({ preparation_sort_label: "العمر" }))
        .toBe("ترتيب الملف: العمر");
    expect(reviewedProductSortButtonLabel({})).toBe("تحديد ترتيب الملف");
});

test("visible reviewed card maps to its product", () => {
    expect(findReviewedProductForCard(products, {
        name: "شنطة",
        sku: "B-2",
    })?.group_key).toBe("product:2");
});

test("saved preference updates only sorting metadata", () => {
    const updated = updateReviewedProductSortPreference(products[0], {
        spec_key: "اللون",
        spec_label: "اللون",
        candidates: [{ key: "اللون", label: "اللون", values: [] }],
    });
    expect(updated.group_key).toBe("product:1");
    expect(updated.preparation_sort_spec).toBe("اللون");
    expect(updated.preparation_sort_candidates).toHaveLength(1);
});

test("sorting controls appear in reviewed products window only", () => {
    const emptyRoot = { querySelector: () => null };
    expect(isReviewedProductsWindow({
        pathname: "/fulfillment-v2",
        search: "?stage=reviewed&view=products",
    }, emptyRoot)).toBe(true);
    expect(isReviewedProductsWindow({
        pathname: "/fulfillment-v2",
        search: "?stage=reviewed&view=files",
    }, emptyRoot)).toBe(false);
});

test("sort manager thumbnail uses current reviewed product image", () => {
    expect(reviewedProductThumbnailUrl({ image_url: "https://cdn.example/product.jpg" }))
        .toBe("https://cdn.example/product.jpg");
    expect(reviewedProductThumbnailUrl({ main_image: "https://cdn.example/fallback.jpg" }))
        .toBe("https://cdn.example/fallback.jpg");
    expect(reviewedProductThumbnailUrl({})).toBe("");
});

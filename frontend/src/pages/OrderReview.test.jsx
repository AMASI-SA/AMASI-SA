import { renderToStaticMarkup } from "react-dom/server";

jest.mock("../services/orderReviewEngine", () => ({
    completeOrderReview: jest.fn(),
    getOrderReview: jest.fn(),
    listPendingOrderReviews: jest.fn(),
    updateOrderReviewItem: jest.fn(),
}));

jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn(), warning: jest.fn() },
}));

import { PaymentReceiptCard, reviewProductSpecs } from "./OrderReview";


test("review product specs include Salla custom fields and nested visible values", () => {
    const specs = reviewProductSpecs({
        options: [],
        custom_fields: [
            { name: "هل تريد إضافة كرت اهداء", value: { name: "لا" } },
            { "الاسم المطلوب": "سارة" },
        ],
        color: null,
        size: null,
        material: null,
    });

    expect(specs).toEqual(expect.arrayContaining([
        expect.objectContaining({ name: "هل تريد إضافة كرت اهداء", value: "لا" }),
        expect.objectContaining({ name: "الاسم المطلوب", value: "سارة" }),
    ]));
});


test("bank transfer receipt renders a clickable image and full-size link", () => {
    const url = "https://cdn.salla.sa/example/receipt-274724433.jpg";
    const markup = renderToStaticMarkup(<PaymentReceiptCard receiptUrl={url} />);

    expect(markup).toContain('data-testid="order-review-payment-receipt"');
    expect(markup).toContain(`src="${url}"`);
    expect(markup).toContain("فتح الإيصال بالحجم الكامل");
});


test("unsafe receipt URLs are not rendered", () => {
    expect(renderToStaticMarkup(<PaymentReceiptCard receiptUrl="javascript:alert(1)" />)).toBe("");
});

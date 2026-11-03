import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PaymentSummary } from "../OrderDetailsV2.js";

jest.mock("react-router-dom", () => ({}), { virtual: true });
jest.mock("../OrderDetailsV2.jsx", () => () => null);
jest.mock("../../hooks/useOrders", () => ({}));
jest.mock("../../components/CompactOrderTimeline", () => () => null);

const render = (payment, status) => {
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(<PaymentSummary order={{ payment, status_native: status, totals: { total: 135, currency: "SAR" } }} />);
    return host.firstElementChild;
};

test.each(["unpaid", "failed", "paid", undefined])("an unsubstantiated %s label has no paid indicator", status => {
    const card = render({ method: "tamara", status, paid_amount: 0, remaining_amount: 0 });
    expect(card.dataset.collectionStatus).toBe("unknown");
    expect(card.textContent).not.toContain("تم الدفع بواسطة");
});

test("verified full payment shows the method and the amount without claiming a bank deposit", () => {
    const card = render({ method: "mada", source_balance: { paid: "135.00", remaining: "0.00" } });
    expect(card.dataset.collectionStatus).toBe("paid");
    expect(card.textContent).toContain("تم الدفع بواسطة مدى");
    expect(card.textContent).toContain("135.00");
    expect(card.textContent).not.toContain("تمت إضافة مبلغ الطلب");
});

test.each(["بانتظار الدفع", "قيد التنفيذ"])("partial %s keeps both sides of the balance visible", status => {
    const card = render({ method: "tabby", source_balance: { paid: "35.00", remaining: "100.00" } }, status);
    expect(card.dataset.collectionStatus).toBe("partial");
    expect(card.textContent).toContain("35.00");
    expect(card.textContent).toContain("100.00");
});

test("unpaid shows what is due, absent evidence while waiting does not display a zero amount", () => {
    expect(render({ method: "mada", source_balance: { paid: "0.00", remaining: "135.00" } }).dataset.collectionStatus).toBe("unpaid");
    expect(render({ method: "mada" }, "بانتظار الدفع").textContent).toContain("غير متاح");
});

test.each(["bank", "cod"])("preserves the separate %s flow", method => {
    const card = render({ method, method_native: method === "bank" ? "التحويل البنكي" : "الدفع عند الاستلام" });
    expect(card.dataset.paymentMethod).toBe(method);
    expect(card.textContent).not.toContain("تم الدفع بواسطة");
});

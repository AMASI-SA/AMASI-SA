import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PaymentMethodSetupNotice from "./PaymentMethodSetupNotice";

let host, root;
const pending = { key: "paypal", name: "PayPal / دولي", status: "needs_commission" };
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async setup => act(async () => root.render(<PaymentMethodSetupNotice setup={setup} />));

test("missing additive data and a completely configured directory show no notice", async () => {
    await render(undefined);
    expect(host.innerHTML).toBe("");
    await render({ items: [{ ...pending, status: "configured" }], pending_count: 0, complete: true });
    expect(host.innerHTML).toBe("");
});

test("new methods show readable names and encoded links to their commission editor", async () => {
    await render({ items: [pending, { key: "other", name: "wallet & pay", status: "needs_commission" },
        { key: "mada", name: "مدى", status: "configured" }], pending_count: 2, complete: true });
    expect(host.querySelector("[role=alert]").textContent).toContain("طرق دفع تحتاج إضافة عمولة");
    expect(host.textContent).toContain("أُضيفت تلقائيًا");
    expect(host.querySelectorAll("a")).toHaveLength(2);
    expect(host.querySelector("a").getAttribute("href")).toBe(`/operational-balances/provider-fees?method=${encodeURIComponent(pending.name)}`);
    expect(host.querySelector("a").getAttribute("aria-label")).toBe(`إضافة عمولة ${pending.name}`);
    expect(host.textContent).not.toContain("مدى");
});

test("incomplete detection remains visible instead of claiming no missing commissions", async () => {
    await render({ items: [], pending_count: 0, complete: false });
    expect(host.querySelector("[role=alert]").textContent).toContain("تعذر التحقق من جميع طرق الدفع");
    expect(host.querySelector("a").getAttribute("href")).toBe("/operational-balances/provider-fees");
});

test("a fresh resolved snapshot removes only the configured method notice", async () => {
    const second = { key: "another", name: "طريقة أخرى", status: "needs_commission" };
    await render({ items: [pending, second], pending_count: 2, complete: true });
    await render({ items: [{ ...pending, status: "configured" }, second], pending_count: 1, complete: true });
    expect(host.textContent).not.toContain(pending.name);
    expect(host.textContent).toContain(second.name);
    await render({ items: [pending, second].map(row => ({ ...row, status: "configured" })), pending_count: 0, complete: true });
    expect(host.innerHTML).toBe("");
});

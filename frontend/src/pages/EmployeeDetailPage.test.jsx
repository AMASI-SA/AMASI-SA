import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import EmployeeDetailPage from "./EmployeeDetailPage";
import { detailPageIdAt } from "../lib/webDetailAccess";
import { grantedPageAt } from "../lib/webPageAccess";
import api from "../lib/api";

jest.mock("react-router/dom", () => {
    const { TextEncoder, TextDecoder } = require("util");
    if (!global.TextEncoder) global.TextEncoder = TextEncoder;
    if (!global.TextDecoder) global.TextDecoder = TextDecoder;
    const location = require.resolve("react-router/package.json");
    const entry = require(location).exports["./dom"].node.default;
    return require(require("path").resolve(require("path").dirname(location), entry));
}, { virtual: true });

let host;
let root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});
const render = async tree => act(async () => root.render(tree));
jest.mock("../lib/api", () => ({ get: jest.fn() }));

const order = { id: "detail.order", label: "تفاصيل الطلب", to: "/employee-detail/detail.order", review_component: "detail_read" };

test("employee can find a specific order without loading other orders", async () => {
    api.get.mockResolvedValue({ data: { title: "طلب 654", facts: [{ label: "حالة الطلب", value: "قيد التجهيز" }], tables: [] } });
    await render(<MemoryRouter initialEntries={[order.to]}><Routes>
        <Route path="*" element={<EmployeeDetailPage page={order} />} />
    </Routes></MemoryRouter>);
    expect(api.get).not.toHaveBeenCalled();
    await act(async () => {
        const input = host.querySelector("input");
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "654");
        input.dispatchEvent(new Event("input", {bubbles: true}));
    });
    await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true})));
    expect(host.textContent).toContain("قيد التجهيز");
    expect(api.get.mock.calls[0][0]).toBe("/web-page-access-v1/details/detail.order");
    expect(api.get.mock.calls[0][1].params.identity).toBe("654");
    await act(async () => host.querySelector("a").click());
    expect(host.textContent).not.toContain("قيد التجهيز");
});

test("only explicit detail grants match canonical native links and landing URLs", () => {
    const loc = p => new URL(p, "https://mezan.invalid");
    const list = {id: "orders.list", to: "/orders-v2"};
    expect(grantedPageAt([list], loc("/orders-v2/654"))).toBeNull();
    expect(grantedPageAt([order], loc("/orders-v2/654?returnTo=%2Fdashboard-advanced"))).toBe(order);
    expect(grantedPageAt([order], loc(order.to))).toBe(order);
    expect(detailPageIdAt("/entity-ledger/supplier/abc")).toBe("detail.supplier");
    expect(detailPageIdAt("/entity-ledger/external/abc")).toBe("detail.entity");
    for (const url of ["/orders-v2/654/admin", "/accounts/abc?workspace=other", "/entity-ledger/employee/abc",
        "/employee-detail/detail.fake", "/analyses/abc%2Fadmin", "/suppliers/a/ledger-detail?from=bad"]) {
        expect(detailPageIdAt(url)).toBeNull();
    }
});

test("financial detail landing lists only returned choices and opens the scoped record", async () => {
    api.get.mockReset();
    api.get.mockResolvedValueOnce({data: {choices: [{ id: "a", label: "مورد التفصيل" }], has_more: false}})
        .mockResolvedValueOnce({data: {title: "مورد التفصيل", facts: [], tables: []}});
    const page = {id: "detail.supplier", to: "/employee-detail/detail.supplier", label: "كشف المورد"};
    await render(<MemoryRouter initialEntries={[page.to]}><Routes>
        <Route path="*" element={<EmployeeDetailPage page={page} />} />
    </Routes></MemoryRouter>);
    await act(async () => host.querySelector("li a").click());
    expect(host.querySelector("h2").textContent).toBe("مورد التفصيل");
    expect(api.get.mock.calls[1][1].params.identity).toBe("a");
});

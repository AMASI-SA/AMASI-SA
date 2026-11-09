import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../context/AuthContext";
import WebPagePermissions from "../pages/WebPagePermissions";
import EmployeeWebLayout from "./EmployeeWebLayout";
import OwnerOnlyRoute from "./OwnerOnlyRoute";
import ProtectedRoute from "./ProtectedRoute";

jest.mock("react-router/dom", () => {
    const { TextEncoder, TextDecoder } = require("util");
    if (!global.TextEncoder) global.TextEncoder = TextEncoder;
    if (!global.TextDecoder) global.TextDecoder = TextDecoder;
    const path = require("path");
    const packagePath = require.resolve("react-router/package.json");
    const target = require(packagePath).exports["./dom"].node.default;
    return require(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });
jest.mock("../context/AuthContext", () => ({ useAuth: jest.fn(), useOptionalAuth: () => ({}) }));
jest.mock("../pages/AmasiDeliveryApp", () => () => null);
jest.mock("../lib/api", () => ({ get: jest.fn(), put: jest.fn() }));

const PRODUCTS = { id: "products.catalog", section: "products", label: "إدارة المنتجات", to: "/products-v2", write_label: "مسودة تعديل" };
const ORDERS = { id: "orders.list", section: "orders", label: "كل الطلبات", to: "/orders-v2" };
const REPORTS = {
    id: "accounting.journals_reports.view",
    section: "accounting",
    label: "القيود والتقارير",
    to: "/integrations-v2?workspace=financial&page=journals-reports",
};

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

function byTestId(id) {
    return host.querySelector('[data-testid="' + id + '"]');
}

async function renderEmployee(url, pages) {
    useAuth.mockReturnValue({
        loading: false,
        user: { id: "employee", role: "user", web_page_access: { pages } },
    });
    await act(async () => root.render(
        <MemoryRouter initialEntries={[url]}>
            <Routes>
                <Route path="/" element={<ProtectedRoute><h1>الرئيسية</h1></ProtectedRoute>} />
                <Route path="/products-v2" element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute employeePages={[PRODUCTS.id]}>
                            <EmployeeWebLayout pages={pages}><h1>كتالوج المتجر</h1></EmployeeWebLayout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                } />
                <Route path="/integrations-v2" element={
                    <ProtectedRoute>
                        <OwnerOnlyRoute>
                            <EmployeeWebLayout pages={pages}><h1>تقارير المتجر</h1></EmployeeWebLayout>
                        </OwnerOnlyRoute>
                    </ProtectedRoute>
                } />
            </Routes>
        </MemoryRouter>
    ));
}

test("a products-only employee lands on the catalogue and can expand the only granted section", async () => {
    await renderEmployee("/", [PRODUCTS]);
    expect(host.querySelector("h1").textContent).toBe("كتالوج المتجر");
    expect(byTestId("mezan-v2-primary-orders")).toBeNull();
    expect(byTestId("mezan-v2-open-all")).toBeNull();
    expect(byTestId("mezan-v2-secondary-products")).toBeNull();

    const productsSection = byTestId("mezan-v2-primary-products");
    expect(productsSection.tagName).toBe("BUTTON");
    await act(async () => productsSection.click());
    const links = Array.from(byTestId("mezan-v2-secondary-products").querySelectorAll("a"));
    expect(links.map((link) => [link.textContent, link.getAttribute("href")]))
        .toEqual([["إدارة المنتجات", "/products-v2"]]);
});

test("a granted product detail can be opened by direct link", async () => {
    await renderEmployee("/products-v2?product=123", [PRODUCTS]);
    expect(host.querySelector("h1").textContent).toBe("كتالوج المتجر");
});

test.each([
    "/products-v2?workspace=access",
    "/products-v2?workspace=intake",
    REPORTS.to,
])("an ungranted direct link %s leaves the allowed header available", async (url) => {
    await renderEmployee(url, [PRODUCTS]);
    expect(host.querySelector("h1").textContent).toBe("هذه الصفحة غير متاحة لحسابك");
    expect(byTestId("mezan-v2-primary-products")).not.toBeNull();
    expect(byTestId("mezan-v2-primary-accounting")).toBeNull();
});

test("the accounting header lists only the accounting page granted along with products", async () => {
    await renderEmployee(REPORTS.to, [PRODUCTS, REPORTS]);
    expect(host.querySelector("h1").textContent).toBe("تقارير المتجر");
    expect(byTestId("mezan-v2-primary-products")).not.toBeNull();
    await act(async () => byTestId("mezan-v2-primary-accounting").click());
    const links = Array.from(byTestId("mezan-v2-secondary-accounting").querySelectorAll("a"));
    expect(links.map((link) => link.getAttribute("href"))).toEqual([REPORTS.to]);
});

test("the owner can save a selection spanning sections in one request", async () => {
    const member = { id: "employee", name: "موظف اختبار", revision: 4, page_modes: {} };
    const inventory = { id: "products.inventory", section: "products", label: "المخزون", to: "/inventory-v2" };
    api.get.mockResolvedValue({ data: { members: [member], pages: [PRODUCTS, inventory, ORDERS, REPORTS] } });
    const pageModes = { [PRODUCTS.id]: "write", [inventory.id]: "read", [REPORTS.id]: "read" };
    api.put.mockResolvedValue({ data: { member: { ...member, revision: 5, page_modes: pageModes } } });
    await act(async () => root.render(<WebPagePermissions initialMemberId={member.id} />));

    const sectionSelect = host.querySelectorAll("select")[1];
    await act(async () => Array.from(host.querySelectorAll("button"))
        .find((button) => button.textContent === "قراءة وكتابة للجميع").click());
    await act(async () => {
        sectionSelect.value = "accounting";
        sectionSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
        const reports = host.querySelector('select[aria-label="القيود والتقارير"]');
        reports.value = "read";
        reports.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => host.querySelector('button[type="submit"]').click());

    expect(api.put).toHaveBeenCalledWith("/web-page-access-v1/employee", {
        expected_revision: 4,
        page_modes: pageModes,
    });
    expect(host.querySelector('[role="status"]').textContent).toBe("تم حفظ الصفحات.");
});

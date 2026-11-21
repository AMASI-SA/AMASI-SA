import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import MezanProductsWorkspace from "./MezanProductsWorkspace";
import * as products from "../services/mezanProductsV2";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("../context/AuthContext", () => ({ useOptionalAuth: () => ({ user: { is_owner: true } }) }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("../services/mezanProductsV2", () => ({
  applyMissingSkus: jest.fn(), getProductV2: jest.fn(), getProductV2Costs: jest.fn(),
  getProductsV2Summary: jest.fn(), listProductsV2: jest.fn(), listWorkspaceProducts: jest.fn(),
  previewMissingSkus: jest.fn(), refreshProductV2Details: jest.fn(), saveProductV2Costs: jest.fn(), syncProductsV2: jest.fn(),
}));
jest.mock("../components/products/EmployeeProductDraft", () => ({ productId }) => <div data-testid="employee-draft">{productId}</div>);
jest.mock("../components/products/ProductControlCenterPanel", () => ({ productId }) => <div data-testid="native-product-editor">{productId}</div>);
jest.mock("../components/products/BundleEditor", () => ({ productId }) => <div data-testid="native-bundle-editor">{productId}</div>);
jest.mock("../components/products/BundleReportPanel", () => () => <div data-testid="native-bundle-reports"/>);
jest.mock("../components/products/ProductMediaDraftEditor", () => () => <div data-testid="native-media-editor"/>);
jest.mock("../components/products/ProductOptionCostEditor", () => () => <div data-testid="native-option-costs"/>);
jest.mock("../components/products/ProductOperationsEditor", () => () => <div data-testid="native-operations"/>);

const item = { id: "native-p", mezan_product_id: "native-p", name: "بلوفر ميزان", sku: "MZ-P-114", main_image: "/actual-catalog-image.png", price: 120, status: "active", options: [{ id: "color", name: "اللون", values: [{ id: "green", name: "أخضر" }] }, { id: "letters", name: "الأحرف", type: "text", values: [] }], variants: [{ id: "variant-m", display_name: "أخضر / M", sku: "MZ-P-114-M", price: 120 }] };
const page = { items: [item], pagination: { page: 1, total: 1, total_pages: 1 } };
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  window.history.replaceState({}, "", "/products-v2");
  window.localStorage.clear();
  window.scrollTo = jest.fn();
  Element.prototype.scrollIntoView = jest.fn();
  products.listWorkspaceProducts.mockResolvedValue(page);
  products.listProductsV2.mockResolvedValue(page);
  products.getProductsV2Summary.mockResolvedValue({ total: 1 });
  products.refreshProductV2Details.mockResolvedValue({ product: item });
  products.getProductV2.mockResolvedValue(item);
  products.getProductV2Costs.mockResolvedValue({ base_cost: "48", variant_costs: { "variant-m": "50" }, notes: "محفوظة" });
  products.saveProductV2Costs.mockResolvedValue({});
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async (props = {}) => act(async () => root.render(<MemoryRouter><MezanProductsWorkspace {...props}/></MemoryRouter>));
const button = (label) => [...host.querySelectorAll("button")].find((node) => node.textContent === label);
async function inputValue(node, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value").set.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("native catalog shows the actual product photo, SKU and customization without loading owner editors", async () => {
  await render();
  expect(host.querySelector("table img").getAttribute("src")).toBe(item.main_image);
  expect(host.querySelector("table").textContent).toContain("MZ-P-114");
  expect(host.textContent).toContain("أخضر");
  expect(host.textContent).toContain("تخصيص نصي عند الطلب");
  expect(host.querySelector('[data-testid="native-product-editor"]')).toBeNull();
  expect(host.querySelector('[data-testid="native-bundle-editor"]')).toBeNull();
  expect(products.listWorkspaceProducts).toHaveBeenCalledWith(expect.objectContaining({ perPage: 30, soldOnly: false }));
});

test("catalog-only employee ignores the owner bundle link and makes no summary, refresh or cost requests", async () => {
  window.history.replaceState({}, "", "/products-v2?inventory_view=bundles");
  await render({ catalogSearch: "?product=native-p" });
  expect(products.listProductsV2).toHaveBeenCalled();
  expect(products.getProductV2).toHaveBeenCalledWith("native-p");
  expect(products.listWorkspaceProducts).not.toHaveBeenCalled();
  expect(products.getProductsV2Summary).not.toHaveBeenCalled();
  expect(products.refreshProductV2Details).not.toHaveBeenCalled();
  expect(products.getProductV2Costs).not.toHaveBeenCalled();
  expect(host.querySelector('[data-testid="native-bundle-reports"]')).toBeNull();
  expect(button("مزامنة سلة")).toBeUndefined();
  expect(button("التكاليف")).toBeUndefined();
  expect(host.querySelector('[data-testid="employee-draft"]')).toBeNull();
});

test("authorized employee draft remains scoped to the selected product", async () => {
  await render({ catalogSearch: "?product=native-p", canEditDraft: true });
  expect(host.querySelector('[data-testid="employee-draft"]').textContent).toBe("native-p");
  expect(products.getProductV2Costs).not.toHaveBeenCalled();
});

test("owner bundle entry opens the existing bundle editor on the real selected product", async () => {
  window.history.replaceState({}, "", "/products-v2?inventory_view=bundles");
  await render();
  expect(host.querySelector('[data-testid="inventory-suite"]').getAttribute("data-inventory-page")).toBe("bundles");
  expect(host.querySelector('[data-testid="native-bundle-editor"]').textContent).toBe("native-p");
  expect(host.querySelector('[data-testid="native-bundle-reports"]')).not.toBeNull();
});

test("native cost tab saves the existing base and variant contract with preserved notes", async () => {
  await render();
  await act(async () => button("التكاليف").click());
  await inputValue(host.querySelector('[aria-label="تكلفة المنتج في ميزان"]'), "54.75");
  await act(async () => button("حفظ التكاليف").click());
  expect(products.saveProductV2Costs).toHaveBeenCalledWith("native-p", { base_cost: "54.75", variant_costs: { "variant-m": "50" }, notes: "محفوظة" });
});

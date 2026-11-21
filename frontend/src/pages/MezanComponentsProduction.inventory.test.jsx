import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import MezanComponentsProduction from "./MezanComponentsProduction";
import * as components from "../services/mezanComponentCatalog";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("../context/AuthContext", () => ({ useOptionalAuth: () => ({ user: { is_owner: true } }) }));
jest.mock("../services/mezanComponentCatalog", () => ({
  ...jest.requireActual("../services/mezanComponentCatalog"),
  getMezanComponentWorkspace: jest.fn(), addMezanComponentPreview: jest.fn(),
  updateMezanComponent: jest.fn(), saveMezanComponentPreviewCost: jest.fn(),
  setMezanComponentStatus: jest.fn(),
}));
const service = { id: "embroidery", name: "تطريز الاسم", code: "EMBROIDERY", track_inventory: false, status: "active", requires_preparation: true, reference_cost: { amount: 12 }, category_ids: ["clothes"], product_usages: [] };
const purchased = { id: "cloth", name: "خامة قطن", code: "COTTON", track_inventory: true, status: "active", cost_source: "purchase_invoice", cost_authoritative: true, initial_unit_cost: 1, reference_cost: { amount: 8.75 }, category_ids: ["clothes"], product_usages: [] };
const workspace = { components: [service, purchased], categories: [{ id: "clothes", name: "ملابس" }], products: [], meta: {} };
let host, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  components.getMezanComponentWorkspace.mockResolvedValue(workspace);
  components.addMezanComponentPreview.mockResolvedValue({ ok: true, resource: { id: "embroidery" } });
  components.updateMezanComponent.mockResolvedValue({ ok: true });
  components.setMezanComponentStatus.mockResolvedValue({ ok: true });
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async () => act(async () => root.render(<MemoryRouter><MezanComponentsProduction/></MemoryRouter>));
const button = (label, scope = document) => [...scope.querySelectorAll("button")].find((node) => node.textContent === label);
async function inputValue(node, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value").set.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("new component sheet requires its classification and sends the native creation contract", async () => {
  await render();
  await act(async () => button("إضافة مكوّن أو خدمة", host).click());
  let dialog = document.querySelector('[role="dialog"]');
  expect(dialog).not.toBeNull();
  expect(button("إنشاء", dialog).disabled).toBe(true);
  await inputValue(dialog.querySelector('input[placeholder="مثل: تطريز الاسم"]'), "  طباعة الأحرف  ");
  await inputValue(dialog.querySelector('input[placeholder="EMBROIDERY_NAME"]'), " letters ");
  expect(button("إنشاء", dialog).disabled).toBe(true);
  await act(async () => dialog.querySelector('.mz-component-categories input[type="checkbox"]').click());
  await inputValue(dialog.querySelector('input[type="number"]'), "4.5");
  expect(button("إنشاء", dialog).disabled).toBe(false);
  await act(async () => dialog.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(components.addMezanComponentPreview).toHaveBeenCalledWith(expect.objectContaining({ name: "طباعة الأحرف", code: "LETTERS", kind: "service", unit_cost: 4.5, category_ids: ["clothes"], requires_preparation: true, unit: "job" }));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

test("purchase authoritative cost displays actual purchase cost and stays locked in inspector and edit sheet", async () => {
  await render();
  await act(async () => host.querySelector('[aria-label="عرض خامة قطن"]').click());
  const current = host.querySelector('[aria-label="تكلفة المكوّن الحالية"]');
  expect(current.disabled).toBe(true);
  expect(current.value).toBe("8.75");
  await act(async () => button("تعديل العنصر", host).click());
  const dialog = document.querySelector('[role="dialog"]');
  const cost = dialog.querySelector('input[type="number"]');
  expect(cost.disabled).toBe(true);
  expect(cost.value).toBe("8.75");
  expect(components.saveMezanComponentPreviewCost).not.toHaveBeenCalled();
});

test("failed native create keeps entered values and shows the actual validation error inside the sheet", async () => {
  components.addMezanComponentPreview.mockResolvedValue({ ok: false, code: "component_code_exists" });
  await render();
  await act(async () => button("إضافة مكوّن أو خدمة", host).click());
  const dialog = document.querySelector('[role="dialog"]');
  await inputValue(dialog.querySelector('input[placeholder="مثل: تطريز الاسم"]'), "طباعة");
  await inputValue(dialog.querySelector('input[placeholder="EMBROIDERY_NAME"]'), "PRINT");
  await act(async () => dialog.querySelector('.mz-component-categories input[type="checkbox"]').click());
  await act(async () => dialog.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(dialog.querySelector('[role="alert"]').textContent).toContain("الرمز مستخدم");
  expect(dialog.querySelector('input[placeholder="EMBROIDERY_NAME"]').value).toBe("PRINT");
  expect(button("إنشاء", dialog).disabled).toBe(false);
});

test("sheet prevents dismissal and repeat submission while a native save is pending", async () => {
  let resolve;
  components.updateMezanComponent.mockReturnValue(new Promise((done) => { resolve = done; }));
  await render();
  await act(async () => button("تعديل العنصر", host).click());
  const dialog = document.querySelector('[role="dialog"]');
  const form = dialog.querySelector("form");
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(dialog.querySelector('[aria-label="إغلاق"]').disabled).toBe(true);
  expect(button("جارٍ الحفظ…", dialog).disabled).toBe(true);
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(components.updateMezanComponent).toHaveBeenCalledTimes(1);
  await act(async () => resolve({ ok: true }));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

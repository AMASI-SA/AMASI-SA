import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import ObligationsPanel from "./ObligationsPanel";
import { loadObligations, obligationsFailure, G_BASE } from "./obligationsAdapter";
jest.mock("../../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn(), delete: jest.fn() } }));
const definitions = { ssot_setup_version: 1, external_person_registry: "mz2_external_persons_v2" };
let node, root;
beforeEach(() => { jest.clearAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true; node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node); });
afterEach(() => { act(() => root.unmount()); node.remove(); });
const render = () => act(async () => { root.render(<ObligationsPanel />); });
test("old definitions fail closed without requesting legacy identities", async () => {
 api.get.mockResolvedValue({ data: { schema_version: 1 } }); await render();
 expect(api.get.mock.calls).toEqual([[`${G_BASE}/definitions`]]); expect(node.textContent).toContain("BLOCKED_BY_BACKEND / not_ready"); expect(api.post).not.toHaveBeenCalled();
});
test("native VAT rows preserve independent amounts, actual zero and missing value", async () => {
 api.get.mockImplementation(path => Promise.resolve({ data: path.endsWith("definitions") ? definitions : { items: [{ id: "sales", category: "sales_vat_payable", amount: "25", currency: "SAR" }, { id: "input", category: "input_vat", amount: "0", currency: "SAR" }, { id: "missing", category: "other_payable", amount: null, currency: "SAR" }] } }));
 await render(); expect(node.textContent).toContain("25.00 ر.س"); expect(node.textContent).toContain("0.00 ر.س"); expect(node.textContent).toContain("ضريبة مدخلات"); expect(node.querySelector('[aria-label="المبلغ غير متاح"]')).not.toBeNull(); expect(api.get).toHaveBeenCalledWith(`${G_BASE}/typed-facts`, undefined); expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
});
test("prepaid requires explicit cutover and displays server computed facts without currency fallback", async () => {
 const source = { source: "operating_recurring_obligations_v2", items: [{ invoice_id: "synthetic", currency: null, calculation: { remaining_prepaid_after_cutover: "123.45" } }], blockers: [] };
 const client = { get: jest.fn().mockResolvedValueOnce({ data: definitions }).mockResolvedValueOnce({ data: source }) };
 expect(await loadObligations("prepaid", "2026-10-01", client)).toEqual({ state: "ready", items: source.items, blockers: [] }); expect(client.get).toHaveBeenLastCalledWith(`${G_BASE}/prepaid-candidates`, { params: { cutover: "2026-10-01" } });
 client.get.mockReset().mockResolvedValue({ data: definitions }); expect(await loadObligations("prepaid", "", client)).toEqual({ state: "date_required" }); expect(client.get).toHaveBeenCalledTimes(1);
});
test("malformed response is error and absent route is unavailable", async () => {
 api.get.mockResolvedValueOnce({ data: definitions }).mockResolvedValueOnce({ data: {} }); await render(); expect(node.querySelector('[role="alert"]')).not.toBeNull(); api.get.mockRejectedValue({ response: { status: 404 } }); await act(async () => node.querySelector('[role="alert"] button').click()); expect(node.textContent).toContain("BLOCKED_BY_BACKEND / not_ready");
});
test("loading and empty states never imply zero", async () => {
 let finish; api.get.mockImplementation(() => new Promise(resolve => { finish = resolve; })); await render(); expect(node.querySelector('.ac-skeleton')).not.toBeNull(); api.get.mockResolvedValue({ data: { items: [] } }); await act(async () => finish({ data: definitions })); expect(node.textContent).toContain("لا توجد سجلات مطابقة"); expect(node.textContent).not.toContain("0.00 ر.س");
});
test("late previous tab cannot replace selected tab", async () => {
 let old; api.get.mockImplementation(path => path.endsWith("definitions") ? Promise.resolve({ data: definitions }) : path.endsWith("typed-facts") ? new Promise(resolve => { old = resolve; }) : Promise.resolve({ data: { items: [{ id: "person", label: "Synthetic person", kind: "external_person" }] } }));
 await render(); await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === "الأطراف الخارجية").click()); await act(async () => old({ data: { items: [{ id: "late", display_name: "SHOULD_NOT_RENDER" }] } })); expect(node.textContent).toContain("Synthetic person"); expect(node.textContent).not.toContain("SHOULD_NOT_RENDER");
});

test("network errors remain distinct from backend not-ready", () => {
 expect(obligationsFailure(new Error("network"))).toEqual({ state: "error" });
 expect(obligationsFailure({ response: { status: 503 } })).toEqual({ state: "blocked", reason: "native_source_not_ready" });
});
test("missing prepaid eligibility and freshness do not imply negative or confirmed facts", async () => {
 api.get.mockImplementation(path => Promise.resolve({ data: path.endsWith("definitions") ? definitions : path.endsWith("prepaid-candidates") ? { source: "operating_recurring_obligations_v2", items: [{ invoice_id: "missing-both", title: "Synthetic unknown" }, { invoice_id: "missing-eligible", title: "Synthetic unknown eligibility", source_stale: false }] } : { items: [] } }));
 await render(); await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === "المقدم والالتزامات المتكررة").click());
 expect(node.textContent).toContain("اختر تاريخ القطع");
 const input = node.querySelector('input[type="date"]');
 await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "2026-10-01"); input.dispatchEvent(new Event("input", { bubbles: true })); });
 expect(node.textContent).toContain("حالة المصدر غير متاحة"); expect(node.textContent).toContain("أهلية المقدم غير متاحة"); expect(node.textContent).not.toContain("غير مؤهل للمقدم"); expect(node.textContent).not.toContain("0.00 ر.س");
});

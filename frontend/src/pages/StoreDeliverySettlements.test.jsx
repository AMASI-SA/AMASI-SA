import React, { act } from "react";
import { createRoot } from "react-dom/client";
import Settlements from "./StoreDeliverySettlements";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

const pendingPath = "/store-delivery/payment-review/pending";
let root, host, summary, summaryResponse, pendingResponse;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const selectDriver = async id => {
  await act(async () => {
    const select = host.querySelector("select");
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(select, id);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
};
const render = async () => { await act(async () => root.render(<Settlements />)); };

beforeEach(() => {
  jest.resetAllMocks();
  global.IS_REACT_ACT_ENVIRONMENT = true;
  summary = {
    cod_cash_collected: 500, cod_cash_remitted: 100, cod_cash_custody: 400,
    delivery_earnings_due: 60, net_due_from_driver: 340, net_due_to_driver: 0,
    ledger_cod_receivable: null, ledger_delivery_fee_payable: null, ledger_net_balance: null,
    noncash_payment_reviews: {
      card_terminal: { pending: { count: 1, amount: 200 }, approved: { count: 1, amount: 80 }, rejected: { count: 0, amount: 0 } },
      bank_transfer: { pending: { count: 1, amount: 125 }, approved: { count: 0, amount: 0 }, rejected: { count: 1, amount: 30 } },
    },
  };
  summaryResponse = () => Promise.resolve({ data: { summary, items: [
    { id: "pending", settlement_type: "cod_remittance", status: "pending_driver_confirmation", amount: 100 },
    { id: "rejected", settlement_type: "cod_remittance", status: "rejected", amount: 30, driver_rejection_reason: "لم أرسل هذا التحويل" },
  ] } });
  pendingResponse = () => Promise.resolve({ data: { items: [
    { assignment_id: "pos-assignment", order_number: "POS-ORDER", driver_name: "Synthetic driver", payment_method: "card_terminal", amount: "200.00", receipt_reference: "receipt", receipt_url: "/synthetic" },
    { assignment_id: "bank-assignment", order_number: "BANK-ORDER", driver_name: "Synthetic driver", payment_method: "bank_transfer", amount: "125.00", receipt_reference: "receipt-2", receipt_url: "/synthetic-2" },
  ] } });
  api.get.mockImplementation((url, config) => {
    if (url === "/store-delivery/settlements/drivers") return Promise.resolve({ data: { items: [
      { id: "driver", name: "Synthetic driver", city: "City", cod_cash_custody: 400 },
      { id: "other", name: "Other driver", city: "City", cod_cash_custody: 0 },
    ] } });
    if (url.startsWith("/store-delivery/settlements/driver/")) return summaryResponse(url);
    if (url === pendingPath) return pendingResponse(config);
    if (url.endsWith("/bank-accounts")) return Promise.resolve({ data: { source: "mz2_financial_accounts", items: [] } });
    return Promise.resolve({ data: { items: [] } });
  });
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});

afterEach(() => { act(() => root.unmount()); host.remove(); });

test("shows actual custody and both payment review methods without inventing a linked ledger", async () => {
  await render(); await selectDriver("driver");
  const custody = host.querySelector('[aria-label="عهدة وأجرة الموصل"]');
  expect(custody.textContent).toContain("400.00");
  expect(custody.textContent).toContain("60.00");
  expect(custody.textContent).toContain("340.00");
  const table = host.querySelector('[aria-label="إجماليات مراجعة التحصيل"]');
  expect(table.textContent).toContain("شبكة / بطاقة");
  expect(table.textContent).toContain("تحويل بنكي");
  expect(table.textContent).toContain("125.00");
  expect(host.textContent).toContain("POS-ORDER"); expect(host.textContent).toContain("BANK-ORDER");
  expect(api.get).toHaveBeenCalledWith(pendingPath, { params: { driver_id: "driver" } });
  expect(host.textContent).toContain("بانتظار تأكيد الموصل");
  expect(host.textContent).toContain("رفض الموصل التسوية");
  expect(host.textContent).toContain("لم أرسل هذا التحويل");
  const remit = [...host.querySelectorAll("button")].find(button => button.textContent === "طلب توريد عهدة الكاش");
  expect(remit.disabled).toBe(true);
  expect(host.textContent).toContain("اعتماد توريد عهدة الموصل إلى البنك غير متاح");
  expect(api.post).not.toHaveBeenCalled();
});

test("settlement summary failure does not hide POS or bank receipt approval", async () => {
  summaryResponse = () => Promise.reject(new Error("summary unavailable"));
  await render(); await selectDriver("driver");
  expect(host.textContent).toContain("تعذر تحميل حساب الموصل");
  expect(host.textContent).toContain("POS-ORDER"); expect(host.textContent).toContain("BANK-ORDER");
  expect([...host.querySelectorAll("button")].filter(button => button.textContent === "تحميل خيارات الاعتماد")).toHaveLength(2);
  expect(api.post).not.toHaveBeenCalled();
});

test("late summary and receipt list cannot replace another selected driver's data", async () => {
  const oldSummary = deferred(), oldReviews = deferred();
  summaryResponse = url => url.endsWith("/driver") ? oldSummary.promise : Promise.resolve({ data: { summary: { ...summary, cod_cash_custody: 777 }, items: [] } });
  const initialReviews = pendingResponse;
  pendingResponse = config => config?.params?.driver_id === "driver" ? oldReviews.promise : initialReviews();
  await render(); await selectDriver("driver"); await selectDriver("other");
  await act(async () => {
    oldSummary.resolve({ data: { summary: { ...summary, cod_cash_custody: 999 }, items: [] } });
    oldReviews.resolve({ data: { items: [{ assignment_id: "late", order_number: "WRONG-DRIVER", payment_method: "bank_transfer", amount: 900 }] } });
  });
  expect(host.querySelector('[aria-label="عهدة وأجرة الموصل"]').textContent).toContain("777.00");
  expect(host.textContent).not.toContain("999.00"); expect(host.textContent).not.toContain("WRONG-DRIVER");
  expect(api.post).not.toHaveBeenCalled();
});

test("failed receipt load is unavailable, not an empty completed review queue", async () => {
  pendingResponse = () => Promise.reject(new Error("reviews unavailable"));
  await render();
  expect(host.textContent).toContain("تعذر تحميل مراجعات التحصيل");
  expect(host.textContent).not.toContain("لا توجد دفعات بانتظار المراجعة");
  expect(api.post).not.toHaveBeenCalled();
});

import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "./lib/api";
import { watchReviewConfirmation, isReviewConfirmationPending } from "./reviewConfirmation";
import { ReviewDrawer } from "./pages/OrderReview";
import { completeOrderReview, getOrderReview } from "./services/orderReviewEngine";
import { completeWaitingSummary } from "./reviewCustomerWaiting";

jest.mock("./lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("./services/orderReviewEngine", () => ({ completeOrderReview: jest.fn(), getOrderReview: jest.fn() }));
jest.mock("sonner", () => ({ toast: { info: jest.fn(), success: jest.fn(), error: jest.fn() } }));
jest.mock("./components/fulfillment/CustomerServiceInstructionBanner", () => () => null);

beforeEach(() => {
  jest.useFakeTimers();
  api.get.mockReset(); api.post.mockReset();
  api.get.mockResolvedValue({ data: { operation: null, revision: 0, order: { order_number: "synthetic" }, items: [] } });
  window.confirm = jest.fn(() => true);
  global.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); document.body.innerHTML = ""; });
async function flush() { for (let i = 0; i < 12; i += 1) await Promise.resolve(); }

test("confirmation polling is GET-only, retains wait through error, stops at completion", async () => {
  api.get.mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ data: { operation: { state: "syncing" } } })
    .mockResolvedValueOnce({ data: { operation: { state: "completed" } } });
  const updated = jest.fn();
  const stop = watchReviewConfirmation("synthetic", updated);
  await flush();
  jest.advanceTimersByTime(15000); await flush();
  expect(updated).toHaveBeenLastCalledWith({ state: "syncing" });
  jest.advanceTimersByTime(15000); await flush();
  expect(updated).toHaveBeenLastCalledWith({ state: "completed" });
  jest.advanceTimersByTime(30000); await flush();
  expect(api.get).toHaveBeenCalledTimes(3);
  expect(api.post).not.toHaveBeenCalled();
  stop();
});

test("drawer does not complete or advance on pending response", async () => {
  getOrderReview.mockResolvedValue({ revision: 0, order: { order_number: "synthetic", created_at: "2026-01-01", items: [] }, items: [], operational_items: [] });
  completeOrderReview.mockResolvedValue({ confirmation_pending: true, salla_status_sync: "pending" });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const completed = jest.fn();
  await act(async () => root.render(<ReviewDrawer orderNumber="synthetic" onCompleted={completed} onClose={() => {}} />));
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent.includes("تمت المراجعة"));
  expect(button).toBeTruthy();
  await act(async () => button.click());
  expect(completed).not.toHaveBeenCalled();
  expect(button.disabled).toBe(true);
  expect(host.textContent).toContain("جارٍ تأكيد تحديث الحالة");
  await act(async () => root.unmount());
});

test("waiting summary keeps overlay and prevents second click on pending", async () => {
  const overlay = document.createElement("div"); const button = document.createElement("button");
  overlay.append(button); document.body.append(overlay);
  api.post.mockResolvedValue({ status: 202, data: { confirmation_pending: true } });
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(overlay.isConnected).toBe(true);
  expect(button.disabled).toBe(true);
  expect(button.textContent).toContain("جارٍ تأكيد");
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(api.post).toHaveBeenCalledTimes(1);
});

test("only confirmed completion is treated as finished", () => {
  expect(isReviewConfirmationPending({ confirmation_pending: true })).toBe(true);
  expect(isReviewConfirmationPending({ salla_status_sync: "pending" })).toBe(true);
  expect(isReviewConfirmationPending({ salla_status_sync: "sent", ok: true })).toBe(false);
});

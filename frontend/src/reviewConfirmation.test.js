import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "./lib/api";
import {
  watchReviewConfirmation, isReviewConfirmationPending, isReviewCompletionComplete,
  isReviewOperationCompleted, reviewConfirmationMessage,
} from "./reviewConfirmation";
import { ReviewDrawer } from "./pages/OrderReview";
import { completeOrderReview, getOrderReview } from "./services/orderReviewEngine";
import { completeWaitingSummary } from "./reviewCustomerWaiting";
import { clearPendingReviewAdvance } from "./reviewAutoAdvance";

jest.mock("./lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() } }));
jest.mock("./services/orderReviewEngine", () => ({ completeOrderReview: jest.fn(), getOrderReview: jest.fn() }));
jest.mock("sonner", () => ({ toast: { info: jest.fn(), success: jest.fn(), error: jest.fn() } }));
jest.mock("./reviewAutoAdvance", () => ({ clearPendingReviewAdvance: jest.fn(), armReviewAutoAdvance: jest.fn(), attemptReviewAutoAdvance: jest.fn(), pendingReviewOrderRows: () => [] }));
jest.mock("./components/fulfillment/CustomerServiceInstructionBanner", () => () => null);

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  getOrderReview.mockReset(); completeOrderReview.mockReset();
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
  expect(clearPendingReviewAdvance).toHaveBeenCalled();
  expect(button.disabled).toBe(true);
  expect(host.textContent).toContain("عملية مراجعة سابقة مرتبطة بسلة");
  await act(async () => root.unmount());
});

test("waiting summary keeps overlay and prevents second click on pending", async () => {
  const overlay = document.createElement("div"); const button = document.createElement("button");
  overlay.append(button); document.body.append(overlay);
  api.post.mockResolvedValue({ status: 202, data: { confirmation_pending: true } });
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(overlay.isConnected).toBe(true);
  expect(button.disabled).toBe(true);
  expect(button.textContent).toContain("بانتظار فحص المراجعة السابقة");
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(api.post).toHaveBeenCalledTimes(1);
});

test("only confirmed completion is treated as finished", () => {
  expect(isReviewConfirmationPending({ confirmation_pending: true })).toBe(true);
  expect(isReviewConfirmationPending({ salla_status_sync: "pending" })).toBe(true);
  expect(isReviewConfirmationPending({ salla_status_sync: "sent", ok: true })).toBe(false);
});

const localCompletion = {
  ok: true, state: "completed", completion_mode: "mezan_local_v1",
  salla_status_sync: "not_requested", operation_id: "local-review",
};

async function renderDrawer() {
  getOrderReview.mockResolvedValue({ revision: 4, order: { order_number: "synthetic", created_at: "2026-01-01", items: [] }, items: [], operational_items: [] });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const completed = jest.fn();
  await act(async () => root.render(<ReviewDrawer orderNumber="synthetic" onCompleted={completed} onClose={() => {}} />));
  const button = () => [...host.querySelectorAll("button")].find((item) => item.textContent.includes("تمت المراجعة"));
  return { host, root, completed, button };
}

function waitingOverlay() {
  const overlay = document.createElement("div"); const button = document.createElement("button");
  overlay.append(button); document.body.append(overlay);
  return { overlay, button };
}

test("local completion requires durable completed state and never fabricates a Salla confirmation", () => {
  expect(isReviewCompletionComplete(localCompletion)).toBe(true);
  expect(isReviewConfirmationPending(localCompletion)).toBe(false);
  expect(isReviewCompletionComplete({ ...localCompletion, state: "prepared" })).toBe(false);
  expect(isReviewCompletionComplete({ ...localCompletion, salla_status_sync: "sent" })).toBe(false);
  expect(isReviewCompletionComplete({ ...localCompletion, completion_mode: "unknown" })).toBe(false);
  expect(isReviewCompletionComplete({ ...localCompletion, confirmation_pending: true })).toBe(false);
  expect(isReviewCompletionComplete({ ok: true, salla_status_sync: "sent", state: "syncing" })).toBe(false);
  expect(isReviewCompletionComplete({ ok: true })).toBe(false);
  expect(isReviewCompletionComplete({ ok: true, salla_status_sync: "sent" })).toBe(true);
  expect(isReviewCompletionComplete({ ok: true, already_reviewed: true })).toBe(true);
  expect(isReviewOperationCompleted({ state: "completed", completion_mode: "unknown" })).toBe(false);
  expect(reviewConfirmationMessage({ completion_mode: "mezan_local_v1" })).not.toContain("سلة");
});

test("no existing operation stops the initial read without a polling dependency", async () => {
  const updated = jest.fn();
  const stop = watchReviewConfirmation("synthetic", updated);
  await flush();
  jest.advanceTimersByTime(60000); await flush();
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(updated).not.toHaveBeenCalled();
  stop();
});

test("drawer completes a local review immediately without waiting for the operation endpoint", async () => {
  completeOrderReview.mockResolvedValue(localCompletion);
  const { host, root, completed, button } = await renderDrawer();
  await act(async () => button().click());
  expect(completeOrderReview).toHaveBeenCalledTimes(1);
  expect(completed).toHaveBeenCalledTimes(1);
  expect(completed).toHaveBeenCalledWith("synthetic");
  expect(host.textContent).not.toContain("عملية مراجعة سابقة مرتبطة بسلة");
  expect(getOrderReview).toHaveBeenCalledWith("synthetic", { localOnly: true });
  await act(async () => { jest.advanceTimersByTime(30000); await flush(); });
  expect(api.get).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount());
});

test("unknown completion response retains the drawer and only reloads local details", async () => {
  completeOrderReview.mockResolvedValue({ ok: true });
  const { root, completed, button } = await renderDrawer();
  await act(async () => button().click());
  expect(completed).not.toHaveBeenCalled();
  expect(clearPendingReviewAdvance).toHaveBeenCalled();
  expect(getOrderReview).toHaveBeenLastCalledWith("synthetic", { localOnly: true });
  await act(async () => root.unmount());
});

test("legacy resolution rejection keeps the drawer blocked and does not redispatch", async () => {
  const error = new Error("legacy operation requires resolution");
  error.code = "review_completion_legacy_operation_requires_resolution";
  completeOrderReview.mockRejectedValue(error);
  const { host, root, completed, button } = await renderDrawer();
  await act(async () => button().click());
  expect(button().disabled).toBe(true);
  expect(host.querySelector('[role="alert"]').textContent).toContain("تحتاج إلى فحص");
  await act(async () => button().click());
  expect(completeOrderReview).toHaveBeenCalledTimes(1);
  expect(completed).not.toHaveBeenCalled();
  await act(async () => root.unmount());
});

test("waiting summary completes from a local snapshot without confirmation polling", async () => {
  api.post.mockResolvedValue({ data: localCompletion });
  const { overlay, button } = waitingOverlay();
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(overlay.isConnected).toBe(false);
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(api.get).toHaveBeenCalledWith("/order-reviews-v1/synthetic", { params: { local_only: true } });
  expect(document.body.textContent).not.toContain("جارٍ تأكيد سلة");
});

test("waiting summary retains unknown results and blocks a legacy-resolution retry", async () => {
  const { overlay, button } = waitingOverlay();
  api.post.mockResolvedValueOnce({ data: { ok: true } });
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(overlay.isConnected).toBe(true);
  expect(clearPendingReviewAdvance).toHaveBeenCalled();
  api.post.mockRejectedValueOnce({ response: { data: { detail: { code: "review_completion_legacy_operation_requires_resolution" } } } });
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(button.disabled).toBe(true);
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(api.post).toHaveBeenCalledTimes(2);
  expect(overlay.isConnected).toBe(true);
});

test("waiting summary preserves the split warning and does not post after cancellation", async () => {
  api.get.mockResolvedValue({ data: { revision: 4, items: [{ order_item_id: "one", name: "منتج", quantity: 2 }] } });
  window.confirm.mockReturnValueOnce(true).mockReturnValueOnce(false);
  const { overlay, button } = waitingOverlay();
  await completeWaitingSummary({ order_number: "synthetic" }, overlay, button);
  expect(window.confirm).toHaveBeenCalledTimes(2);
  expect(api.post).not.toHaveBeenCalled();
  expect(overlay.isConnected).toBe(true);
  expect(button.disabled).toBe(false);
});

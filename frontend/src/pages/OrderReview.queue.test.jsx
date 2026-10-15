import { act } from "react";
import { createRoot } from "react-dom/client";
import OrderReview from "./OrderReview";
import { listPendingOrderReviews } from "../services/orderReviewEngine";

jest.mock("../services/orderReviewEngine", () => ({ listPendingOrderReviews: jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

beforeEach(() => {
  jest.useFakeTimers();
  listPendingOrderReviews.mockReset();
  global.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(() => {
  jest.clearAllTimers(); jest.useRealTimers(); document.body.innerHTML = "";
});

test("empty first cursor page retains navigation to older pending work and shows server total", async () => {
  listPendingOrderReviews.mockResolvedValueOnce({ items: [], nextCursor: "older", totalCount: 1 })
    .mockResolvedValue({ items: [{ order_number: "100", created_at: "2026-01-01", customer: { name: "عميل" } }], nextCursor: null, totalCount: 1 });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<OrderReview />));
    const next = host.querySelector('button[aria-label="الصفحة التالية"]');
    expect(next).toBeTruthy();
    expect(next.disabled).toBe(false);
    expect(host.querySelector('[data-testid="pending-review-total"]').textContent).toContain("1");
    await act(async () => next.click());
    expect(listPendingOrderReviews).toHaveBeenLastCalledWith({ limit: 10, cursor: "older", search: "" });
    expect(host.textContent).toContain("#100");
  } finally {
    await act(async () => root.unmount());
  }
});

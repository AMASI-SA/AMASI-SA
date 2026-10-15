import {
  WAITING_CUSTOMER_REVIEW_CSS,
  loadWaiting,
  reviewQueueTabVisibility,
  waitingCustomerActionLabel,
  waitingCustomerCount,
} from "./reviewCustomerWaiting";
import api from "./lib/api";

jest.mock("./lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
}));

afterEach(() => {
  document.body.innerHTML = "";
  api.get.mockReset();
});


test("customer waiting action changes between queue and resume modes", () => {
  expect(waitingCustomerActionLabel(false)).toBe("انتظار مراجعة العميل");
  expect(waitingCustomerActionLabel(true)).toBe("إرجاع لانتظار المراجعة");
});

test("queue tabs show exactly one queue at a time", () => {
  expect(reviewQueueTabVisibility("pending")).toEqual({
    pendingHidden: false,
    customerHidden: true,
  });
  expect(reviewQueueTabVisibility("customer")).toEqual({
    pendingHidden: true,
    customerHidden: false,
  });
});

test("red customer-review badge uses the waiting order count", () => {
  expect(waitingCustomerCount([])).toBe(0);
  expect(waitingCustomerCount([
    { order_number: "100" },
    { order_number: "99" },
  ])).toBe(2);
  expect(waitingCustomerCount(null)).toBe(0);
});

test("customer waiting drawer hides edit controls but keeps complete action available", () => {
  expect(WAITING_CUSTOMER_REVIEW_CSS).toContain(
    '[data-testid="order-review-product-card"] button',
  );
  expect(WAITING_CUSTOMER_REVIEW_CSS).toContain("[data-review-edit-control]");
  expect(WAITING_CUSTOMER_REVIEW_CSS).not.toContain(
    "data-review-customer-complete-action",
  );
});

test("polls only while the customer-review queue page is mounted", async () => {
  api.get.mockResolvedValue({ data: { items: [] } });

  document.body.innerHTML = "<main><h1>تسجيل الدخول</h1></main>";
  await loadWaiting();
  expect(api.get).not.toHaveBeenCalled();

  document.body.innerHTML = "<main><h1>لوحة التحكم</h1></main>";
  await loadWaiting();
  expect(api.get).not.toHaveBeenCalled();

  document.body.innerHTML = "<header><h1>طلبات بانتظار المراجعة</h1></header>";
  await loadWaiting();
  await loadWaiting();
  expect(api.get).toHaveBeenCalledTimes(2);

  document.body.innerHTML = "<main><h1>الطلبات</h1></main>";
  await loadWaiting();
  expect(api.get).toHaveBeenCalledTimes(2);
});

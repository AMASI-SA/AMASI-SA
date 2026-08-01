import {
  reviewQueueTabVisibility,
  waitingCustomerActionLabel,
  waitingCustomerCount,
} from "./reviewCustomerWaiting";


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

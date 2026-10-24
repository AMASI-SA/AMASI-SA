import api from "./api";
import { disableOperationalPush, enableOperationalPush } from "./operationalPush";
jest.mock("./api", () => ({ get: jest.fn(), post: jest.fn() }));
let subscription, registration;
beforeEach(() => {
  jest.resetAllMocks(); localStorage.clear();
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
  window.PushManager = function () {};
  window.Notification = { requestPermission: jest.fn().mockResolvedValue("granted") };
  subscription = { toJSON: () => ({ endpoint: "https://push.example.test/token", keys: { p256dh: "public", auth: "secret" } }), unsubscribe: jest.fn().mockResolvedValue(true) };
  registration = { scope: new URL("/operational-notifications/", window.location.origin).href, active: {}, pushManager: { getSubscription: jest.fn().mockResolvedValue(subscription), subscribe: jest.fn().mockResolvedValue(subscription) } };
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { register: jest.fn().mockResolvedValue(registration), getRegistration: jest.fn().mockResolvedValue(registration) } });
  localStorage.setItem("amasi_operational_push_installation", "installation-1");
  api.get.mockResolvedValue({ data: { enabled: true, public_key: "AQID" } }); api.post.mockResolvedValue({ data: {} });
});
test("denied permission never creates or sends a subscription", async () => {
  Notification.requestPermission.mockResolvedValue("denied"); await expect(enableOperationalPush()).rejects.toThrow("اسمح");
  expect(api.get).not.toHaveBeenCalled(); expect(navigator.serviceWorker.register).not.toHaveBeenCalled();
});
test("subscription uses isolated worker scope and current authenticated registration endpoint", async () => {
  await enableOperationalPush();
  expect(navigator.serviceWorker.register).toHaveBeenCalledWith("/operational-notifications-sw.js", { scope: "/operational-notifications/" });
  expect(api.post).toHaveBeenCalledWith("/operational-notifications/devices/register", { kind: "web", installation_id: "installation-1", subscription: subscription.toJSON() });
});
test("failed registration unsubscribes rather than leaving previous account subscribed", async () => {
  api.post.mockRejectedValue(new Error("offline")); await expect(enableOperationalPush()).rejects.toThrow("offline"); expect(subscription.unsubscribe).toHaveBeenCalledTimes(1);
});
test("logout unsubscribes device even when server unreachable", async () => {
  api.post.mockRejectedValue(new Error("offline")); await expect(disableOperationalPush()).rejects.toThrow("offline");
  expect(subscription.unsubscribe).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/operational-notifications/devices/unregister", { installation_id: "installation-1" }, { timeout: 5000 });
});

test("logout does not unsubscribe unrelated root-scope workers", async () => {
  registration.scope = new URL("/", window.location.origin).href;
  await disableOperationalPush();
  expect(registration.pushManager.getSubscription).not.toHaveBeenCalled(); expect(subscription.unsubscribe).not.toHaveBeenCalled();
});

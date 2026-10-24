import api from "./api";

const SCOPE = "/operational-notifications/";
const INSTALLATION = "amasi_operational_push_installation";

export function supportsOperationalPush() {
  return typeof window !== "undefined" && window.isSecureContext &&
    "Notification" in window && "serviceWorker" in navigator && "PushManager" in window;
}

function installationId() {
  let id = localStorage.getItem(INSTALLATION);
  if (!id) { id = crypto.randomUUID(); localStorage.setItem(INSTALLATION, id); }
  return id;
}

function decodeKey(key) {
  const decoded = atob(key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - key.length % 4) % 4));
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}

export async function enableOperationalPush() {
  if (!supportsOperationalPush()) throw new Error("هذا المتصفح لا يدعم إشعارات الهاتف؛ تبقى التنبيهات داخل التطبيق متاحة.");
  // Called only by the user's button, before network awaits, preserving user activation.
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("اسمح بالإشعارات من إعدادات المتصفح لتفعيل إشعار الهاتف.");
  const { data } = await api.get("/operational-notifications/web-push-key");
  if (!data?.enabled || !data?.public_key) throw new Error("إشعارات الهاتف لم تُفعّل على الخادم بعد.");
  const registration = await navigator.serviceWorker.register("/operational-notifications-sw.js", { scope: SCOPE });
  if (!registration.active) {
    await new Promise((resolve, reject) => {
      const worker = registration.installing || registration.waiting;
      if (!worker) return reject(new Error("تعذر تجهيز إشعارات الهاتف."));
      const timeout = setTimeout(() => reject(new Error("انتهت مهلة تجهيز إشعارات الهاتف.")), 15000);
      const check = () => { if (worker.state === "activated") { clearTimeout(timeout); resolve(); } else if (worker.state === "redundant") { clearTimeout(timeout); reject(new Error("تعذر تجهيز إشعارات الهاتف.")); } };
      worker.addEventListener("statechange", check); check();
    });
  }
  const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeKey(data.public_key) });
  try {
    await api.post("/operational-notifications/devices/register", { kind: "web", installation_id: installationId(), subscription: subscription.toJSON() });
  } catch (error) {
    await subscription.unsubscribe().catch(() => {});
    throw error;
  }
}

export async function disableOperationalPush() {
  if (!("serviceWorker" in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration(SCOPE);
  const subscription = registration?.scope === new URL(SCOPE, window.location.origin).href
    ? await registration.pushManager.getSubscription() : null;
  if (subscription) await subscription.unsubscribe();
  const id = localStorage.getItem(INSTALLATION);
  if (id) await api.post("/operational-notifications/devices/unregister", { installation_id: id }, { timeout: 5000 });
}

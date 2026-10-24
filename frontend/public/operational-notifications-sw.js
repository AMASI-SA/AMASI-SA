/* Push only: deliberately no fetch interception or application caches. */
self.addEventListener("install", (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch { /* Generic notification only. */ }
  const instructionId = typeof payload.instruction_id === "string" && /^[a-zA-Z0-9_-]{1,120}$/.test(payload.instruction_id) ? payload.instruction_id : "";
  event.waitUntil(self.registration.showNotification("أماسي", {
    body: "لديك تحديث على تعليمات الطلب. افتح التطبيق للاطلاع.",
    tag: typeof payload.event_id === "string" ? payload.event_id.slice(0, 120) : "amasi-instruction",
    data: { instructionId },
  }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const id = event.notification.data?.instructionId;
  const target = new URL("/", self.location.origin);
  if (typeof id === "string" && /^[a-zA-Z0-9_-]{1,120}$/.test(id)) target.searchParams.set("instruction_id", id);
  event.waitUntil(self.clients.openWindow(target.href));
});

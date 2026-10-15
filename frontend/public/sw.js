/*
 * MEZAN_SERVICE_WORKER_RETIREMENT_V1
 * Intentionally has no fetch handler and does not delete origin-wide caches.
 */
(function () {
  "use strict";

  function ignoreFailure(promise) {
    return promise.then(function () {}, function () {});
  }

  self.addEventListener("install", function (event) {
    event.waitUntil(self.skipWaiting());
  });

  self.addEventListener("activate", function (event) {
    var claim = ignoreFailure(self.clients.claim());
    event.waitUntil(claim.then(function () {
      return self.registration.unregister();
    }));
  });
}());

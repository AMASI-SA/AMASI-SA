const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
function worker() {
  const handlers = {}, calls = [];
  const self = { addEventListener: (name, cb) => { handlers[name] = cb; }, location: { origin: "https://example.test" }, registration: { showNotification: (...args) => { calls.push(args); return Promise.resolve(); } }, clients: { openWindow: (url) => { calls.push(url); return Promise.resolve(); } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public/operational-notifications-sw.js"), "utf8"), { self, URL });
  return { handlers, calls };
}
test("push never exposes supplied customer content and has no fetch interception", async () => {
  const { handlers, calls } = worker(); let work;
  handlers.push({ data: { json: () => ({ title: "customer name", body: "private note", instruction_id: "note-1", event_id: "event-1" }) }, waitUntil: (p) => { work = p; } }); await work;
  assert.equal(handlers.fetch, undefined);
  assert.equal(calls[0][0], "أماسي"); assert.ok(!JSON.stringify(calls).includes("private note"));
  assert.equal(calls[0][1].data.instructionId, "note-1");
});
test("notification click ignores remote URLs and invalid identifiers", async () => {
  for (const id of ["https://evil.test/", "../secrets", "note-1"]) {
    const { handlers, calls } = worker(); let work;
    handlers.notificationclick({ notification: { close() {}, data: { instructionId: id, url: "https://evil.test" } }, waitUntil: (p) => { work = p; } }); await work;
    assert.equal(calls[0], id === "note-1" ? "https://example.test/?instruction_id=note-1" : "https://example.test/");
  }
});

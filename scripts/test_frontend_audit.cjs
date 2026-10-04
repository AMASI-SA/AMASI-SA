"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { evaluate } = require("./check_frontend_audit.cjs");
const advisory = { github_advisory_id: "GHSA-vfj7-8cjw-p6xm", module_name: "braces", severity: "high", cves: ["CVE-2026-93687"], findings: [{ version: "3.0.3" }] };
function payload(list) {
  const vulnerabilities = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const a of list) vulnerabilities[a.severity]++;
  const text = [...list.map(a => ({ type: "auditAdvisory", data: { advisory: a } })), { type: "auditSummary", data: { vulnerabilities } }].map(JSON.stringify).join("\n");
  const status = Object.values(vulnerabilities).reduce((b, n, i) => b | (n ? 1 << i : 0), 0);
  return [text, status];
}
const run = list => evaluate(...payload(list));
test("the formerly excepted advisory and every duplicate path now block", () => assert.equal(run([advisory, advisory]).blocking.length, 2));
for (const severity of ["moderate", "high", "critical"]) {
  test(`${severity} always blocks regardless of package`, () => assert.equal(run([advisory, { ...advisory, github_advisory_id: "GHSA-other", severity }]).blocking.length, 2));
}
for (const [name, change] of Object.entries({ package: { module_name: "other" }, version: { findings: [{ version: "3.0.2" }] }, mixedVersions: { findings: [{ version: "3.0.3" }, { version: "3.0.2" }] }, cve: { cves: ["CVE-other"] }, extraCve: { cves: ["CVE-2026-93687", "CVE-other"] }, critical: { severity: "critical" }, noFindings: { findings: [] } })) {
  test(`${name} remains blocking`, () => assert.equal(run([{ ...advisory, ...change }]).blocking.length, 1));
}
for (const exception of [{}, { expires_at: "2026-10-10T16:47:30Z" }, { expires_at: "9999-01-01T00:00:00Z" }, { ghsa: advisory.github_advisory_id }]) {
  test(`exception argument is refused: ${JSON.stringify(exception)}`, () => assert.throws(() => evaluate(...payload([]), exception), /exceptions are not accepted/));
}
test("clean complete audit passes", () => assert.equal(run([]).blocking.length, 0));
test("existing low/info policy remains unchanged", () => assert.equal(run([{ ...advisory, severity: "low" }, { ...advisory, severity: "info" }]).blocking.length, 0));
test("audit errors fail closed", () => assert.throws(() => evaluate('{"type":"error"}', 1)));
test("missing summary refused", () => assert.throws(() => evaluate("", 0)));
test("duplicate summary refused", () => { const [text, status] = payload([]); assert.throws(() => evaluate(text + "\n" + text, status)); });
test("malformed output refused", () => assert.throws(() => evaluate("invalid", 0)));
test("missing advisory details refused", () => assert.throws(() => evaluate(payload([advisory])[0].split("\n").pop(), 8)));
test("audit exit mismatch refused", () => assert.throws(() => evaluate(payload([])[0], 1)));
test("unknown advisory severity refused", () => assert.throws(() => evaluate('{"type":"auditAdvisory","data":{"advisory":{"severity":"unknown"}}}\n' + payload([])[0], 0)));

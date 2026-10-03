"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { evaluate } = require("./check_frontend_audit.cjs");
const exception = require("../security/frontend-audit-exception.json");
const now = Date.parse(exception.created_at) + 1000;
const advisory = { github_advisory_id: exception.ghsa, module_name: "braces", severity: "high", cves: [exception.cve], findings: [{ version: "3.0.3" }] };
function payload(list) {
  const vulnerabilities = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const a of list) vulnerabilities[a.severity]++;
  const text = [...list.map(a => ({ type: "auditAdvisory", data: { advisory: a } })), { type: "auditSummary", data: { vulnerabilities } }].map(JSON.stringify).join("\n");
  const status = Object.values(vulnerabilities).reduce((b, n, i) => b | (n ? 1 << i : 0), 0);
  return [text, status];
}
const run = (list, e = exception, time = now) => evaluate(...payload(list), e, time);
test("only exact advisory allowed, duplicate paths count as one exception", () => { const r=run([advisory,advisory]); assert.equal(r.uniqueExceptions,1); assert.equal(r.allowed.length,2); assert.equal(r.blocking.length,0); });
for (const severity of ["moderate","high","critical"]) test(`other ${severity} remains blocking`, () => assert.equal(run([advisory,{...advisory,github_advisory_id:"GHSA-other",severity}]).blocking.length,1));
for (const [name, change] of Object.entries({package:{module_name:"other"},version:{findings:[{version:"3.0.2"}]},mixedVersions:{findings:[{version:"3.0.3"},{version:"3.0.2"}]},cve:{cves:["CVE-other"]},extraCve:{cves:[exception.cve,"CVE-other"]},critical:{severity:"critical"},noFindings:{findings:[]}})) test(`${name} is not excepted`,()=>assert.equal(run([{...advisory,...change}]).blocking.length,1));
test("expiry fails closed including at boundary",()=>assert.throws(()=>run([advisory],exception,Date.parse(exception.expires_at)),/expired/));
test("before approval fails closed",()=>assert.throws(()=>run([],exception,Date.parse(exception.created_at)-1)));
test("extension beyond seven days refused",()=>assert.throws(()=>run([],{...exception,expires_at:"2026-10-11T16:47:30Z"})));
test("different exception identifier refused",()=>assert.throws(()=>run([],{...exception,ghsa:"GHSA-other"})));
test("clean complete audit passes",()=>assert.equal(run([]).uniqueExceptions,0));
test("low remains nonblocking",()=>assert.equal(run([{...advisory,severity:"low",github_advisory_id:"GHSA-other"}]).blocking.length,0));
test("audit errors fail closed",()=>assert.throws(()=>evaluate('{"type":"error"}',1,exception,now)));
test("missing summary refused",()=>assert.throws(()=>evaluate('',0,exception,now)));
test("malformed output refused",()=>assert.throws(()=>evaluate('invalid',0,exception,now)));
test("missing advisory details refused",()=>assert.throws(()=>evaluate(payload([advisory])[0].split('\n').pop(),8,exception,now)));
test("audit exit mismatch refused",()=>assert.throws(()=>evaluate(payload([])[0],1,exception,now)));

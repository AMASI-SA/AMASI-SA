"use strict";
const fs = require("node:fs");
const BLOCKING = new Set(["moderate", "high", "critical"]);
const GHSA = "GHSA-vfj7-8cjw-p6xm";
const CVE = "CVE-2026-93687";

function evaluate(text, exitStatus, exception, now = Date.now()) {
  const created = Date.parse(exception.created_at);
  const expires = Date.parse(exception.expires_at);
  if (exception.ghsa !== GHSA || exception.cve !== CVE || exception.package !== "braces" ||
      exception.version !== "3.0.3" || exception.severity !== "high" ||
      !Number.isFinite(created) || !Number.isFinite(expires) ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(exception.created_at) ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(exception.expires_at) ||
      expires <= created || expires - created > 7 * 86400000 || now < created || now >= expires ||
      !exception.authorization || !exception.reason || !exception.removal_plan) {
    throw new Error("Invalid, not-yet-valid, or expired single-advisory exception");
  }
  if (!Number.isInteger(exitStatus) || exitStatus < 0 || exitStatus > 31) throw new Error("Audit execution failed");
  const rows = text.split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
  if (rows.some(row => row.type === "error")) throw new Error("Audit reported an execution error");
  const summaries = rows.filter(row => row.type === "auditSummary");
  if (summaries.length !== 1) throw new Error("Exactly one complete audit summary required");
  const counts = summaries[0].data?.vulnerabilities;
  const levels = ["info", "low", "moderate", "high", "critical"];
  if (!counts || levels.some(level => !Number.isInteger(counts[level]) || counts[level] < 0)) throw new Error("Invalid audit summary");
  const expectedStatus = levels.reduce((bits, level, i) => bits | (counts[level] ? 1 << i : 0), 0);
  if (exitStatus !== expectedStatus) throw new Error("Audit status does not match complete summary");
  const advisories = rows.filter(row => row.type === "auditAdvisory").map(row => row.data?.advisory);
  if (advisories.some(a => !a || !levels.includes(a.severity))) throw new Error("Invalid audit advisory");
  // A summary with findings but no corresponding detail must never pass.
  for (const level of levels) {
    if (counts[level] !== advisories.filter(a => a.severity === level).length) throw new Error("Audit summary/detail mismatch");
  }
  const allowed = [], blocking = [];
  for (const a of advisories) {
    const id = a.github_advisory_id || (a.url === `https://github.com/advisories/${GHSA}` ? GHSA : undefined);
    const match = id === GHSA && a.module_name === "braces" && a.severity === "high" &&
      Array.isArray(a.cves) && a.cves.length === 1 && a.cves[0] === CVE &&
      Array.isArray(a.findings) && a.findings.length > 0 && a.findings.every(f => f.version === "3.0.3");
    if (match) allowed.push(a);
    else if (BLOCKING.has(a.severity)) blocking.push(a);
  }
  return { allowed, blocking, advisoryRows: advisories.length, uniqueExceptions: allowed.length ? 1 : 0 };
}

if (require.main === module) {
  try {
    const [auditFile, status, exceptionFile] = process.argv.slice(2);
    const exception = JSON.parse(fs.readFileSync(exceptionFile, "utf8"));
    const result = evaluate(fs.readFileSync(auditFile, "utf8"), Number(status), exception);
    const lines = [
      `${GHSA}: ${result.allowed.length ? "ALLOWED / EXCEPTION (not remediated)" : "NOT PRESENT"}`,
      `Expires: ${exception.expires_at}; unique advisory exceptions: ${result.uniqueExceptions}; matching path rows: ${result.allowed.length}`,
      `Reason: ${exception.reason}`,
      `Blocking other advisories: ${result.blocking.length}`,
      ...result.blocking.map(a => `BLOCKED: ${a.github_advisory_id || a.url} ${a.module_name} ${a.severity}`),
    ];
    console.log(lines.join("\n"));
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join("\n\n") + "\n");
    if (result.blocking.length) process.exitCode = 1;
  } catch (error) {
    console.error(`Frontend audit refused: ${error.message}`);
    process.exitCode = 1;
  }
}
module.exports = { evaluate };

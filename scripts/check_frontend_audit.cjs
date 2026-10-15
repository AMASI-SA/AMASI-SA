"use strict";
const fs = require("node:fs");
const BLOCKING = new Set(["moderate", "high", "critical"]);
function evaluate(text, exitStatus) {
  if (arguments.length !== 2) throw new Error("Audit exceptions are not accepted");
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
  const blocking = advisories.filter(a => BLOCKING.has(a.severity));
  return { blocking, advisoryRows: advisories.length };
}

if (require.main === module) {
  try {
    if (process.argv.length !== 4) throw new Error("Expected audit file and exit status only; no exceptions");
    const [auditFile, status] = process.argv.slice(2);
    const result = evaluate(fs.readFileSync(auditFile, "utf8"), Number(status));
    const lines = [
      "Audit exceptions: NONE",
      `Blocking advisories: ${result.blocking.length}`,
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

const fs = require("node:fs");
const path = require("node:path");

const configPath = path.join(__dirname, "..", "vite.config.js");
const source = fs.readFileSync(configPath, "utf8");

const requiredSuffixes = [
  ".preview.emergentcf.cloud",
  ".preview.emergent.host",
];

for (const suffix of requiredSuffixes) {
  if (!source.includes(`"${suffix}"`)) {
    throw new Error(`Missing controlled Emergent Preview host suffix: ${suffix}`);
  }
}

const allowedHostsAssignments = source.match(
  /allowedHosts:\s*EMERGENT_PREVIEW_ALLOWED_HOSTS/g,
) || [];

if (allowedHostsAssignments.length !== 2) {
  throw new Error(
    `Expected server and preview allowedHosts assignments; found ${allowedHostsAssignments.length}`,
  );
}

if (/allowedHosts:\s*true\b/.test(source)) {
  throw new Error("Vite host validation must never be disabled with allowedHosts: true");
}

if (source.includes("salla-analytics.cluster-12.preview.emergentcf.cloud")) {
  throw new Error("Do not hard-code one ephemeral Emergent cluster hostname");
}

console.log("Emergent Preview host policy verified.");

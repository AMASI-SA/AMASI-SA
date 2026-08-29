"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const { buildOnce } = require("./build-frontend.cjs");

const frontendRoot = path.resolve(__dirname, "..");
const buildRoot = path.join(frontendRoot, "build");
const metadataPath = path.join(buildRoot, "build-meta.json");
const proofRoot = path.join(frontendRoot, ".release");
const proofPath = path.join(proofRoot, "reproducible-build.json");

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function cleanBuildState() {
  fs.rmSync(buildRoot, { force: true, recursive: true });
  fs.rmSync(path.join(frontendRoot, "node_modules", ".vite"), {
    force: true,
    recursive: true,
  });
  fs.rmSync(path.join(frontendRoot, "node_modules", ".cache"), {
    force: true,
    recursive: true,
  });
}

function readBuildMetadata() {
  const bytes = fs.readFileSync(metadataPath);
  const metadata = JSON.parse(bytes.toString("utf8"));
  return { bytes, metadata };
}

function sourceSummary(source) {
  return {
    scope: source.scope,
    git_tree_oid: source.git_tree_oid,
    file_count: source.file_count,
    tree_sha256: source.tree_sha256,
  };
}

function buildPass(ordinal, metadata, metadataBytes) {
  return {
    ordinal,
    build_meta: {
      path: "build-meta.json",
      bytes: metadataBytes.length,
      sha256: sha256(metadataBytes),
    },
    artifact_tree_sha256: metadata.artifact_tree_sha256,
  };
}

function reproducibilityProof(first, second) {
  return {
    schema_version: 1,
    kind: "frontend_two_clean_builds_v1",
    git_sha: second.metadata.git_sha,
    source: sourceSummary(second.metadata.source),
    toolchain: second.metadata.toolchain,
    environment: second.metadata.environment,
    passes: [
      buildPass(1, first.metadata, first.bytes),
      buildPass(2, second.metadata, second.bytes),
    ],
    retained_pass: 2,
  };
}

function writeProof(proof, targetPath = proofPath) {
  const temporary = `${targetPath}.tmp`;
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.writeFileSync(
    temporary,
    `${JSON.stringify(proof, null, 2)}\n`,
    "utf8",
  );
  fs.renameSync(temporary, targetPath);
}

function removeProofFiles(targetPath = proofPath) {
  fs.rmSync(targetPath, { force: true });
  fs.rmSync(`${targetPath}.tmp`, { force: true });
}

async function runReproducibleBuild({
  clean = cleanBuildState,
  build = buildOnce,
  read = readBuildMetadata,
  write = writeProof,
  removeProof = removeProofFiles,
} = {}) {
  removeProof();
  try {
    clean();
    await build();
    const first = read();

    clean();
    await build();
    const second = read();

    if (!first.bytes.equals(second.bytes)) {
      throw new Error(
        "Frontend release build is not reproducible: clean build metadata differs",
      );
    }

    const proof = reproducibilityProof(first, second);
    write(proof);
    return proof;
  } catch (error) {
    removeProof();
    throw error;
  }
}

async function main() {
  await runReproducibleBuild();
  process.stdout.write(
    `Frontend reproducibility proof: ${sha256(fs.readFileSync(proofPath))}\n`,
  );
}

if (require.main === module) {
  main().catch((error) => {
    removeProofFiles();
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  cleanBuildState,
  removeProofFiles,
  reproducibilityProof,
  runReproducibleBuild,
  writeProof,
};

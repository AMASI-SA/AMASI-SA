"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  removeProofFiles,
  runReproducibleBuild,
  writeProof,
} = require("./build-release-frontend.cjs");

function pass(bytes = Buffer.from('{"same":true}\n')) {
  return {
    bytes,
    metadata: {
      git_sha: "a".repeat(40),
      source: {
        scope: "git_head_frontend_tree_v1",
        git_tree_oid: "b".repeat(40),
        file_count: 5,
        tree_sha256: "c".repeat(64),
      },
      toolchain: { node: "22.23.2", yarn: "1.22.22", vite: "8.2.1" },
      environment: {
        mode: "production",
        effective: {
          NODE_ENV: "production",
          VITE_USER_NODE_ENV_present: false,
          VITE_prefixed_keys: [],
        },
        allowed_client_keys: ["REACT_APP_BACKEND_URL"],
        values: {
          REACT_APP_BACKEND_URL: { present: true, sha256: "d".repeat(64) },
        },
      },
      artifact_tree_sha256: "e".repeat(64),
    },
  };
}

test("release build cleans and builds A then B, retains B, and writes one proof", async () => {
  const events = [];
  const reads = [pass(), pass()];
  let proof;
  await runReproducibleBuild({
    removeProof() { events.push("remove-proof"); },
    clean() { events.push("clean"); },
    async build() { events.push("build"); },
    read() { events.push("read"); return reads.shift(); },
    write(value) { events.push("write"); proof = value; },
  });

  assert.deepEqual(events, [
    "remove-proof",
    "clean", "build", "read",
    "clean", "build", "read",
    "write",
  ]);
  assert.equal(proof.kind, "frontend_two_clean_builds_v1");
  assert.deepEqual(proof.passes.map((row) => row.ordinal), [1, 2]);
  assert.equal(proof.passes[0].build_meta.sha256, proof.passes[1].build_meta.sha256);
  assert.equal(proof.retained_pass, 2);
  assert.doesNotMatch(JSON.stringify(proof), /https:\/\/api\.example|secret/i);
});

test("mismatch or build failure removes stale proof and never writes", async () => {
  for (const scenario of ["mismatch", "failure"]) {
    const events = [];
    const reads = [pass(), pass(Buffer.from('{"same":false}\n'))];
    await assert.rejects(
      runReproducibleBuild({
        removeProof() { events.push("remove-proof"); },
        clean() { events.push("clean"); },
        async build() {
          events.push("build");
          if (scenario === "failure" && events.filter((row) => row === "build").length === 2) {
            throw new Error("build B failed");
          }
        },
        read() { events.push("read"); return reads.shift(); },
        write() { events.push("write"); },
      }),
      scenario === "mismatch" ? /not reproducible/ : /build B failed/,
    );
    assert.equal(events[0], "remove-proof");
    assert.equal(events.at(-1), "remove-proof");
    assert.equal(events.includes("write"), false);
  }
});

test("proof file replacement is atomic and cleanup is path-specific", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-proof-"));
  const target = path.join(root, ".release", "reproducible-build.json");
  const sibling = path.join(root, ".release", "future-policy.txt");
  fs.mkdirSync(path.dirname(sibling), { recursive: true });
  fs.writeFileSync(sibling, "keep\n");
  try {
    writeProof({ schema_version: 1 }, target);
    assert.equal(fs.existsSync(target), true);
    assert.equal(fs.existsSync(`${target}.tmp`), false);
    removeProofFiles(target);
    assert.equal(fs.existsSync(target), false);
    assert.equal(fs.existsSync(sibling), true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("proof cleanup never follows a symlinked parent", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-proof-link-"));
  const outside = path.join(root, "outside");
  const linked = path.join(root, "linked");
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, "reproducible-build.json"), "survive\n");
  fs.symlinkSync(outside, linked, "dir");
  const target = path.join(linked, "reproducible-build.json");
  try {
    assert.throws(() => removeProofFiles(target), /parent must be a real directory/);
    assert.equal(
      fs.readFileSync(path.join(outside, "reproducible-build.json"), "utf8"),
      "survive\n",
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

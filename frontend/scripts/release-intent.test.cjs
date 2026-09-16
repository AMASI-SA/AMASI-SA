"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const test = require("node:test");

const {
  canonicalSourceTreeSha256,
  gitBlobOid,
  loadReviewedReleaseIntent,
} = require("./release-intent.cjs");
const {
  bootstrapIntentEnabled,
  captureSourceIdentity,
  reviewedIntentEnabled,
} = require("./write-build-meta.cjs");

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function git(root, args, options = {}) {
  return execFileSync("git", ["-C", root, ...args], {
    encoding: "utf8",
    ...options,
  }).trim();
}

function writeJson(target, value) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function createFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-release-intent-"));
  const frontendRoot = path.join(root, "frontend");
  const intentPath = path.join(root, "release", "release-intent-v5.json");
  fs.mkdirSync(path.join(frontendRoot, "src"), { recursive: true });
  fs.writeFileSync(path.join(frontendRoot, "package.json"), '{"private":true}\n', "utf8");
  fs.writeFileSync(path.join(frontendRoot, "src", "main.js"), 'console.log("reviewed");\n', "utf8");
  fs.chmodSync(path.join(frontendRoot, "src", "main.js"), 0o755);

  git(root, ["init", "--quiet"]);
  git(root, ["add", "frontend"]);
  const rootTree = git(root, ["write-tree"]);
  const frontendTree = git(root, ["rev-parse", `${rootTree}:frontend`]);
  const staged = execFileSync(
    "git",
    ["-C", root, "ls-files", "--stage", "-z", "--", "frontend"],
  ).toString("utf8").split("\0").filter(Boolean);
  const files = staged.map((entry) => {
    const match = entry.match(/^(100644|100755) ([0-9a-f]{40}) 0\tfrontend\/(.+)$/);
    assert.ok(match, `unexpected Git index entry: ${entry}`);
    const [, mode, gitBlob, relative] = match;
    const content = fs.readFileSync(path.join(frontendRoot, ...relative.split("/")));
    assert.equal(gitBlobOid(content), gitBlob);
    return {
      path: relative,
      mode,
      git_blob: gitBlob,
      bytes: content.length,
      sha256: sha256(content),
    };
  }).sort((left, right) => left.path.localeCompare(right.path));
  const frontendSource = {
    scope: "git_head_frontend_tree_v1",
    git_tree_oid: frontendTree,
    file_count: files.length,
    files,
    tree_sha256: canonicalSourceTreeSha256(files),
  };
  const intent = {
    schema_version: 2,
    kind: "mezan_emergent_release_intent_v1",
    protocol_version: 5,
    source_git_sha: "a".repeat(40),
    source_base_git_sha: "b".repeat(40),
    branch: "hotfix/prod-snap-meta-final",
    frontend_source: frontendSource,
    backend_runtime_source: {},
    release_control_source: {},
    client_environment: {},
    frontend_build: {},
    frontend_reproducibility: {},
    critical_file_hashes: {},
    runtime_identity: {
      source_git_sha: "a".repeat(40),
      source_base_git_sha: "b".repeat(40),
      branch: "hotfix/prod-snap-meta-final",
    },
  };
  writeJson(intentPath, intent);

  const gitDirectory = path.join(root, ".git");
  const detachedGitDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "mezan-detached-git-"));
  fs.rmdirSync(detachedGitDirectory);
  fs.renameSync(gitDirectory, detachedGitDirectory);
  return {
    root,
    frontendRoot,
    intentPath,
    intent,
    detachedGitDirectory,
    cleanup() {
      fs.rmSync(root, { recursive: true, force: true });
      fs.rmSync(detachedGitDirectory, { recursive: true, force: true });
    },
  };
}

test("reviewed release intent validates full frontend source without .git", () => {
  const fixture = createFixture();
  try {
    assert.equal(fs.existsSync(path.join(fixture.root, ".git")), false);
    const loaded = loadReviewedReleaseIntent({
      intentPath: fixture.intentPath,
      frontendRoot: fixture.frontendRoot,
    });
    assert.equal(loaded.source_git_sha, "a".repeat(40));
    assert.equal(loaded.source_base_git_sha, "b".repeat(40));
    assert.equal(loaded.branch, fixture.intent.branch);
    assert.deepEqual(loaded.frontend_source, fixture.intent.frontend_source);

    const captured = captureSourceIdentity({
      environment: {},
      intentPath: fixture.intentPath,
      sourceRoot: fixture.frontendRoot,
    });
    assert.equal(captured.git_sha, fixture.intent.source_git_sha);
    assert.deepEqual(captured.source, fixture.intent.frontend_source);
  } finally {
    fixture.cleanup();
  }
});

test("explicit reviewed-intent mode ignores GitHub merge identity", () => {
  const fixture = createFixture();
  try {
    const captured = captureSourceIdentity({
      environment: {
        MEZAN_RELEASE_USE_REVIEWED_INTENT: "1",
        GITHUB_ACTIONS: "true",
        GITHUB_SHA: "b".repeat(40),
        GITHUB_WORKSPACE: "/different/github/checkout",
      },
      intentPath: fixture.intentPath,
      sourceRoot: fixture.frontendRoot,
    });
    assert.equal(captured.git_sha, fixture.intent.source_git_sha);
    assert.deepEqual(captured.source, fixture.intent.frontend_source);
  } finally {
    fixture.cleanup();
  }
});

test("reviewed release intent rejects source tampering and unreviewed files", () => {
  const fixture = createFixture();
  try {
    const mainPath = path.join(fixture.frontendRoot, "src", "main.js");
    fs.appendFileSync(mainPath, "// tampered\n", "utf8");
    assert.throws(
      () => loadReviewedReleaseIntent({
        intentPath: fixture.intentPath,
        frontendRoot: fixture.frontendRoot,
      }),
      /source mismatch.*main\.js/i,
    );
    fs.writeFileSync(mainPath, 'console.log("reviewed");\n', "utf8");
    fs.chmodSync(mainPath, 0o755);

    fs.writeFileSync(path.join(fixture.frontendRoot, "unreviewed.js"), "unsafe\n", "utf8");
    assert.throws(
      () => loadReviewedReleaseIntent({
        intentPath: fixture.intentPath,
        frontendRoot: fixture.frontendRoot,
      }),
      /membership mismatch.*unreviewed\.js/i,
    );
  } finally {
    fixture.cleanup();
  }
});

test("reviewed release intent rejects symlinks, malformed identity, and noncanonical source", async (t) => {
  await t.test("listed source must remain a regular file", () => {
    const fixture = createFixture();
    try {
      const mainPath = path.join(fixture.frontendRoot, "src", "main.js");
      fs.rmSync(mainPath);
      fs.symlinkSync("../package.json", mainPath);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /must not be a symlink|must be a regular file/i,
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("malformed top-level identity fails closed", () => {
    const fixture = createFixture();
    try {
      fixture.intent.source_git_sha = "A".repeat(40);
      writeJson(fixture.intentPath, fixture.intent);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /source_git_sha.*lowercase full Git SHA/i,
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("canonical source hashes cannot be asserted by intent alone", () => {
    const fixture = createFixture();
    try {
      fixture.intent.frontend_source.tree_sha256 = "f".repeat(64);
      writeJson(fixture.intentPath, fixture.intent);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /tree_sha256 is not canonical/i,
      );
      fixture.intent.frontend_source.tree_sha256 = canonicalSourceTreeSha256(
        fixture.intent.frontend_source.files,
      );
      fixture.intent.frontend_source.git_tree_oid = "f".repeat(40);
      writeJson(fixture.intentPath, fixture.intent);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /git_tree_oid is not canonical/i,
      );
    } finally {
      fixture.cleanup();
    }
  });
});

test("reviewed release intent requires the exact schema v2 top-level contract", async (t) => {
  await t.test("schema v1 is rejected", () => {
    const fixture = createFixture();
    try {
      fixture.intent.schema_version = 1;
      writeJson(fixture.intentPath, fixture.intent);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /unsupported release intent schema_version/i,
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("missing and extra top-level keys are rejected", () => {
    const fixture = createFixture();
    try {
      delete fixture.intent.backend_runtime_source;
      writeJson(fixture.intentPath, fixture.intent);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /release intent keys are not canonical/i,
      );

      fixture.intent.backend_runtime_source = {};
      fixture.intent.unreviewed = {};
      writeJson(fixture.intentPath, fixture.intent);
      assert.throws(
        () => loadReviewedReleaseIntent({
          intentPath: fixture.intentPath,
          frontendRoot: fixture.frontendRoot,
        }),
        /release intent keys are not canonical/i,
      );
    } finally {
      fixture.cleanup();
    }
  });

  await t.test("source base SHA must be lowercase and full length", () => {
    for (const invalid of ["B".repeat(40), "b".repeat(39), "g".repeat(40)]) {
      const fixture = createFixture();
      try {
        fixture.intent.source_base_git_sha = invalid;
        fixture.intent.runtime_identity.source_base_git_sha = invalid;
        writeJson(fixture.intentPath, fixture.intent);
        assert.throws(
          () => loadReviewedReleaseIntent({
            intentPath: fixture.intentPath,
            frontendRoot: fixture.frontendRoot,
          }),
          /source_base_git_sha.*lowercase full Git SHA/i,
        );
      } finally {
        fixture.cleanup();
      }
    }
  });

  await t.test("governed identity sections must be plain objects", () => {
    for (const name of [
      "backend_runtime_source",
      "release_control_source",
      "client_environment",
      "frontend_build",
      "frontend_reproducibility",
      "critical_file_hashes",
      "runtime_identity",
    ]) {
      const fixture = createFixture();
      try {
        fixture.intent[name] = [];
        writeJson(fixture.intentPath, fixture.intent);
        assert.throws(
          () => loadReviewedReleaseIntent({
            intentPath: fixture.intentPath,
            frontendRoot: fixture.frontendRoot,
          }),
          new RegExp(`${name} must be a JSON object`, "i"),
        );
      } finally {
        fixture.cleanup();
      }
    }
  });
});

test("reviewed release intent binds runtime identity provenance", async (t) => {
  for (const [name, replacement] of [
    ["source_git_sha", "c".repeat(40)],
    ["source_base_git_sha", "d".repeat(40)],
    ["branch", "different-release-branch"],
  ]) {
    await t.test(`${name} mirror drift is rejected`, () => {
      const fixture = createFixture();
      try {
        fixture.intent.runtime_identity[name] = replacement;
        writeJson(fixture.intentPath, fixture.intent);
        assert.throws(
          () => loadReviewedReleaseIntent({
            intentPath: fixture.intentPath,
            frontendRoot: fixture.frontendRoot,
          }),
          new RegExp(`runtime_identity ${name} differs from intent`, "i"),
        );
      } finally {
        fixture.cleanup();
      }
    });
  }
});

test("bootstrap capture is an explicit one-value switch", () => {
  assert.equal(bootstrapIntentEnabled({}), false);
  assert.equal(bootstrapIntentEnabled({ MEZAN_RELEASE_BOOTSTRAP_INTENT: "" }), false);
  assert.equal(bootstrapIntentEnabled({ MEZAN_RELEASE_BOOTSTRAP_INTENT: "1" }), true);
  for (const invalid of ["0", "true", "yes", " 1"] ) {
    assert.throws(
      () => bootstrapIntentEnabled({ MEZAN_RELEASE_BOOTSTRAP_INTENT: invalid }),
      /must be exactly 1/,
    );
  }
  assert.equal(reviewedIntentEnabled({}), false);
  assert.equal(
    reviewedIntentEnabled({ MEZAN_RELEASE_USE_REVIEWED_INTENT: "1" }),
    true,
  );
  for (const invalid of ["0", "true", "yes", " 1"] ) {
    assert.throws(
      () => reviewedIntentEnabled({ MEZAN_RELEASE_USE_REVIEWED_INTENT: invalid }),
      /must be exactly 1/,
    );
  }
});

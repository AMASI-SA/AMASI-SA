"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const { buildInvocation } = require("./build-entry.cjs");

const sha = "a".repeat(40);
const root = path.resolve("/tmp/mezan-build-entry-repo");
const frontend = path.join(root, "frontend");

test("GitHub Actions builds the exact checked-out HEAD with Vite only", () => {
  const invocation = buildInvocation({
    environment: {
      CI: "false",
      GITHUB_ACTIONS: "true",
      GITHUB_SHA: sha,
      GITHUB_WORKSPACE: root,
    },
    repositoryRoot: root,
    frontendDirectory: frontend,
    nodeBin: "/governed/github-node",
    gitHead: () => sha,
    viteBin: () => "/repo/node_modules/vite/bin/vite.js",
  });
  assert.deepEqual(invocation, {
    mode: "github_current_head",
    command: "/governed/github-node",
    args: ["/repo/node_modules/vite/bin/vite.js", "build"],
    cwd: frontend,
  });
  assert.doesNotMatch(JSON.stringify(invocation), /deployment_adapter|build:release/);
});

test("pull request builds use the reviewed head SHA instead of the merge SHA", () => {
  const invocation = buildInvocation({
    environment: {
      CI: "false",
      GITHUB_ACTIONS: "true",
      GITHUB_SHA: "b".repeat(40),
      GITHUB_WORKSPACE: root,
      GITHUB_EVENT_NAME: "pull_request",
      GITHUB_EVENT_PATH: "/runner/event.json",
    },
    repositoryRoot: root,
    frontendDirectory: frontend,
    nodeBin: "/governed/github-node",
    gitHead: () => sha,
    readEvent: () => ({ pull_request: { head: { sha } } }),
    viteBin: () => "/repo/node_modules/vite/bin/vite.js",
  });
  assert.equal(invocation.mode, "github_current_head");
  assert.equal(invocation.command, "/governed/github-node");
});

test("pull request integration builds may use the trusted merge SHA", () => {
  const mergeSha = "b".repeat(40);
  const invocation = buildInvocation({
    environment: {
      CI: "false",
      GITHUB_ACTIONS: "true",
      GITHUB_SHA: mergeSha,
      GITHUB_WORKSPACE: root,
      GITHUB_EVENT_NAME: "pull_request",
      GITHUB_EVENT_PATH: "/runner/event.json",
    },
    repositoryRoot: root,
    frontendDirectory: frontend,
    gitHead: () => mergeSha,
    readEvent: () => ({ pull_request: { head: { sha } } }),
    viteBin: () => "/repo/node_modules/vite/bin/vite.js",
  });
  assert.equal(invocation.mode, "github_current_head");
});

test("non-GitHub host always enters the Emergent deployment adapter", () => {
  const invocation = buildInvocation({
    environment: {},
    repositoryRoot: root,
    frontendDirectory: frontend,
    pythonBin: "/usr/bin/python",
  });
  assert.deepEqual(invocation, {
    mode: "emergent_cloud_adapter",
    command: "/usr/bin/python",
    args: [
      path.join(root, "scripts", "emergent_deployment_adapter.py"),
      "build",
    ],
    cwd: frontend,
  });
});

test("GitHub dispatcher fails closed on partial or mismatched identity", () => {
  const valid = {
    CI: "false",
    GITHUB_ACTIONS: "true",
    GITHUB_SHA: sha,
    GITHUB_WORKSPACE: root,
  };
  for (const [name, environment, gitHead, pattern] of [
    ["false actions marker", { GITHUB_ACTIONS: "false" }, () => sha, /exactly true/],
    ["SHA without actions", { GITHUB_SHA: sha }, () => sha, /exactly true/],
    ["workspace without actions", { GITHUB_WORKSPACE: root }, () => sha, /exactly true/],
    ["bad SHA", { ...valid, GITHUB_SHA: "short" }, () => sha, /full lowercase/],
    [
      "wrong workspace",
      { ...valid, GITHUB_WORKSPACE: `${root}-other` },
      () => sha,
      /repository root/,
    ],
    ["wrong checkout", valid, () => "b".repeat(40), /checkout differs/],
  ]) {
    assert.throws(
      () => buildInvocation({
        environment,
        repositoryRoot: root,
        frontendDirectory: frontend,
        gitHead,
        viteBin: () => "/vite.js",
      }),
      pattern,
      name,
    );
  }

  assert.throws(
    () => buildInvocation({
      environment: {
        ...valid,
        GITHUB_EVENT_NAME: "pull_request",
        GITHUB_EVENT_PATH: "/runner/event.json",
      },
      repositoryRoot: root,
      frontendDirectory: frontend,
      gitHead: () => sha,
      readEvent: () => ({ pull_request: { head: {} } }),
      viteBin: () => "/vite.js",
    }),
    /pull_request head SHA.*full lowercase/,
  );
});

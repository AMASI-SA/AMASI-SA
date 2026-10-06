# Isolated Linux exact-SHA test environment

CI-only branch: `codex/linux-mongo-test-environment`. No application PR is modified.
P1253 remains PERFORMANCE_VALIDATION_DEFERRED and is not used here.

## Execution contract

Ubuntu 24.04 GitHub-hosted job, Python 3.12 test container (exact patch and image
ID recorded), MongoDB 8.0.12 single-node real replica set `reviewtest` on 27018.
Pinned focused Python packages copied unchanged from the existing review CI;
these are a CI environment, not a replacement for application dependencies.

Mongo uses Docker `--network none`; tests share only its network namespace.
No host port is published. Tests receive only the loopback synthetic Mongo URI;
no GitHub token, host environment, production credentials, Docker socket or
Production API route is passed. Source is read-only. No application server or
startup/recovery command runs. Provider transports must be mocked by tests.
Dependencies/images are downloaded before network-isolated tests begin.

Each execution verifies a full 40-hex requested SHA against checked-out HEAD,
records TREE and tracked cleanliness, and validates explicit test paths.
Never use refs/pull/N/merge. For a Draft PR read its head.sha, then submit that
immutable value. If the PR moves, this run remains evidence only for its old SHA.

## Run without merging this workflow

Edit only `.github/ci/linux-mongo-request.json` on this CI branch with the full
approved source SHA, explicit backend test selectors and reviewed minimum count.
Push that CI-only request: the workflow runs on this diagnostic branch.
Do not push test requests before authorization to run the requested suites.
The checked-in initial request is only an environment smoke using provider and
G47 integration tests on a7977f4; it does not certify any later PR.

`workflow_dispatch` inputs and `workflow_call` are also defined. Dispatch UI/API
availability requires GitHub's workflow registration/default-branch conditions;
branch request pushes avoid requiring a Production merge. Reusable callers may
reference this workflow by its reviewed CI commit SHA. Non-push invocations
use harness commit `abb24232b8165c9dcc27d42fb2b9e85bfdde8cff`, not a moving branch;
push verification uses the exact push SHA. Both identities are recorded.
No `secrets: inherit`.
For #1270/#1272/#1273/#1274 and later PRs, select each PR's own reviewed suites;
this harness does not guess absent test files or silently skip unsupported tests.

## Evidence and failure

Artifact `linux-mongo-<tested SHA>-<attempt>` includes identity.json (HEAD/TREE,
OS, runner image, Python, Mongo version/digest, replica hello, test image ID),
harness-sha.txt, packages.txt, pytest.log, results.xml, zero-skip.json, mongo.log.
Always-upload preserves partial evidence. Missing XML, empty collection, below
minimum count, skip/xfail, failure/error, or nonzero pytest exit fails the gate.
No automatic reruns. No suppressions. Containers are removed after execution.
A network-dependent test fails; do not reconnect it to Production to make it pass.

Local gate tests cover valid, skipped, failed, error, empty, below-minimum and
missing report behavior. Full-suite success must come from a fresh Linux run,
not from these harness unit tests.

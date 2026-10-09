# Isolated current-Production integration verification

The user asked to continue completing Operational Balance without Production merge or release. This check executes tests against an exported comparison tree; it does not merge any branch or create a release candidate commit.

## Exact inputs

- Production Git: `128bfc1c2b4670d853a8bce33f15a667d2fdb120` (rechecked unchanged at completion).
- Operational task: `6fe7186b79e908e86bde40ac10e8f39cecb7b8fd`.
- Comparison tree: `35ccee1c37cfd5d91cf03c174377441d698dd0e8`.
- Merge-base: `a7977f4cfc1ef0a721fc25d783661332f32fe3b6`.
- `git merge-tree --write-tree --name-only` returned0/no textual conflicts. No merge commit or ref update was made. `git archive` exported4174 entries into `D:/codex-test-data/operational-integration-20261009/source`, outside all worktrees.

## Executed results

| Check | Result |
| --- | --- |
| All `backend/tests/test_operational_balance*.py` | 310 PASS, zero skip/error/failure |
| Operational Web tests plus navigation shell | 102 PASS / 8 suites, zero pending/failure |
| Runtime/auth/startup safety contracts from Runtime Stability workflow | 63 PASS, zero skip/error/failure |
| Web auth bootstrap/context/protected-route/customer-waiting tests | 22 PASS / 4 suites, zero pending/failure |
| Compile all Backend Python sources | PASS, exit0; no imports/server execution |
| Exported tracked source bytes after install/tests | Unchanged, including package.json/yarn.lock/requirements |

Total:497 passing cases. These are local isolated integration results, not GitHub checks on a merged commit. The previous task-branch CI evidence remains separately recorded in READINESS_20261009.md.

Backend used the existing focused test environments on Python3.13.15; this is not certification of the complete Production dependency environment. Production Motor/PyMongo versions match the focused CI environment. Real Mongo tests use loopback27316 and disposable randomly named test databases. Windows initially lacked an IANA timezone database in the minimal environment; `PYTHONTZPATH` was pointed at the already-installed tzdata2026.4 directory. The initial collection log is retained. No source/requirements change or package suppression was used.

Web used Node22.23.2 and Yarn1.22.22 with a fresh `yarn install --frozen-lockfile --non-interactive` inside the export. Production's updated dependency resolutions were preserved. No governed frontend build, Release Guard Prepare/Prepublish, Intent, artifact identity, lease, or deployment action was invoked. Backend compilation alone is not a full Release Readiness PASS.

## Evidence and boundary

Local evidence root: `D:/codex-test-data/operational-integration-20261009`. Files: `backend.xml/log`, `runtime.xml/log`, `frontend.json/log`, `auth-web.json/log`, `frontend-install.log`, `compile.log`, `verification.json`, and the exact `source.zip`. The manifest-byte comparison excludes newly generated dependency/cache/test-output files and checks every exported tracked file.

Operational financial behavior, API permissions and worker integration passed these suites on the combined source. This does not close formal Security1269 attestation, adopt ReleaseGuard1265, approve merging1263/1271, verify the deployed authentication environment, or authorize a release. Existing limits and emulator-only acceptance remain in READINESS_20261009.md. Native code/APK was not changed. Production writes=0; Production unchanged by this task.

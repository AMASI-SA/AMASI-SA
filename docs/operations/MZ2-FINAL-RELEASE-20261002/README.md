# Final software release â€” verification in progress

The owner explicitly authorized deploying the software after all technical
Release/Deployment gates pass, independently of financial go-live.
Full Business UAT stays **NOT PASS** until the later owner-input, Opening,
inventory, Activation and P08 gates. Deploy is not Business UAT acceptance;
Acceptance Smoke B is not Production financial acceptance.

## Source composition

- Production/base/rollback: `83363097d48e034dc7140a60c290efc684e1ffde`.
- PR1240 source A: `385867187bd6d6fcbb7048e8857816b973471440`.
- Original PR1240 intent B: `88cc9131027fd6783a46e2e00fc6aaec4788b8fa`, preserved.
- PR1241 transferred commit: `d5db39003d6590feb89821e7246c1b0b11083a66`.
- Initial combined source: `dac573ddd1a17a0943ec81d925b3a4a5bfda321f`.
- Current source after fixture-only integration fix: `57688c6423848165525bbc85265c4bb56e65da1c`.
- Current tree: `9f5a6f76a827e91602eee994343e5bb68d29f131`.
- Combined tree: `fb7a2cbce98ac57aaaa749a17450ca657a21aa8a`.
- Draft review: https://github.com/AMASI-SA/AMASI-SA/pull/1243.
- Branch: `codex/mz2-final-release-1240-1241-20261002`.

The operational commit applied without manual conflict resolution. Most of its
backend behavior was already in the integration; the new source delta comprises
two frontend runtime files, their two tests and the operational handoff document.
At initial composition all18 operational paths are accounted for:17 exact blobs, plus repository.py
retaining the full operational behavior and the existing financial delivery
snapshot/pinning methods. All Backend blobs exactly match PR1240.
See SOURCE-COMPOSITION.json and CHANGED-FILES.json.

No application behavior was reimplemented. Existing imported/native fee paths
already reject stale current-carrier evidence while preserving posted replay and
immutable history. Existing native current-carrier tests cover serialization.
No writer/contract/guard/write-control change is needed or made here.

## Verification contract

| Requirement | Evidence required | Current state |
|---|---|---|
| Source composition/accounting boundary | Exact path/blob comparison; no backend loss; current-carrier guard review | PASS source review; fresh runtime checks pending |
| Backend/domain/real Mongo/G47 | Previously declared147-file native baseline plus7 additional PR1241 operational files;154 total; disposable replica and standalone; zero failures/errors/skips | Prepared, not yet executed |
| Full frontend | Every discovered frontend suite including both transferred test files; no skipped/todo/failing tests | Prepared, not yet executed |
| Ordinary build | Compile final source with existing installed toolchain | Prepared; not a governed deploy artifact |
| Security/CodeQL/technical CI | Fresh exact-source GitHub workflows | Pending |
| SSOT | Preserved guards/static audit plus final-source runtime evidence | Pending final audit |
| Acceptance setup/public locks | Existing16-stage setup and unchanged409/423; exact-source evidence | Pending; never Full Business UAT |
| Smoke B | Real full-app auth, canonical pause,404/423 and whole-DB equality on isolated Acceptance | Prepared; never Production financial proof |
| Intent/provenance | Verified source artifact, fresh intent-only B, Aâ†’B invariant and fresh exact-B CI | NOT generated |
| Deployment | Shared /app fresh status, reviewed exact Production source, adapter rehearsal, prepare/prepublish, one publish and verify | NOT started |

This Backend selection is the established native integration regression plus the
operational PR's additional files. It is not a claim to execute every historical
test in the repository. The selection is explicit in BACKEND-SELECTION.json.

The old base Intent remains unchanged while reviewing source A. It does not bind
the new source and must not be used to deploy it. No lease is prepared at this stage.

## Isolated runner

`run_technical_verification.py` takes explicit immutable source, output directory,
Node and Mongo executables. It runs full frontend, ordinary compile, the selected
Backend regression and the existing full-app Acceptance Smoke B harness. No
assertions/product routes are replaced. Failures are retained without retry.

The runner uses fresh loopback-only Mongo8.0.12 on27141/27142, paths on D, an OS
environment allowlist, disabled dotenv, synthetic test credentials and no inherited
provider credentials. It retains HEAD/TREE, source manifests, commands, logs,
results and owned-process cleanup. Mongo data stays on disk for review; no existing
database is connected or dropped. Isolated tests do perform their specified local
financial/stock fixture writes; **Production financial writes remain0**.

The evidence branch and verification files are separate from the application
source and must not be merged into it. No financial information or credentials
are persisted in this documentation.

## Holds after software deployment

Opening Post, Inventory Initialization, Activation, P08 execution, Backfill,
financial schedules and Production financial writes remain unauthorized.
write-control,409,423,production_verified and financial writers are unchanged.
No automatic retry if the one authorized deployment attempt fails.
Stop after exact deployed identity and required health/smoke verification.

## B fixture correction after real CI failure

CI run37013964325 failed seven tests in the transferred shipping identity suite.
The same seven failures reproduced locally under react-scripts. Its existing
resetMocks=true erases the factory printer mock implementation before each case.
A two-line test-only change restores its intended true return in beforeEach.
All original assertions and application code are unchanged. Focused three-suite
rerun:28PASS,0failures/pending. Raw before/after JSON/log/commands and the failed
CI log are retained under fixture-regression. This is the one test-blob delta
from PR1241; all its runtime behavior is preserved. No global reset setting was changed.

Independent read-only runner review required mandatory evidence predicates and
failure-safe fixture cleanup; those corrections were made before launch and
re-reviewed. No outer timeout kills only a harness parent; CPJ owns cancellation.

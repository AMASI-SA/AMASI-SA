# Independent review and CI coverage

Review date: 2026-11-26. Reviewer: `audit_evening_release`, independently of
the option, payment and archive implementation owners. This note records source,
test, AST and saved-evidence review. It is not a claim that the forthcoming
source A, intent B or deployment D has already passed CI or Production acceptance.
The parent owns the final source checkpoint and release.

## Review conclusions

**No blocking finding remains in the reviewed option authority, payment read
view or archive checkpoint changes.**

- **Option authority:** static AST comparison against the pre-extraction source
  confirmed exact preservation of the catalog construction body, preceding
  per-line admission and following token resolution. Only successful catalog
  identities/bound labels are reused within one source attempt. Amounts,
  quantities, currency, selected bindings and line failures are still evaluated
  normally. Final mutation validation is outside per-order recovery; normal exit
  requires validation, and exceptional/cancelled exit closes and clears the
  scope after owned preparation joins.
- **Native metadata:** the initial full-row observer would fall back for ordinary
  Mongo ObjectId/datetime metadata. This effectiveness defect was corrected.
  Exact ObjectId byte references are captured and rechecked, including in-place
  reinitialization. Exact datetime supports naive and fixed standard-library
  timezone values; subclasses and custom time zones retain fallback. Metadata
  observation does not convert IDs or interpret money.
- **Payment read view:** the public snapshot retains full independent copying.
  Mechanical AST comparison confirmed unchanged admission/overlay statements
  after substituting the injected copier. The private collector opt-in borrows
  only items/customer/shipping/shipments, which the downstream payment normalizer
  and fee-rail reader do not inspect. Other financial roots use one copy memo,
  preserving aliases and root cycles. Unsupported financial graph shapes fall
  back to the original full copy. The private view does not escape in normalized
  financial output.
- **Archive boundaries:** only the immediately duplicated admission checkpoint
  at each shared eight-page boundary was removed. The preceding completion
  checkpoint is also the next group's admission, with no intervening read,
  await or processing. Fresh first/every-eight/final authorization and lease
  renewal, per-page validation, cancellation joining, exact histories and
  four-page prefetch limits remain. Denial after page 8/16 still prevents 9/17.

The parent separately reviewed the copier's final `try/finally` and seven
weakref cases. This reviewer did not independently re-run or re-review that
implementation. It changes only the lifetime of the private recursive closure,
not additional copy call sites; see `report_copy_lifetime_regression.md`.

## Review versus execution evidence

No test or benchmark was re-run for this final independent review or CI audit.

| Area | Execution evidence and scope |
| --- | --- |
| Option authority | Author reports 176 passing cases in 3.75 s: 63 new and 113 existing, following red controls for repeated preparation, native metadata readiness, ObjectId mutation and custom time zones. Reviewer read all 63 new cases and checked the final source hashes. |
| Payment view | Parent reports 45 focused passing cases. Reviewer read the cases and traced all downstream readers. The broader parent run reported 430 passes plus 184 subtests and four failures caused by the unavailable local Mongo fixture; those failures are not integration passes. |
| Archive boundary | Reviewer inspected saved red/green JUnit: red 32 cases with 16 intended failures; green 119 passes, no failures/errors/skips. Eleven real-Mongo/socket cases were explicitly deselected locally and remain required in CI. |
| Copier lifetime | Author evidence records seven new cases (red: five failures/two passes), then 74 passing new/existing cases, zero skips/deselections. Parent performed the independent implementation review. |

The selected actual-candidate source benchmark exercises the real option/payment
opt-ins, includes native Mongo metadata and asserts 128 cached products plus full
output/query/input/source parity. Its synthetic timing is separate evidence, not
a substitute for the real-Mongo gates or a successful live saved calculation.

## CI selection and unchanged hard gates

The payment test was renamed from `test_operational_payment_read_view.py` to
`test_operational_balance_payment_read_view.py`. Its contents and SHA256 are
unchanged. No workflow change is needed or made.

| New test module | Expected focused cases | Existing CI selection |
| --- | ---: | --- |
| `test_operational_balance_payment_read_view` | 45 | Both operational globs |
| `test_operational_balance_option_authority` | 63 | Both operational globs |
| `test_operational_balance_report_copy_lifetime` | 7 | Both operational globs |
| `test_operational_balance_archive_checkpoint_boundaries` | 32 | Both operational globs |

`.github/workflows/operational-balance-web.yml` checks out the explicit PR head
SHA, asserts and records the exact source/tree, and passes
`backend/tests/test_operational_balance*.py` to pytest (line 69). Its JUnit gate
requires at least 3,500 cases, retains existing suite-specific requirements, and
rejects every skipped, failed or errored case (lines 143-252).

`.github/workflows/operational-stock-eligibility.yml` also checks out the explicit
PR head and passes `tests/test_operational_balance*.py` to pytest (line 75).
Its always-run JUnit gate derives required module names from that same filesystem
glob and requires every module to have executed cases, with zero skips, failures
or errors (lines 81-101). The four new modules now statically match both globs.
The gates are inline workflow checks, not an additional pytest allowlist plugin;
both test commands use `--noconftest`. The separate dashboard adoption plugin is
not involved in these operational runs.

Exact A and B artifacts must subsequently confirm the four module counts and
the four named integration cases below. Static selection review is not execution
proof, and prior-release green artifacts must not be reused for the new source.

## Four locally unavailable Mongo cases

Both complete files match the operational glob; none of these functions has a
skip decorator or a mock/fallback branch:

- `test_operational_balance_split_tenders.py::test_actual_provider_payout_allocates_to_one_split_share_only_and_replay_is_idempotent`
- `test_operational_balance_split_tenders.py::test_frozen_split_order_does_not_turn_into_a_second_bank_receipt_or_cod_collection`
- `test_operational_balance_bank_tender_proof.py::test_replayed_refresh_of_bank_and_bnpl_waits_for_original_bank_leg_date`
- `test_operational_balance_bank_tender_proof.py::test_existing_provider_share_can_coexist_only_when_proved_same_payment`

Each calls `test_operational_balance_integration.run` (lines 26-48). That helper
asserts a loopback-only URI, constructs an actual `AsyncIOMotorClient`, pings
Mongo, creates a UUID-named isolated database, runs the actual scenario, then
drops only that database and closes the client. Unavailable Mongo raises rather
than silently skipping. The scenarios use the actual refresh/store/movement
paths and test original dates, split-share allocation and replay behavior.

Web provisions Mongo 8.0.12 as replica set `operationalprojection` on loopback
27305 and sets `OPERATIONAL_TEST_MONGO_URI` explicitly. Stock provisions Mongo
8.0.12 replica set `operationalphysical` on loopback 27462 and sets the same
environment variable for its fixture. Both initiate the replica set and require
a writable primary before testing. Both preserve the strict JUnit rejection
above. Consequently these four cases must execute and pass on both A/B jobs;
the parent's absent localhost:27305 fixture cannot waive them.

## Deployment D policy

Web and Stock run for the relevant PR head A/B. Their current triggers do not
automatically run those suites again on a push to the production branch.
`AGENTS.md`'s reviewed two-commit handoff expressly permits B or a tree-equivalent
merge commit as `deployment_git_sha`; it does not require a duplicate Web/Stock
dispatch on D. Preserve the existing policy: prove D's tree equals reviewed B's,
retain exact A/B passing evidence, and require the applicable exact-D push
workflows. `mezan-production-release.yml` runs Release Readiness on the production
push; Security Gate and CodeQL also have production-push triggers. The governed
original-host/release-identity checks and three consecutive Production probes
remain required. Do not describe A/B operational tests as if re-executed on D.

## Reviewed SHA256 identities

Paths below are repository-relative. These hashes identify the reviewed files,
independently of the final commit SHA the parent will freeze.

| File | SHA256 |
| --- | --- |
| `backend/operational_option_authority.py` | `41cfcf5720ff1a08069b7502f3de24198ecedc7adc25862555b66ae5680e144d` |
| `backend/operational_balance_sources.py` | `f8f8046218005e485bea1985dd4ad2b99c3dfc53f2a4f518c8aad981ef158200` |
| `backend/tests/test_operational_balance_option_authority.py` | `f8b9441a3c2116a72de419e649dc84af7b33af9957108a73f0a5155b5c209a3d` |
| `backend/operational_payment_read_view.py` | `4015ff278b26319cc85b87f758a9bc8850c709bbdc0596fcf73f8a831a8a409d` |
| `backend/salla_payment_collection.py` | `25d4e09a0f2b2ce0b0c4e3a1900d723ce0d191f5efe75400c67e786f8b719e32` |
| `backend/operational_provider_transaction_evidence.py` | `3371f3bc0ea06c9a40048866b128c654e2349e00b1af8c0d142b1c99a7d39b38` |
| `backend/tests/test_operational_balance_payment_read_view.py` | `6518e26f848fdd1d3032c1973d5cb4bdf42e63cd31d1394c75d0eacbd5eb4905` |
| `backend/operational_audit_prefetch.py` | `621951659b007b7ccb0e69cb1caa9f12ed6105e9db4288cfe4b4464a8ad6f2b3` |
| `backend/operational_balance_archive.py` | `40ffa6f7aa481d4bd24d63ff34ba7cb086a1b81e23d83131b430abe8518c65b6` |
| `backend/tests/test_operational_balance_archive_checkpoint_boundaries.py` | `4d9851ba0b1f054adb64b07e0c8c8891eabb13dd8ca5ee35f3b2921d0e45d92c` |
| `backend/operational_report_copy.py` (parent review) | `5a67158416030aa0ffec203f53d4c675c1cf1aaa612ca7d4e9afd538fcc4b990` |
| `backend/tests/test_operational_balance_report_copy_lifetime.py` (parent review) | `a435fd0ac23eee16d8aeb73b93d421c76fbc4088af8b82c5f8078c7a069d916a` |
| `.github/workflows/operational-balance-web.yml` | `309b8af8aacaef3e0a197e40c18bd44809d858556b038f2de82918edad262220` |
| `.github/workflows/operational-stock-eligibility.yml` | `06eeb4a430e2e16f4f4c513fb73d13ee2bc2d8e33668b20bd2dd439e8892a9ac` |
| `.github/workflows/mezan-production-release.yml` | `68ec9e4760b56a40d36aacc82f4ff3842b690d28b4c80b61e5b7164671f8306c` |

No runtime, workflow, test semantics, host, database or production action was
performed by this CI audit. Only this evidence document was written.

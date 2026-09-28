# P02 / PR #1130 — V4 isolated review patch

Status: CHANGES_REQUIRED_NOT_READY_TO_APPLY until independent V4 review.
Base: 5292a87a476a140ae8c3c78e88dfba7d8c83f035
Head: 20400fffb03594af8a38d6b4750c170233bd5f37
Prerequisite: the previously verified three-file CONTRACT_SLICE, still uncommitted.
V4 REPLACES the unapplied V3 proposal. Do not apply V3 first.

## Scope and explicit boundaries

The central accounting.shipping.contracts.review key is owner-approved, but
requires explicit assignment for every user, including an owner. It is not an
alias for management or posting. No user record is granted or migrated here.

The current accounting_shipping_p02.py, prepare_courier_fee, post_courier_fee,
PUT /rates, UI, ledger and report producer are NOT modified. No existing entry
point imports, calls or registers the new service. No new endpoint is registered.

P01 remains IN_PROGRESS; P02 and P03 remain LOCKED. No Preview/Production
data, advertising account, advertising wallet or Snapchat change is authorized.

## Runtime rejection is the first executable statement

Both prepare_contract_courier and post_contract_courier now begin with:

    require_native_contract_runtime()

The real closed gate raises HTTP 423 / shipping_contract_native_path_locked.
This occurs before actor access, the preview call, input validation, order
evidence reads, courier lookup, original COD validation, Mongo, the evidence
service, the owner transaction, journal entries and audit writes.
There is no owner, payload, environment or database flag to open this gate.
The source-controlled closed gate itself is unchanged from V3.

save_contract_draft and approve_contract retain their existing first-statement
gate. The dormant route installer retains its no-registration behavior.

The business rejection cod_base_receivable_required is preserved BELOW this
closed runtime boundary. Outer entrypoints must not read orders just to choose
that error while closed. The inner guard still rejects missing original COD
recognition, and first external COD sale remains an unimplemented dependency.
No sale, output VAT, fabricated receivable or suspense account is added here.

## Decimal-only leg validation

_leg requires a finite, strictly positive Decimal. It never calls float or
round, and does not coerce strings, integers or binary numbers into money.
It quantizes to Decimal("0.01") in a dedicated Decimal Context with sufficient
precision for the input coefficient and integer digits. The exact input must
equal the quantized amount. Nonzero sub-halala values are rejected rather than
silently rounded; redundant trailing zeroes are accepted.

The contract calculator remains responsible for contractual HALF_UP rounding.
Already rounded calculator results are accepted unchanged. Tests specify
ordinary halalas, large exact values beyond ordinary Decimal context precision,
large fractional-halalas, rounding midpoints, zero/negative/nonfinite inputs,
explicit rejection of non-Decimal inputs, a Decimal subclass that forbids
__float__, and independence from the caller's precision/rounding/traps.

This proves nothing about the existing ledger's eventual number representation.
The ledger/report bridge is NOT_CONNECTED and cannot be reached through the
closed native entrypoints. End-to-end large-value posting needs separate
review when that bridge is implemented. No ledger file is edited to mask this.

## Evidence and journal boundaries

Production evidence service: NOT_INTEGRATED. The resolver remains fail-closed.
No synthetic evidence adapter is included in this patch or in production code.
The dormant snapshot/retention/link functions do not establish completed
production approval/deletion/revocation integration.

Native ledger/report integration: NOT_CONNECTED. shipping_cod_reclass is not
added to current ENTRY_TYPES or report producers. Existing shipping and source
deletion handlers are unchanged. There is no claim of a complete COD lifecycle.

The previously reviewed illustrative matrix is unchanged:
debit COD 1000 and credit proven original COD 1000;
debit shipping 15 and shipping input VAT 2.25;
debit commission 10.43 and contained input VAT 1.57;
credit actual courier payable 29.25.
Debit and credit each total 1029.25, with no sale/output VAT/bank leg.

## Authored tests, not run

backend/tests/test_mz2_shipping_contract_isolation.py contains:
- 12 preserved central-permission/readiness tests.
- 9 entrypoint/no-access tests exercising the REAL closed gate.
- 11 Decimal-only leg validation tests.
Total in this file: 32.

backend/tests/test_mz2_shipping_payment_evidence.py retains the 13 V3 tests
against the existing producer normalizer. Total carried by this patch: 45.
The 43 prior CONTRACT_SLICE/calculator/legacy cases are untouched.
Static combined count for those named groups is 88, NOT a PASS result and NOT
a count of the full repository or a complete integration acceptance suite.

No candidate import, unit test, Backend/Frontend, Real Mongo, HTTP, CI or UAT
run is authorized at this review stage. Dependency observers in isolation
tests assert NO CALL; they do not substitute an evidence authority or execute
a fake ledger/transaction. Positive database/rollback/integration testing
remains a separately reviewed scope.

## Patch separation and authorized check

PR #1130 patch contains 8 paths: the registry and 7 new files. The separate
#1126 test revision correction is NOT embedded in this patch, source bundle
or check-only runner. No existing P02 test or activation gate is edited here.

The companion runner embeds ONLY this #1130 patch and verifies the actual
Emergent worktree, local branch, HEAD, Base/merge-base, origin identity and all
three CONTRACT_SLICE file hashes. It runs only git apply --check, --stat and
--numstat (never actual application), using patch bytes on standard input.
It prints HEAD/branch/status before and after and compares content manifests
for tracked and non-ignored untracked files plus the Git index. Ignored runtime
outputs are explicitly outside the manifest. It writes no source, index, patch,
log, commit or ref and creates no worktree.

Only results produced in the ORIGINAL Emergent terminal qualify as target
apply-check evidence. A check in another container or a file reconstruction
must not be described as a target-worktree PASS.

Application, tests, Commit, Push, PR edits, restack/merge, deployment, phase
activation and Preview/Production writes require a new explicit authorization.

APPLIED=false
TESTS=NOT_RUN
REAL_MONGO=NOT_RUN
CI=NOT_RUN
UAT=NOT_RUN
P01=IN_PROGRESS
P02=LOCKED
P03=LOCKED
COMMIT=NONE
PUSH=NONE

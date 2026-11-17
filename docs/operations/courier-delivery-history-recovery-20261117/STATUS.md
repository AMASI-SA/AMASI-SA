# Courier delivery history recovery — 2026-11-17

## WIP: implementation checkpoint awaiting integrated verification

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/courier-delivery-history-recovery-20261117`.
- Verified production source base: `8df479ef24b4f8847c26ba18d48e3248f7b673cf`.
- Scope: visible-request courier evidence acquisition, fair bounded progress,
  and explicit matching of an existing pending order. Preserve delivery proof,
  source version, owner, waybill, cutover and financial guards.
- Production changed: **no**. No release lease, merge or deployment performed.
- Remote RED checkpoint: `3e42238ccfcd42eddf5890f09357ac68d04f3431`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1967 .
- Implementation checkpoint: `7116da02320807bb6f7dc4c5dd5a0028811ce43e`
  (tree `b6b32fd269acf7f1cdf9645396a0283df5494ef1`).

## Evidence

The existing native history verifier accepts a synthetic reproduction with a
single original delivered shipment, no shipment delivery timestamp, and a
complete same-carrier dispatch/delivery history. No date parser or proof guard
change is justified by that reproduction.

New synthetic regressions in
`backend/tests/test_operational_balance_courier_acquisition_progress.py` reproduce:

1. An expired invalid prefix repeatedly consumes all four selection slots and
   prevents a later never-attempted delivery from being inspected.
2. Due candidates are chosen in insertion order rather than oldest retry order.
3. One native transport timeout abandons the remaining bounded order allowance.
4. A hanging bank adapter consumes the complete visible acquisition budget and
   prevents courier acquisition from starting.

Root independently ran the following against unchanged production source:

```bash
PYTHONPATH=backend:backend/tests PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  /tmp/mezan-courier-test-venv/bin/python -m pytest --noconftest \
  -p no:cacheprovider -q --tb=line \
  backend/tests/test_operational_balance_courier_acquisition_progress.py
```

Result: **4 failed in 1.99 seconds, exit 1**, at the four intended assertions.
The tests use in-memory Mongo emulation and mocked provider reads; they are not
real-Mongo or production evidence. Existing waybill acquisition baseline:
**14 passed in 1.62 seconds**.

## Implementation and verification

Implemented fair atomic claim ordering (never attempted, then oldest due),
per-order native transport failure isolation, reserved time for each remaining
source inside the unchanged twenty-five-second visible budget, and truthful
verified/saved/unchanged/blocked/conflicted/unavailable outcomes. Source writes
still require the complete existing compare-and-set guard. No delivery proof
acceptance rule was relaxed.

The visible refresh accepts an optional courier order target only when its
current session scope matches and the owner's saved courier issues contain it.
It inspects only that order, obeys the fifteen-minute cooldown, skips unrelated
native sources, and then invokes the shared aggregate once. The frontend adds
an explicit per-order matching action, aborts on close/hide/scope change, and
keeps explanatory results below the pending-order table.

Focused backend verification, including targeted selection, owner isolation,
CAS receipts, cancellation and existing confirmation/waybill behavior:
**66 passed in 3.65 seconds, exit 0**. Final isolated targeted rerun:
**21 passed in 1.63 seconds, exit 0**. These tests use mocked external reads.

The oldest-due and expired-prefix regressions now require genuine Mongo using
the existing isolated integration fixture. Mongomock 4.3.0 has a confirmed
sorted find-and-modify defect when projection omits `_id`: it returns the
sorted document but updates the first unsorted match. Production projection
and claim semantics were not changed to accommodate that emulator defect.

Local real-Mongo verification is blocked by the execution runtime: the
official Debian MongoDB 8.0.12 binary (verified SHA-256
`6afc35ae5623b302deeaeace63981f685d775d8b1bcd2dbe8d4077f9f21267c8`)
aborts before startup because its allocator requires an unavailable sysfs CPU
file. No test server was started. Run these non-skipping regressions in the
existing CI Docker replica set; do not patch the binary or fake sysfs.

The governed Node v22.23.2 / Yarn v1.22.22 toolchain is verified locally.
At exact implementation SHA `7116da02320807bb6f7dc4c5dd5a0028811ce43e`,
Operational Balance Web run `44140834654` passed frontend contracts:
**996 tests / 62 suites, zero failures, pending or skipped tests**. The changed
four suites account for 184 tests. Its browser/mobile/visibility acceptance
passed fourteen operational scenarios and eight simple-balances scenarios;
canonical worker exclusion passed. The genuine Mongo replica startup and
exact-source recording passed; backend results are still being collected.

Independent backend review found no authority, cancellation, proof-guard or
CAS regression. Independent frontend review found one concrete export overlap
issue: a cancelled export could leave export buttons busy when a targeted
match subsequently cancelled or failed without replacing the snapshot. The
new source checkpoint explicitly clears export presentation state when it
revokes that export, with four regression cases (page/full export crossed with
match cancellation/failure). This follow-up needs fresh exact-head frontend
CI; the earlier green result does not cover it. No other blocking review
finding was reported.

## Implementation ownership and next action

Root owns backend production changes. A test-only agent authored the new
regressions. A separate frontend agent owns the explicit per-order matching
control and focused frontend verification. No shared production file has
multiple writers.

Next: preserve this implementation checkpoint and trigger CI, complete the
four focused frontend suites, review the integrated diff independently, and
require the real-Mongo ordering regressions to pass. Only then prepare a
governed source/intent release under the existing production protocol.

Preserve the four-order cap, per-order fifteen-minute cooldown, cancellation,
authorization and compare-and-set guards. No background recovery, manual
delivery assertion, financial mutation shortcut, or loosened shipment matching.

## Continuity

Issue #1006 has reached its comment limit. Do not overwrite its historical
content or repeatedly attempt an append. This task status and the forthcoming
task PR serve as the continuation record, with exact pushed checkpoint SHAs in
the PR description. The draft PR's initial RED checkpoint CI is not evidence
for this implementation; assess the new pushed implementation SHA explicitly.

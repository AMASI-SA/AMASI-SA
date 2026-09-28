# Fail-closed predeploy control candidate — 2026-09-28

## Authorized source inputs

- Production base / rollback target: `a7bcb1626e2ad4defba2260d4a113417f91f3120`.
- Accepted source A4: `8492bfd0a99662dca09cef41a751328e0128eced`.
- Superseded candidate B4 / Draft PR #1177: `ae97e850ca2c76074c8a9cc9674c26e4d05560ad`.
- Frozen #1131: `6f777f22c06fc9a7a96f131b7a8b3fd0aaf67a9c`.
- Frozen #1157 / G47: `f2431282c3728a8d2fe618dbbe7a8616196f068d`.

The initial staged source tree was verified byte-for-byte equal to A4 before this
patch. A5 retains the Production base intent; B5 must replace it with a newly
generated protocol-v5 intent from the final A5 source. The old B4 intent is not
a release input.

## Incremental behavior

Only two runtime modules change relative to A4:
- `backend/accounting_write_control.py`: a missing owner row or absent
  `writes_paused` field reads as paused, with initial control revision zero.
- `backend/accounting_atomic.py`: only the existing explicit control path can
  bootstrap the permanent coordination row. An ordinary transaction requires
  the stored value to be exactly false before invoking its callback; rejection
  aborts its transaction and returns HTTP 423 / `mz2_writes_paused`.

Existing transaction callers, nested transaction semantics, writer transition,
G47 business logic, tax/cutover policy and accounting calculations are unchanged.
There is no startup, build, deployment or migration write. This changes the
default of the existing MZ2 barrier; it does not expand its caller coverage.

The existing owner control API can explicitly resume an unconfigured owner
using revision zero and a nonempty reason. It commits control revision one and
an audit with previous_paused=true. Viewer/accountant actors cannot resume.

## Verification contract

Seven new real-replica-set acceptance tests in `test_mz2_write_control.py`
cover missing row/field GET, callback rejection without financial or
product/component inventory effects, owner bootstrap/resume/audit, ordinary
work after resume, unauthorized resume, and durable sale/refund evidence
before idempotent replay. Together with the six existing tests, the control
suite executes 13 tests. The new tests failed against A4 defaults before the
patch and passed after the patch.

Existing successful-write regression fixtures now explicitly initialize their
synthetic owner as unpaused at control revision zero. This is test setup only;
it is not a runtime initializer. The missing-row tests remove only their unique
local fixture row. The missing unrelated-tenant expectation now asserts paused.

All tests use disposable loopback MongoDB fixtures or GitHub-hosted test
containers, never Preview or Production MongoDB. Final CI status and exact
A5/B5 identities are recorded in the Draft PR and Issue #1006; this source
record alone is not a declaration that all mandatory gates passed.

Required final B5 gates: MZ2 Accounting Module, G47 Focused Integration,
Fulfillment V2, Qoyod Payment Freshness, Security Gate, CodeQL Python and
JavaScript/TypeScript, frontend build, Release Readiness, and the executed
Host Node20 clean-clone rehearsal. Mandatory skipped checks do not pass.

## Operational boundary

No new Production DB access, no live DB write, no pause/resume, no transition or
financial activation, no opening balance/inventory action, no release lease,
and no Merge/Deploy/Publish are authorized by this source patch.
Review B5 before any separately authorized deployment.

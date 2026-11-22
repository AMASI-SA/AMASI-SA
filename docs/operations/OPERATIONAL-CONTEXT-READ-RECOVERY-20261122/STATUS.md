# OPERATIONAL-CONTEXT-READ-RECOVERY-20261122

## Scope and state

Local implementation and focused verification complete on
`codex/operational-context-read-recovery-20261122`,
based on published merge M3 `02e8be41e6f1972806f0e095b8fdc38645a7f113` in
`AMASI-SA/AMASI-SA`. Root owns remote checkpoints, CI, release review and any
later publication. Independent source and test review passed at the hashes below;
the remote source checkpoint is pending. This follow-up has not changed Production. Root separately completed
M3 publication, observed **3/3 original live verification probes pass** and a
fresh inactive guard, and preserved that completed evidence at
`4ee3d64159cba4edd20133f81ec938cf33068ae8` on
`codex/operational-workspace-release-evidence-20261122`. Those observations verify
M3's deployment identity, not this new correction or the inventory loading issue.

The observed inventory permission bootstrap failed twice after M3 publication.
The latest retained runtime logs show other slow requests and health upstream
timeouts, but no matching inventory/context timing. The live cause is therefore
unproved. Source review found that existing `GET /api/operational-balances/context`
loads complete historical audit pages even though its response only needs current
status, start time, openings count, current issues and separately checked access.

The implemented correction reuses `read_financial_position_state` only inside this
GET handler, leaving the original router gates, permission checks, session digest,
bank assignments, JSON response fields and all full mutation/audit readers intact.
Only its documented current-snapshot HTTP 503 is translated to the context
endpoint's pre-existing audit-integrity error payload; unrelated errors propagate.

Current snapshot/issue packets and audit-manifest metadata remain validated.
Historical audit page contents are intentionally not materialized for context;
the full store reader continues to validate them for audits and mutations.

## Owned files

- `backend/operational_balance_routes.py`: existing GET `/context` reader boundary only.
- `backend/tests/test_operational_context_snapshot_reader.py`: isolated actual-router contracts.
- This status document.

Root additionally owns `.github/workflows/operational-balance-web.yml`: it now
selects the new regression module and requires at least **21 executed cases** in
the existing no-skips/completeness gate. Existing thresholds are unchanged.

No frontend, grants, financial calculations, mutation behavior, protocol/guard,
frozen source, generated outputs or host state is in this change's write scope.

## Verification

Tests use synthetic data through the actual ASGI router and a read-only database
double that honors the current reader's exclusion projection. Unsupported database
operations raise; no real API/database writes are performed. The mobile-owner
case replaces only the unrelated bank directory, preserving `assigned_banks`,
the native permission policy, actor resolution and route behavior.

Environment: existing isolated Python **3.12.14** environment at
`/workspace/scratch/78a3f53e49a4/payment-setup-venv`; no dependency changes.
Commands ran from the task worktree root, with bytecode and pytest cache disabled.

### Red on unchanged M3

Before modifying `backend/operational_balance_routes.py`:

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/78a3f53e49a4/payment-setup-venv/bin/python -B -m pytest \
  --noconftest -q backend/tests/test_operational_context_snapshot_reader.py \
  --tb=short -p no:cacheprovider
```

Result: **11 failed, 10 passed in 5.83 s**, exit **1**. The six inline/packed
draft/active/frozen cases and the mobile-owner case returned the correct current
context but still read all **three** synthetic historical audit pages. The two
missing/changed historical-page cases returned 503 instead of the intended
current-context response. Two boundary-seam cases also showed that M3 did not
use the selected current-state reader. This reproduces the unnecessary read
mechanism; it does not reproduce or prove the cause of the live timeout.

An initial invocation omitted `backend/tests` from `PYTHONPATH` and failed
collection; it was corrected before the semantic red run. The first mapping
implementation exposed local `HTTPException` import shadowing in five cases;
using a separate local exception alias fixed it without changing the existing
permission loop. Neither intermediate failure was treated as acceptance.

### Green and focused neighboring regressions

The exact red command after the route change: **21 passed in 6.28 s**, exit **0**.

```bash
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 \
  /workspace/scratch/78a3f53e49a4/payment-setup-venv/bin/python -B -m pytest \
  --noconftest -q \
  backend/tests/test_operational_context_snapshot_reader.py \
  backend/tests/test_operational_financial_position_state.py \
  backend/tests/test_operational_balance_financial_position_routes.py \
  backend/tests/test_operational_balance_supplier_workspace.py \
  --tb=short -p no:cacheprovider
```

Result: **71 passed in 13.23 s**, exit **0**. The 21 new cases are included in
that 71, not additional unique tests.

The new contracts prove:

- Complete successful JSON bytes match the unchanged full-reader reference for
  inline and packed current state in draft, active and frozen statuses.
- The absent-state draft response, nonempty opening count, restored issues,
  session digest, owner flags, native permission mask and assigned banks remain
  unchanged.
- Context reads zero historical pages and excludes top-level write histories
  from transfer; synthetic stored data remains unchanged.
- Missing or changed historical page bodies no longer block current context;
  the unchanged full store reader still rejects those histories with HTTP 503.
- Corrupt financial packets, issue packets and audit manifests preserve the
  exact original context 503 response bytes and do not return permission data.
- Anonymous, disabled, employee, owner-shaped mobile employee and revoked-page
  principals cannot read state. Identity revocation after the router gate is
  checked again before the state read.
- Unrelated HTTP 403 and HTTP 503 failures retain their original response.

`git diff --check` passed. An AST comparison against M3 also confirmed that the
initial actor scope, entire permission/response tail, and entire module outside
this GET context reader body remain unchanged.

### Reviewed source bytes

| File | SHA-256 |
|---|---|
| `backend/operational_balance_routes.py` | `092fd7915285a2f654400631b3f70001e8b9eb11d205565fa2dae3650b50f14b` |
| `backend/tests/test_operational_context_snapshot_reader.py` | `fa4f9d765197ad062a2c753f52ce2453abdd516fd4cc38cfe4c561131d2c697d` |

## Limits and incomplete work

The reader still loads the remaining current aggregate and decodes financial and
issue packets; this is not a claim that context memory or latency is now bounded
by a small header. Native stocktake, physical inventory, later catalogs, payment
writes and broader server pressure are unchanged. Current financial/header and
archive-manifest validation uses the established stricter report reader; only
historical page-body verification is intentionally deferred to full readers.

This focused verification is not live acceptance or final exact-head release CI.
The production request failure remains undiagnosed without matching response/log
evidence. Root has registered the new **21-case** file in the explicit operational
workflow selection and its executed-test gate; this author did not edit CI.

## Next safe action

Root will preserve these independently reviewed source/test/docs bytes on the
dedicated remote branch and record the full source SHA and PR in the release
evidence. No Issue #1006 comment is posted without explicit messaging authorization.
No staging, commit, ref or remote write has been done
by this implementation agent. Root must follow the unchanged new-source A/B
protocol and final exact-B gates before any future publication. Do not alter
frozen M3 or shared host outputs. M3's completed release lease must not be reused.

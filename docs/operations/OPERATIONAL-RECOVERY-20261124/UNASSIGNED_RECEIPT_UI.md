# Recovery receipt groups without an assigned entity

## Defect and scope

The deployed PR #2101 source can produce a valid, complete recovery receipt
containing unallocated supplier forecasts. The financial engine deliberately
uses `party_id: null` when no supplier receipt proves a supplier assignment;
the recovery grouping retains that value as `entity_id: null`. The receipt UI
previously required every group identity to be nonempty text, so one such
group rejected the entire receipt and hid its download control.

This follow-up starts from production merge
`0ffa962d3cfda3eb49b5750c7bdc2f932a645d4d` in an isolated worktree. It changes
only the receipt UI, its focused tests, and this continuity note. The frozen
source/intent pair and the existing production deployment remain intact.

## Behavior

- Receipt groups accept an explicit JSON `null` as an unassigned identity.
  Missing, empty, oversized and non-string non-null identities still fail
  validation. The separate named-entity lookup still requires a real ID.
- Unassigned rows display a clear label and the receipt shows a warning that
  they cannot be attributed to a particular account.
- The download preserves the complete original validated JSON, including
  groups outside the visible page, all four comparison sections and their
  null identities. The UI never substitutes an ID or modifies source data.
- Owner/scope checks, read-only request behavior, dates, values, preservation
  evidence, duplicate diagnostics and source-completeness warnings retain
  their existing contracts.

## Verification

On exact production source plus the new tests, the two positive regressions
failed and the other 23 proof tests passed. Both failures reproduced the
missing table/export caused by rejecting the valid unassigned group.

After the narrow component change, all 39 cases across these three suites
passed on governed Node 22.23.2 and Yarn 1.22.22:

```bash
CI=true PYTHONDONTWRITEBYTECODE=1 python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watch=false --runInBand --resetMocks=false --cache=false \
  --runTestsByPath src/pages/operational/BalanceRecoveryProof.test.jsx \
  src/pages/operational/BalanceFreshnessNotice.test.jsx \
  src/pages/operational/BalanceAutomationSettings.test.jsx
```

The proof suite verifies the unallocated forecast display, rejection of
malformed identities, strict named-entity metadata, unchanged source objects
and exact all-sections JSON export beyond the visible page. `git diff --check`
passed. A preliminary direct relative executable command failed because the
toolchain helper executes from the repository root; using the documented Yarn
frontend working directory resolved that runner invocation issue.

An independent reviewer found no blocking issue and separately reran all 25
proof UI/export cases with the same pinned toolchain: all passed, exit 0.

The root coordinator owns remote preservation, integrated final-head CI, a
new reviewed release intent, guarded publication and direct live verification.
This component checkpoint does not itself publish or run financial recovery.

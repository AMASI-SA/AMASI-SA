# Snapchat management settings canonical sync repair

Task: SNAP-SETTINGS-CANONICAL-20261108

Repository: AMASI-SA/AMASI-SA
Branch: codex/snap-bid-settings-20261108
Target: hotfix/prod-snap-meta-final

## User goal

Through Mezan, reduce target conversion cost by 15% for active USD Snapchat
ad squads whose observed cost per purchase exceeds USD 30. Re-read performance
and exact current provider bid settings before execution. Never derive a target
bid from observed CPA or change an automatic bidding strategy to satisfy the goal.

## Diagnosis

The dashboard refresh uses SnapchatV2SyncPipeline and writes canonical entity
facts. Management settings previously read only the older native entity catalogue.
Refreshing therefore left the management snapshot stale. The canonical client
also omitted bid strategy, bid amount, and provider identity from its raw settings
allowlist, so changing the reader alone would not safely enable management.

## Implementation

Preserve exact allowlisted provider settings during canonical entity retrieval.
Read canonical observations with account/user-scoped sync proof, maintaining
legacy compatibility only where no canonical identity exists. Missing, stale,
incomplete, mismatched, or unproved observations remain blocked. GET requests
remain read-only. No live advertising writes occur in code verification.

## Release and execution state

Verified backend commit: `fe781eea162465b95f81bfc83e701ad6c8fdc21d`.

Validation from `backend/` (exit 0, **195 passed**, two pre-existing Pydantic
deprecation warnings):

```bash
python -m pytest tests/test_snapchat_entity_settings_v1.py tests/test_snapchat_campaign_management_v1.py tests/test_snapchat_management_execution_hardening_v1.py tests/test_snapchat_management_safety_regressions_v1.py tests/test_snapchat_reporting_v2_foundation.py tests/test_snapchat_entity_pagination_v2.py tests/test_snapchat_v2_entity_pagination_budget.py -q --tb=short
```

Regression evidence: the original reader loaded in isolation fails the new
canonical-current/legacy-stale scenario (99,000,000 instead of 12,500,000 micro);
the fixed reader passes. `git diff --check` passed. A fresh sync after deployment
is required because historical canonical rows lack the previously omitted fields.

Production changed: **no**. No bids or budgets changed.
No release lease created, no merge, no deployment.

Automatic approval review rejected reading the Emergent deployment dashboard
because it considered private deployment-project metadata outside the authorized
code-fix and cost-adjustment scope. Do not retry that access or use an alternate
route until explicit user approval covers the project administration/deployment.

Next safe actions: finish regression verification, commit and push this task
branch, open a draft PR, and record exact commit and tests in the PR. Ask the user
for explicit Emergent project administration and deployment access. After approval,
follow AGENTS.md release protocol v5, integrate against current production, verify
the deployed identity, sync through Mezan, then re-read eligibility and execute
the authorized 15% target-cost reductions with before/after verification.

Issue #1006 has reached the GitHub comment limit; use the draft PR as the durable
continuation ledger when the issue cannot accept a new entry.

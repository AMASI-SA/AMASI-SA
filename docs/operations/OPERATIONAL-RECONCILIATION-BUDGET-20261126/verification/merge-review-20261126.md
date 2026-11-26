# Operational reconciliation — integration/publish checkpoint (2026-11-26)

This is **documentation-only** evidence, on a separate branch. NEVER merge or deploy this branch. No owner financial reports or confidential source records are stored here.

## Authorized request
The owner explicitly requested to continue, merge and publish the accounting-refresh root fix. Preserve historical facts and dates, evidence/approvals, idempotence, query scope, authorization/lease rules, 85-second deadline, and the original v5 deployment protocol. No Preview or paid Emergent agent chat.

## Exact Git history
- Repo: AMASI-SA/AMASI-SA.
- Production source before this task: `hotfix/prod-snap-meta-final` = `33be2d0e1b51faa355d55707c0c374f4c1d6bbe2`.
- Provider fact-index companion PR #2136 merged **into task branch only**, not Production, by ordinary merge: `99aa895e63656599d4d89cc335a3371eb8c93ebf`.
- Task branch: `codex/operational-reconciliation-budget-20261126`; its merged HEAD is source candidate A `99aa895e63656599d4d89cc335a3371eb8c93ebf`, NOT release intent B.
- Production-targeted sibling PR #2137 CI HEAD `4aa417110b267be62d5db74ab5c3697f6edaf0d9` passed all 12 workflows: Backend 8,118 cases + 151 subtests; Stock 6,791 cases + 97 subtests; zero skips/failures/errors. The companion's three candidate file blobs match those reviewed on the CI sibling. This does **not** substitute for exact merged-head CI or production financial acceptance.
- Root merged A CI: PR #2135, GitHub Actions were running at checkpoint; the Mezan Release Readiness workflow `45851489888` succeeded. Exact merged-head Backend, Stock, Security, CodeQL and other workflows must be read back before any release.
- Intent candidate artifact from merged A readiness: artifact ID `15133342979`, name `release-v5-reviewed-intent-candidate`. Uncompressed JSON: `1,549,194` bytes, SHA256 `85184ff1370039178ea1fdaceb95bc65d923965de9a0d49014deed3dd5174f1e`. Declares A `99aa895e...`, base `33be2d0e...`, and deterministic release ID `rg5-455e302bce49659f5eed4bb82fb3555f8f67b1d50a087bfe989b1189dc075079`. It is an **artifact**, NOT an already committed intent B.
- Read-only production health showed the prior release `rg5-3786c214f5c7213c2b3c118ff79d895b716268f479c94d8c5fba2f7a0de66b86` / source `b7c7d13f9685fdbc5d7c93b8f8278638bcb2aa6d`; verified identity and frontend/backend hashes were true. No publication by this task has occurred.

## Remaining safety and acceptance gates
1. Check all exact A workflows. Verify benchmark/provider tests actually executed in merged Backend and Stock artifacts.
2. Independently review cumulative old-movement / valid historical payout coverage and synthetic full-tail A/B; passing the bounded 120+120 provider scan does not prove the actual 85-second production budget.
3. Freeze original reviewed intent artifact into an **intent-only B**, changing `release/release-intent-v5.json` and nothing else. Do not invent a smaller manifest, fake another SHA, or treat A as B.
4. Run exact B workflows and Node 20/24 clean-clone v5 adapter rehearsals. Ordinary merge B to Production as a tree-equivalent D and require D push checks.
5. On original Emergent host only: read `python scripts/production_release_guard.py status` and check `active=false` before touching /app. Verify full preservation/untracked snapshot, governed prepare/prepublish, direct publish, and official three-probe verify. Never override another task's lease or reuse previous deployment identity.
6. Accept only after a fresh real calculation **saves** and a private recovery report proves historical movements, original dates, approvals, balances and replay/no-duplicate parity. Existing source-completeness warnings must remain explicit.

## Current result
Source merge into WIP task branch: **yes**. Production merge: **no**. Intent B: **not yet committed**. Original-host prepare/publish/verify: **not run**. Live saved accounting recovery: **not yet proven**. Never claim that green GitHub checks by themselves mean financial recovery.

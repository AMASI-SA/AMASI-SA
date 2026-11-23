# Inventory side-option classification — release above PR2087

## Authorization and current source boundary

The owner requested **ادمج وانشر واختبر** on 23 November 2026. Merge, one guarded publication and read-only live acceptance are authorized for the inventory option classification feature. This is a new source composition; no publication success is claimed.

- Repository: `AMASI-SA/AMASI-SA`
- Source branch: `codex/inventory-side-options-release-r2-20261123`
- Reviewed Production base J: `422fffc8fb9c381165cc76b1ded5ca595773ce8e` (normal PR2087 merge above PR2086)
- Inherited Production intent blob: `59a341dcd70a72d450033ab4883a44bc4890dd27`
- Feature provenance: [PR2084](https://github.com/AMASI-SA/AMASI-SA/pull/2084), complete source `e412bc630de7d7e8eaf7dd03f6c1d09e59c18367`
- Superseded composition: [PR2089](https://github.com/AMASI-SA/AMASI-SA/pull/2089), immutable source `45c8fb4ea91e5af8523971fbfa2270ee96552224` and intent `499b98735b5cc4eae79d380e4f8d5ec5477175e5`, preserved unmerged and unpublished.
- Evidence branch: `codex/inventory-side-options-evidence-20261123`; readiness checkpoint `a106d73faed146fd7b2f178e56e9dbb4fa9ef5bf`. Evidence branches are never deployable.

Production advanced through PR2087 at 16:35:10 UTC while the previous composition finished CI. A fresh pre-merge check detected the move; no old intent or source was merged over the new base. The complete source-only interval `76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43..45c8fb4ea91e5af8523971fbfa2270ee96552224` was applied to an isolated worktree at the new J. No ancestry containing the old intent is imported. Source patch SHA256: `4c30a6bd1237b2e95cc06543de0ef2038771ec531eaf084b3c8c8c691b041a27`. Before this status refresh, the combined tree is `27e88fabf092ae80d83c7b1f490e271b51711e75`.

## Behavior and preservation

Each stable product option ID is classified as a physical specification, actual customization, or a customer side addition. Gift-card questions, gift text and notes may use the side-addition role. They do not block new inventory readiness, create inventory identity, or force deferral to the customer. Customer option definitions/answers and costs are retained. Required physical dimensions remain required and locked to the physical role.

Preserve conditional option relationships, stale-draft review and policy revision/fingerprint checks, completed-command replay, original invoice/receipt identities, supplier-piece history and all financial amounts. No valuation gate, financial writer, customer-order inventory dispensing or Salla option definition is changed.

The new base also includes PR2086 accepted-statement/weekly-fee work and PR2087 automatic balance/source freshness warnings. The only source overlaps with the PR2087 change are `.github/workflows/operational-balance-web.yml` and `backend/operational_balance_routes.py`; both are additive. Preserve the freshness routes/permission, collection/no-skip gates, and thirteen simple-balance browser scenarios alongside inventory-role routes and eight inventory browser scenarios. The new J intent stays byte-exact throughout source preparation.

## Existing evidence and required new verification

The superseded exact-B composition completed all applicable gates: 54 raw check records, 44 success and 10 expected event/scope skips; latest workflows 21 success and one scoped skip, with no pending/failure/neutral. Operational backend 6810 tests plus 90 subtests, demand 106, shipping 16 plus 18 subtests, frontend 1699 tests in 106 suites, stock eligibility 5531 plus 97 subtests, accounting, security/CodeQL, recovery and G47 all passed. Counts overlap and are not additive.

Its actual Node20.20.2/24.19.0 clean-clone package adapter rehearsals also passed. Independent and root reviews verified four artifact ZIP hashes, both inner archive checksums, all 389 generated file paths/bytes/modes, original intent/source/artifact/proof/runtime validators, and complete frontend/backend package proofs. Source browser acceptance passed eight inventory scenarios, ten screenshots and zero errors, with one policy-only PUT and no payment calls in synthetic fixtures. These results are provenance only for that superseded composition.

This new composition needs its own exact-source CI and browser evidence, original governed two identical clean builds, external candidate validation, new intent-only B and all exact-B gates including actual Node20/24 adapters. Freeze a new source A and intent B; do not amend, rebase or squash them. Normal merge must retain exact B tree and source ancestry.

## Shared host and coordination

Canonical handoff: Issue1006 comment6238987123, section INVENTORY-SIDE-OPTIONS-20261123. Preserve the in-progress PR2087 deployment handoff. At 16:40:21 UTC the original guard was inactive, shared /app clean at previous J76efd2c3, and remote Production at new J422fffc8. This observation is historical and must be refreshed before any shared boundary.

No shared /app source/output, release lease, Production merge or publication has been performed by this task. Source integration and ordinary CI use isolated worktrees. Host staging/publication wait for PR2087 verified completion and a fresh inactive original guard. Never clear another actor's lease, stop its verifier, or overwrite unrelated files. The prepared PR2089 host helpers remain disabled and must not be activated for this new identity.

## Exact next safe sequence

Checkpoint the new source and its integration review, execute the new exact-source gates, freeze the new governed candidate and intent-only B, and record all immutable identities in post-freeze evidence and Issue1006. After all applicable gates and independent review, recheck fresh production/guard/coordination, normal-merge the new PR, bind reviewed host helpers to that actual merge, run the original adapter rehearsal, safely stage, then prepare/prepublish immediately before one publication. Require a newer explicit successful deployment, original three-probe verification with exact source/release/artifact and JSON MIME, and final inactive guard.

Live acceptance is read-only: open inventory entry, option classifications, purchase entry and awaiting-receipt pages. Do not save real option policy, stock, invoice, receipt or payment records for testing. Leave the final source/PR/CI/deployment/verification outcome in the evidence branch and canonical handoff.

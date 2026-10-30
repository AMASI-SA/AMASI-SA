# Courier balance consistency — 2026-10-30 (Riyadh)

Verified runtime source:7df29a0ff8fd2ab445413cb29dc00b4809c238bb, branch codex/courier-balance-20261030, PR #1641, original base0bf90537643c9b033d4ec9cc1d51e2e9c2d20a24. This status-only checkpoint leaves runtime code unchanged.

Completed: shared pure COD calculation between courier label and driver collection, including provider partial-payment evidence hidden by normalized remaining=0; canonical currency in enrichment. Retains positive canonical balances, paid-order protection and deliberate canonical zero when raw data is shipping-only. Missing/conflicting payment evidence remains unavailable.

Fresh evidence: baseline total300/provider paid50 returned0; modified source returns250. Initial full Store Delivery Backend run passed118 tests. The broad native run exposed a historical shipping-only zero regression; fixed that mechanism and added a regression. Current native-contract run40961326547/job123754539643 passed753 tests and549 subtests. Store Delivery Backend job123754539552 also passed. All enabled checks completed success/neutral/skipped on the final reread; no failing check remained. Source frontend-build run40961326792 retained candidate intent artifact12935577494, but no intent-only B was created because production advanced.

Production unchanged by this task. Read-only host check observed /app HEAD61da14e645e8aec6a5fa33eac8a8f5208c110e5c, tracked diff0, ACTIVE=true, actor codex-reports-direct-20261030, release rg5-f0663e033649e1da330bb6d90ebbfff2b6593b2ecc3cd8f68ca108514732bbf8. Do not clear/reuse that lease or change /app while active. Browser later displayed Cannot reconnect.

Mobile companion PR AMASI-SA/amasi-mobile#307 sourceef8ce447146818beeefd7b3938b757b8ed5faf8e produced signed1.0.15/code16 APK via local GitHub build40963633590. App35 tests, TypeScript, Expo doctor18/18 and signature/version/Firebase binary checks passed. Physical phone delivery and server outbound push enablement are still unverified.

Next safe action: the other publication owner closes its lease; reread remote/host/AGENTS and preserve newer production changes; integrate this patch onto current production, rerun CI, freeze a new matching v5 intent, perform clean adapter rehearsal, prepare/prepublish and authorized publication, then require three matching probes and active=false. Do not freeze or publish a stale source based only on the old candidate artifact. No actual order, collection or ledger was modified. Issue#1006 is full; task PR and this document are the continuation record.

## Resume integration — 2026-10-30

Fresh authenticated host read: /app and origin production both fb12300c353cb1875f4462dc279b25360913af5c; active=false; tracked changes empty. Isolated /tmp clone merged that production into the task branch without conflicts. Diff against current production remains only the original five backend/test files and this status document. Prior runtime evidence remains historical until integrated CI passes. No production source, lease, orders or ledgers changed by this integration.

Next: push the integrated task checkpoint, inspect fresh CI; freeze a NEW v5 source/intent for this integrated source, rehearse the adapter, then authorized guarded publication and three matching probes. Issue #1006 has 2500 comments; this document and PR #1641 remain the task continuation record.

## Release source ancestry correction

Integrated source9fb26aa71f97128942991ae11395639a4f84a087 passed Store Delivery Backend, but release frontend classification rejected its merged intent history (source A must change governed source without changing intent). No build artifact or lease was created. Preserve that branch checkpoint; do not weaken the guard. The fresh release branch codex/courier-balance-release-20261030 starts directly from reviewed Production fb12300c353cb1875f4462dc279b25360913af5c, applying the exact conflict-free five-file runtime/test patch plus this document. This avoids importing prior intent revisions through merge ancestry. Fresh CI and a newly frozen matching intent B are required. Production remains unchanged by this task.

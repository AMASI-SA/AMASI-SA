# MZ2 CodeQL logging blocker — final verification

## Exact source
- Repository: AMASI-SA/AMASI-SA.
- New Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1244.
- Source A2: `3cb4ecc89146b67322b56ca848cfcf67fd904e75`.
- Final B2 HEAD: `551bd3d51b0cf33f5f34a8524495216bf56cc0de`.
- B2 TREE: `425ea7d0a62f5de3953f8bf1744ff3e3301d34c9`.
- Production and rollback, freshly read from GitHub: `83363097d48e034dc7140a60c290efc684e1ffde`.
- Original A1/B1 and Draft PR #1243 remain unchanged. The new source branch starts at A1, adds only the selected security patch, then freezes a new intent-only B2; this preserves the v5 requirement that source history must not contain an old frozen intent. No squash, rebase, force push, or production promotion.
- A2→B2 changes only `release/release-intent-v5.json`. All 1,880 source manifest records validated.
- New runtime identity: `rg5-03579150395576569c0806e08c418c71c3aa61fd5cdd923c06c7b725a5ee23b2`.

## Four alert classifications
All four are High `py/clear-text-logging-sensitive-data` (CWE-312/359/532). Original Python analysis: 1881603681; original PR test-merge 27fc2fe49981e6b64dc2682aa16ec2e129a3c1f6 had the original B TREE.

| Alert | Original line / sink | Reported SARIF path | Independently verified sink risk |
|---|---|---|---|
| #80 | 291, shipment_sync.synced | False Positive: callback cross-talk | Helper result is literal bool or absent; no employee/BNPL data reaches it |
| #81 | 294, order reference | False Positive for employee/BNPL path | True Positive: original webhook identifiers, including stringified secret-bearing objects, can reach logs |
| #79 | 295, shipment ID | False Positive for employee/BNPL path | True Positive: original webhook identifiers, including stringified token-bearing objects, can reach logs |
| #78 | 296, reason | False Positive: callback cross-talk | Real helper branches return static reason codes; now explicitly bounded at logging |

The SARIF path joins BNPL billing eligibility and employee create/update results through the generic local `callback(db)` in operational_atomic.py:416, then shipment operational_owner (webhook_order_sync.py:652), then shipment_sync at capture.py:270 and the result logger. At runtime callbacks are local to each invocation: shipment uses its persist closure; employee and BNPL closures belong to separate calls. They cannot exchange results through a shared callback slot.

A separate real leak was reproduced: verified webhook → original payload → shipment aliases (`order_reference_id/reference_id/order.reference_id/order.order_number`, `shipment_id/id`) → `_first_text` → `_text: str(value).strip()` → shipment result → raw result logger. The audit sanitizer operates on a separate payload and did not protect those raw log arguments. Synthetic private identifiers and nested objects containing secret/access_token reproduced disclosure.

## Smallest repair and changed files
1. `backend/salla_integration/webhook_event_capture.py`: only result-log projection and a private reason-code projection helper. Replace raw order/shipment references with the existing sanitized capture fingerprint prefix; log strict Boolean outcomes and known literal reason codes, or `unrecognized`.
2. `backend/tests/test_salla_current_shipping.py`: six security/control cases using the actual helper and mocked Mongo, preserving input/returned identities/audit/fingerprint/replay behavior.
3. `release/release-intent-v5.json`: newly generated source-bound intent after source changed.

No logging removal, suppression, dismissal, annotation, CodeQL/query configuration change, accounting writer, journal, transaction helper, write-control or guard changes. AST comparison preserved capture_unknown_event except its result logger and preserved _sanitize, _fingerprint and list_recent_captures exactly. Independent review compared 15 scenarios, 14 log checks and 10 reason projections. Unchanged event-name/exception logs outside the selected record were not globally audited.

## Tests
- Six security/control cases: 6 FAIL before → 6 PASS after. They inspect rendered messages and raw LogRecord.args, include normal shipping update/replay and malformed nested identifiers, preserve audit data and identities, and assert no financial collection writes.
- Wider local selected run: **84 PASS + 2 pre-existing FAIL**, not a completely passing focused run. Both failures reproduce on an untouched archive of original B.
  - `test_verified_order_webhook_refreshes_attribution_ledger`: fixture module lacks `persist_component_source_snapshot`.
  - `test_ledger_failure_never_blocks_salla_order_ingestion`: fixture supplies `object()` instead of a DB exposing `command`.
  - Existing tests/assertions/fixtures unchanged; correcting these gaps would be separate scope.
- Fresh final B2 CI: **41/41 workflows SUCCESS**; **68 Actions jobs SUCCESS + 4 conditional SKIPPED**, plus **independent CodeQL security check SUCCESS** = 69 successful checks / 4 skipped / 0 failed / 0 pending.
- Skips: unaffected Warehouse frontend build; unaffected Snapchat Settings backend/frontend jobs; manual redeploy notice on a PR. Host Node 20 was executed successfully on B2, not counted from A2 or from a skipped result.
- The historical 2172+900 and 244/1417 counts are not presented as fresh runs of this task. Fresh full CI covers its configured suites; the separate historical local full-selection run was not repeated here.

## Exact CodeQL evidence
- Final Python analysis: **1882283648**, created 2026-10-02T16:50:26Z.
- GitHub PR test-merge: `6f05863c668b697657e492192cfff984e0648ed2`; its TREE exactly equals B2 TREE.
- Target rule/file results: **0**. All CodeQL results in webhook_event_capture.py: **0**. Original alerts #78–81 open on new PR: **none**. No dismissals made.
- Independent GitHub CodeQL check **110934965691**: SUCCESS, zero annotations.
- [CodeQL workflow](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077868), [Security Gate](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077498), [G47](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077949), [Track F](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077623), [Accounting including real Mongo](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077811).

## Reproducible release rehearsal
[Final B2 release-readiness workflow](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077757): Host Node 20 job 110934895667 SUCCESS, including clean tracked-source clone, no generated input artifacts, no Git metadata, adapter build, isolated package boundaries, runtime identity and build-meta JSON route.
Artifact 11240221458 SHA256: `50e3b6f6ce02905abe28b88a4b51e89d22d40f893971d81cc7d7254ed51ed75d`.
Host Node 20.20.2 / Yarn 1.22.22 / Python 3.11.16; rehearsal is isolated evidence, not an observed Production build.

## Fresh final CI matrix
| Workflow | Result |
|---|---|
| [Runtime Stability](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077984) | SUCCESS |
| [Production Campaign AI Subprocess Worker V1](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077927) | SUCCESS |
| [MZ2 Accounting UI Phase 2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077907) | SUCCESS |
| [Snapchat Settings Management V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077903) | SUCCESS |
| [Salla Orders V3 acceptance](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077952) | SUCCESS |
| [Warehouse Location Engine](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036079557) | SUCCESS |
| [Production Fulfillment 278 Port](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077914) | SUCCESS |
| [Snapchat Reporting V2 Shadow](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077857) | SUCCESS |
| [Mezan Release Readiness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077757) | SUCCESS |
| [G47 Focused Integration](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077949) | SUCCESS |
| [Qoyod Payment Freshness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077946) | SUCCESS |
| [Build20 component category contract (no deployment)](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077768) | SUCCESS |
| [Snapchat CAPI Purchases](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077813) | SUCCESS |
| [Employees V2 Foundation](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077878) | SUCCESS |
| [Build20 supplier invoice integrity (no deployment)](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077803) | SUCCESS |
| [Production Preparation Piece Operations](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077799) | SUCCESS |
| [Build20 supplier scan recovery (no deployment)](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077823) | SUCCESS |
| [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077868) | SUCCESS |
| [MZ2 Accounting Module](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077811) | SUCCESS |
| [MZ2 A+B source integration](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077923) | SUCCESS |
| [Auth Passkey Security](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077637) | SUCCESS |
| [Mezan MCP Gateway security tests](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077470) | SUCCESS |
| [MZ2 advertising V2 native accounting](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077514) | SUCCESS |
| [MZ2 Supplier Native Invoice V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077616) | SUCCESS |
| [Production Dashboard V2 Salla Ads Executive](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077569) | SUCCESS |
| [Production Dashboard Four Platform Spend V1](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077687) | SUCCESS |
| [Salla Abandoned Cart Webhooks](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077688) | SUCCESS |
| [Snapchat Integrations V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077698) | SUCCESS |
| [Fulfillment V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077636) | SUCCESS |
| [Store Delivery V1](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077726) | SUCCESS |
| [Components Required Category and Group Picker V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077522) | SUCCESS |
| [Security Gate](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077498) | SUCCESS |
| [Ads Auto Sync 5 Minutes V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077393) | SUCCESS |
| [Salla Order Revision P0 contracts](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077362) | SUCCESS |
| [Recipient Delivery Projection](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077710) | SUCCESS |
| [Products V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077375) | SUCCESS |
| [MZ2 Track G SSOT contracts](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077579) | SUCCESS |
| [MZ2 Accounting UI H2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077477) | SUCCESS |
| [Supplier Invoice Service Policy](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077650) | SUCCESS |
| [MZ2 Supplier Payments V2](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077712) | SUCCESS |
| [MZ2 Track F native shipping](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37036077623) | SUCCESS |

## Evidence and remaining release boundaries
Retained original SARIF/dataflows, red/green/focused/baseline logs and JUnit XML, B2 Python SARIF/proof, complete B2 CI job/step matrix, independent checks, intent manifest validation and Host20 artifact in the Codex Security artifact store. Published checkpoint metadata belongs on the evidence-only branch, not in B2.

Selected CodeQL blocker is resolved on B2. No release readiness or deployment claim follows from this task. Full Business UAT remains NOT PASS. Prior broader SSOT/Smoke B/Acceptance evidence belongs to original B and is not silently re-labelled as B2 evidence. New native accounting/Track G/Track F/G47 CI is recorded in the matrix.

Production financial writes by this work = 0. Isolated tests used mocks or CI Mongo replica sets; no Production endpoint or DB was used for financial testing. Production Git SHA unchanged. Write-control unchanged. No prepare, prepublish, lease, merge, deploy, Opening, inventory initialization, Activation, P08, schedules or backfill. Guard active=false remains last owner-provided /app observation, not a fresh remote status read. /app still last observed at old B1. Stop and report the security fix and verification.

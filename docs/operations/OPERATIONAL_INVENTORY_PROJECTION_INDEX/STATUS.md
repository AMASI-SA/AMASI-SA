# Operational inventory projection/index integration

Independent stacked branch based exactly on #1317 HEAD365899e6d3c491a65610d91ec815fc1e647085d7, TREE2f18c915c36f08d7e8dddab4a36c5df6a1a6be67. Original #1317/#1271 unchanged. Verified Production reference b41cd9cc36a738a0528dd89ddc7349b31db2a361 before starting.

Authorized scope: shared read eligibility in inventory projection, bounded evidence query optimization, tests/benchmark and read-only compatibility rehearsal. No production indexes, permanent hint, writer, accounting write capability, merge/deploy/restart/intent or Salla sync. 110-piece fixture is read-only simulation, not receipt-cycle acceptance.

Plan: reproduce projection discrepancy; share receipt eligibility; restrict evidence to active occupancy references while retaining current vetoes and opening adoption; benchmark100k receipts and growth with explain; CI on exact HEAD; stop for owner review. Held/reserved/available must be explicitly distinguished, including existing reservations on newly ineligible stock. Pending valuation is not sale availability.

## Implementation evidence (pending exact-HEAD GitHub CI)

- Shared eligibility now drives projection. Physical occupancy is never rewritten. `physical = held + reserved + available` for proven allocations; ambiguous allocation evidence returns unknown rather than invented availability. Existing reservations on newly blocked stock remain reserved, while the unreserved remainder is held.
- Read-only snapshot transaction keeps receipt status, occupancy and reservations at one committed instant. Replica Set/transaction support is a runtime prerequisite; failure never falls back to available stock. Test-only Mongo fixtures use Replica Set.
- Location condition fields are included in product decision/consume, product catalog and Salla read projections; no order transitions or sync behavior changed.
- Nonempty valuation markers remain unavailable until an explicit financial policy exists. No marker is treated as client-provided approval. Pending costs are not presented as approved values, even with an additional condition veto.
- Evidence query retains direct receipts and opening-adoption witnesses of all statuses, owner scoped,100locations/500references per query,20000unique proof cap perlocation batch. No production index initialization or permanent hint.

Before correction: targeted state tests6FAIL/8PASS (pending/rejected for product+component; pending valuation for both). Integrated intermediate suite621PASS,1SKIP,7subtestsPASS; skip is standalone-topology negative test because local standalone URI was not configured. GitHub provides the standalone fixture and rejects all skips. Final expanded acceptance also covers location-level changes, same-snapshot receipt changes and pending valuation display.

The110 fixture is only a read simulation:3proven historical +8raw gold +99ready silver/Abeer =110physical;107financially pending held,3available. It does not execute10/100receipts or2/1returns and cannot establish writer acceptance.

Benchmark24measurements,100k/200k: candidate indexes chosen automatically without hints. Full explain plans and medians in benchmark_evidence_queries.json/.md. Query-only improvement does not eliminate scans with current indexes; index rollout requires separate review. No production index was changed.

Remaining: final CI evidence; separately reviewed index deployment, Production stack integration approval, runtime Replica Set capability validation, physical-writer contract/approval and real110receipt/return cycle. No claim of complete operational inventory readiness.

Final focused acceptance:41PASS. First expanded GitHub CI exposed2collection errors because the backend-working-directory workflow omitted repository root from PYTHONPATH; added parent path, no tests removed/skipped. Fulfillment and Qoyod CI passed on9dff2216; all suites rerun on corrected workflow HEAD. Final results are linked in PR1326 and Issue1006 to avoid changing tested source just to append run IDs.

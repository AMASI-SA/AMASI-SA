# Operational bundle-cost projection — 2026-11-02

## Scope and authority

Owner clarification: the other conversation owns the dashboard fix and its publication (#1690). This task must calculate linked/nested product costs in OPERATIONAL balances, not change or republish the dashboard implementation. Preserve both tasks and publish the final operational integration only once after its business gates close.

Base: PR1687 integration/operational-balances-unified-20261102 at f7a2480bd3cabcf880dfe7fcb746c820551e3fad, tree58a36afeeb6bc2280e9bc4b0ed0b81b06b2df166. Latest incorporated production70576e2a remains untouched.

Read: production AGENTS.md; parent STATUS.md; PR1690 at3e6ea31764b194432e10027c03d82c5ccf502901; its order_cost_authority.py contract. Dashboard fix is still draft in the read snapshot; owner reports publication is being handled separately. Do not assume its live publication.

## Implemented in this checkpoint

- Pure operational projection of the SAME frozen, expanded per-selection leaves. Both unconditional and selected-option child products, nested quantities and independent resources are included. Parent catalog base/options are never added over the recipe a second time.
- Per-unit/per-leaf source identities; SAR cost is independent of customer payment currency. Exact pending-review/in-progress aliases gate only unreceived estimates.
- Incomplete costs, mismatched selection/quantity, duplicate identities and unsafe unit evidence remain explicit unknowns, not zero.
- Native stock/receipt/consumption/segment evidence is NOT treated as new supplier debt. It produces a financial-link review requirement; its cost is withheld from a supposedly complete unreceived total. Source inputs remain unchanged. posting_allowed=false is intentional, not a flag to flip.
- Deterministic cent allocation preserves complete-line totals and repeated projections have the same identities.

Local verification: PYTHONPATH=backend python -m pytest backend/tests/test_operational_bundle_cost_projection.py -q --junitxml=tests.xml. Result:67 passed,0 failures/errors/skips. Pure synthetic Python tests only, NOT Mongo/HTTP/production or full combined acceptance. Synthetic examples: four units of shawl15+rose40=220SAR; no rose=60SAR; optional independent embroidery10=260SAR. One rose with native receipt evidence leaves180SAR as KNOWN unreceived subtotal and marks the overall estimate incomplete pending financial linkage; it does not manufacture a40SAR payable.

## Not yet implemented / next safe action

THIS IS A PROJECTION CORE, NOT YET WIRED TO OPERATIONAL RUNTIME. No live bug-fix completion is claimed.

1. Read latest1690/production and1687 before integration. Use the shared authority's bounded owner/order/line loader and resolver; validate line fingerprints including text/variant changes. Do not copy dashboard totals, use today's catalog to reprice historical orders, or load one query per component.
2. Connect current and pre-cutover operational open-cost readers with per-piece identities. Prove the parent-only estimate is replaced, not appended, and absence/ambiguity cannot silently erase prior valid facts. Existing supplier final-invoice logic remains unchanged in this checkpoint.
3. Match native bundle procurement receipts/purchase invoices to the correct operational financial source and saved opening coverage. Bundle parents are excluded from the legacy preparation supplier path; do not assume the parent's preparation invoice covers a child. Cover partial and split sourcing explicitly. Require owner-reviewed read-only differences before financial recognition.
4. Run integrated source/engine/preview/real-Mongo/UI tests, including no duplicate payable/cost and mixed receipt/final invoice cases. The67 pure tests do not establish this wiring.
5. Retain all parent STATUS.md blockers: provider postsettlement refund protection, actual payment evidence/policies/openings, customers/loans, shipping/COD, warnings, payroll approval, common-period profit, release identity and one verified v5 publication.

Production changed:NO. No dashboard source, database, opening, invoice, stock, financial movement, policy, payroll activation, /app, lease or release intent changed. Restricted COD archive not accessed. Checkpoint changes only new module/test/status files on the isolated integration branch. Issue1006 remains capped; this status and PR1687 are the continuation record.

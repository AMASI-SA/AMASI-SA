# Operational inventory projection/index integration

Independent stacked branch based exactly on #1317 HEAD365899e6d3c491a65610d91ec815fc1e647085d7, TREE2f18c915c36f08d7e8dddab4a36c5df6a1a6be67. Original #1317/#1271 unchanged. Verified Production reference b41cd9cc36a738a0528dd89ddc7349b31db2a361 before starting.

Authorized scope: shared read eligibility in inventory projection, bounded evidence query optimization, tests/benchmark and read-only compatibility rehearsal. No production indexes, permanent hint, writer, accounting write capability, merge/deploy/restart/intent or Salla sync. 110-piece fixture is read-only simulation, not receipt-cycle acceptance.

Plan: reproduce projection discrepancy; share receipt eligibility; restrict evidence to active occupancy references while retaining current vetoes and opening adoption; benchmark100k receipts and growth with explain; CI on exact HEAD; stop for owner review. Held/reserved/available must be explicitly distinguished, including existing reservations on newly ineligible stock. Pending valuation is not sale availability.

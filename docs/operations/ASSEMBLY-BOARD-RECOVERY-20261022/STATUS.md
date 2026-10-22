# Assembly board recovery — WIP

Scope: old native in-progress/completed boards; keep the Mezan WebView experimental.

Mobile checkpoint: AMASI-SA/amasi-mobile, codex/assembly-board-recovery-20261022,
a8542ff3cbb6a762d1ba03447c6e2ed391509ba9, PR 294.
Component regression fails on build 54 and passes after the spinner settlement fix.
Explicit search button verified through the actual TSX component harness.
No new APK published.

Backend base: 571e1704fc823c681c61b2f31de1db4f5ea5f55a.
Candidate removes sequential per-order reads via existing tenant-scoped get_orders.
Search excludes unrelated workflows before loading details, preserving the existing
5000-workflow window, substring matching, eligibility, sorting and paging.
Tests add a one-bulk-read search contract and retain local/legacy/cancelled cases.
Python compile passed. Local integration tests initially blocked on missing test
dependencies and no local Mongo replica set. Exact-source CI remains required.

Production changed: no. Release Guard not started. No financial/order writes.
Next: run exact-source CI including real Mongo, review errors, then prepare the
server release under protocol v5 and Android follow-up build. Deployment access
must be restored through the authorized login flow; earlier declined login is not
permission to retry silently. Web session persistence/SSO remains separate pending work.

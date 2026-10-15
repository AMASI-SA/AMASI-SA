# P01 atomic recovery checkpoint — 2026-09-19

P01 IN_PROGRESS; P02 locked. Draft PR #1091, branch codex/p01-closure-integration-20260918.
Resumed from 18144cb44f1abdb5c27e54eeed4427cf99e75b3f.

## Root cause and scope
The ledger core inserts posted legs sequentially. The previous recognition marker prevented replay after an uncertain failure but did not hide a partially written journal or recover it. The settlement route similarly changed its draft status independently of ledger writes.

Mezan 2 now uses a session-bound database adapter and one Mongo transaction encompassing every ledger leg, entry counter, audit and recognition event. Settlement route claim/journal/final status use that same transaction. A permanent owner coordination row is updated first inside the transaction, serializing recognition, refunds and settlement balance decisions; it is not an expiring lease. Mongo releases transaction locks after abort/process loss. The driver handles transient transaction/commit retries, with canonical event identity preventing committed replay. An exact Decimal balance check runs before commit.

The adapter requires a replica set with sessions; standalone is rejected before any financial write. There is no nontransactional fallback, automatic replica-set conversion or modification of shared Mongo. The existing ledger API and legacy callers remain unchanged. Old pre-candidate uncertain records are not silently repaired or reposted; protected existing settlements remain untouched.

## Fresh evidence
Code commit 631e8d9bd7f32c1290a9cc8178ec73d2a615af7c:
https://github.com/AMASI-SA/AMASI-SA/actions/runs/35442683637
- Real dedicated MongoDB 8.0.12 replica set in CI: all 12 operational API/bridge/ledger tests passed.
- Five actual Mongo recovery tests passed: process death after first inserted leg then retry; process death after successful commit before response; simultaneous import/sync/webhook; settlement rollback including draft status; standalone refusal without financial writes.
- The process-death test exits the worker with os._exit, reads through a separate client, observes no partial financial records, then retries successfully without deleting a lock or repairing a journal.
- Tax arithmetic/policy tests and existing backend/frontend CI contracts also passed.
- Additional real endpoint retry and exact-cent imbalance regressions are included in the follow-up commit; inspect its CI separately.
- Public test output is limited to synthetic invariants; private report retains run-specific identifiers.
- The prior local emulator suite has been upgraded to require an explicit isolated real Mongo URI. There is no fallback connection to application data.

These are isolated CI results, not Preview UI acceptance. The tests use a short transaction lifetime on their dedicated fixture server only; application code changes no server parameters.

## Preview/access and reservations
The existing terminal URL reported Preview Unavailable, then Starting IDE and failed to become usable. Administration displayed a static preview with backend session verification failure and a resuming message. No paid agent message, publish, restart command or shared database change was issued.
The current release guard and reservation owner could not be reread. The last known active release lease must not be assumed cleared. The previous own reservation at /tmp/mz2-p01-operational-preview.lock belongs, if unchanged, to:
P01 operational 01a08c76-d160-73c1-bfb1-7ef6940e107a
Do not remove it without reading and matching ownership; never touch another lease.

No new Preview financial writes, no protected-settlement changes, no Production changes by this task.
Backend and Frontend candidate runtime identities remain unverified.

## Exact next safe work
1. Restore independent terminal access; read Release Guard status and exact reservation ownership before any shared service action.
2. Inspect actual Mongo hello/topology without credentials in output. If standalone, use an explicitly isolated replica set for Preview test data, without altering shared Mongo or claiming standalone can transact.
3. Verify isolated data/egress, snapshot test documents, and run matching Backend/Frontend from the complete candidate.
4. Run accountant UI manual-tax sale/refund/new-settlement acceptance and persistence across login.
5. Download original XLSX and verify saved bytes and owner permissions in browser.
6. Run the same crash/concurrency suite on the actual Preview test database, then compare UI and ledger balances.
7. Keep P01 open pending those acceptance proofs. No Production release is authorized.

References: MongoDB transactions documentation https://www.mongodb.com/docs/v7.0/core/transactions/ and driver transaction retry specification https://github.com/mongodb/specifications/blob/master/source/transactions/transactions.md .

## Final verification
Code commit b35fa098e9d35e673f1198c9486f0c96dc163fec passed all MZ2 jobs in https://github.com/AMASI-SA/AMASI-SA/actions/runs/35442913594 : 12 real-Mongo operational tests, 7 real-Mongo atomic recovery tests, 11 arithmetic/policy tests, 49 backend contract tests, 36 frontend tests and frontend build. The added endpoint and exact-cent tests passed. Fresh Preview probe still returned HTTP 502 at 2026-09-19 12:27:46 UTC. This documentation-only checkpoint adds no application changes after that tested code commit.

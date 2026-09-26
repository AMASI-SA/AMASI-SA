# MZ2-FIN-CUTOVER-001: accounting write control and release preparation

This supersedes the missing-write-stop finding in P01-RELEASE-PREPARATION-20260920.md. P01 remains IN_PROGRESS, P02 LOCKED. Existing synthetic Preview acceptance and protected settlements remain accepted and untouched. No Production merge, deployment, database/index change, financial operation or activation is authorized by this preparation.

## Operating the control

The accounting workspace displays **كتابات ميزان 2**. Users with accounting read permissions can see its state and continue reading balances, evidence and previews. Only the current active owner can change it or request replay; the backend reloads authority from users, so hiding a UI button is not the permission boundary.

1. Owner records a reason and chooses **إيقاف الكتابات وانتظار المعاملات الجارية**. Wait for a successful response and refresh the state. A timeout/disconnect is an unknown result: reread state and revision before retrying; it is not evidence of a completed stop.
2. A confirmed pause serializes with all MZ2 transactions for that owner. An earlier transaction commits its entire balanced group or aborts. No partial journal becomes visible. Transactions ordered after the pause reject with HTTP 423. Other owners are independent; operations must enumerate every owner in the intended cutover scope and confirm each owner's pause.
3. Read access remains available. UI saves/approvals, automated recognition through webhook/sync/background bridge, receipts, settlement changes and refund drafts cannot change accounting state while paused. Raw provider evidence and the durable nonfinancial ingress queue continue to be persisted, as do control/audit records. This distinction is necessary to avoid losing incoming events.
4. To resume, the owner enters a reason and chooses **استئناف الكتابات**. The revision comparison rejects stale/conflicting decisions with HTTP 409. Tax policy, its effective date, and cutover configuration are not modified. The sticky managed-owner marker prevents return to the legacy bridge even if older routing markers disappear.
5. Resume allows new writes; it does not silently drain the queue. The separate **معالجة حتى 50 حدثًا محفوظًا** action can post eligible sales. Refund observations remain nonfinancial drafts for the existing daily approval flow. Repeat batches and reconcile pending evidence until accounted for; events requiring review remain pending and are never discarded. This action must be included explicitly in the activation plan.

API base: `/api/financial-provider-apps/accounting-module/write-control`. GET returns state/revision/owner capability/pending count; PUT accepts strict boolean `paused`, integer `revision`, and nonempty `reason` (maximum 500 characters); POST `/replay` processes at most 50 persisted events. Existing API authentication applies. No environment toggle, policy deletion, cutoff deletion, or legacy fallback is an operating procedure.

## Transaction and persistence boundary

The control uses the same owner coordination row as recognition and settlement commits (`mz2_atomic_owners`). Mongo snapshot transactions with majority+journaled commit serialize pause/resume and writes across workers. All mutating accounting routes bind their database operations to that transaction; upload reads its stream before entering its existing transaction so Mongo retries cannot consume an already-read stream. Read-only POST preview and the separately authorized control routes are excluded.

`mz2_ingress_events` persists minimal accounting evidence with a deterministic built-in unique `_id` before processing. Its completion marker and accounting effects commit in the same transaction. Replay after interruption, duplicate delivery or concurrent consumers uses the existing economic identity and cannot add a second group. Failed processing retains pending evidence. It does not replace upstream provider persistence/retry; an unavailable database cannot acknowledge durable acceptance. Existing safe bridge callers retain raw payment/refund evidence for later retry.

`mz2_write_control_audit` records actor, reason, old/new pause state, revision and time in the control transaction. There is no new permission that delegates pause to accountants: owner only. Before launch designate the owner, substitute coverage and scoped accounting grants, and verify those accounts through the real authenticated UI.

## Verification and immutable release handoff

The real replica-set suite `backend/tests/test_mz2_write_control.py` exercises the full production router, paused read/write behavior, owner/viewer isolation, stale revisions, sticky routing, webhook/sync/background deferral, concurrent replay, another client's restart, failed replay, and an in-flight first ledger leg during pause. Existing process-death recovery, daily refunds and settlement suites remain required. The settlement fixture repair retains the real refund guard and tests missing/partial settlement entries.

The earlier frozen PR #1099 pair A `781c657f3a2973885b5b6c4e5dcf5576e2741e4e` / B `e5b689a1cd2a63f1d70b3d2e6e2065f2fa4ea844` is preserved. WIP #1101 is retained. Production advanced independently through Qoyod #1100 to J `3b6bdc290bb401e41e804b46d1f29b068a50b4fa`. The new candidate retains those exact Qoyod changes and J's intent bytes, with clean source ancestry rooted at J. It contains the complete prior accounting changes plus write control; it does not merge into Production.

Final source A, intent-only B, runtime identity, exact CI run/test counts, and clean-clone Host Node 20/no-Git package rehearsal results are recorded in Issue #1006 after the source checkpoint. They cannot be embedded into A itself without changing A. Any later code change requires a fresh source and regenerated intent. A source checkpoint alone is not release approval.

## Production database and backup readiness: NOT VERIFIED

No confirmed Production database connection or backup administration session is available to this task. The browser surface exposes no existing admin tabs, no Mongo connector is available, and local saved-connection discovery has not established a production identity. Preview or a shared `/app` terminal does not prove Production topology or backup state. No paid Emergent conversation is used.

Needed access: the **Emergent project Production database/connection and backup administration page**, or the linked **MongoDB provider's cluster and Backups/Restore pages**, with explicit mapping to `mezansalla.com` Production. The database provider/cluster identity has not been established; do not presume Atlas. Provide a named saved read-only connection/session, not credentials in chat.

With that authorized access, record read-only evidence of environment/database identity, replica-set and session support (`hello`), existing unique indexes (`listIndexes`) for journal idempotency and settlement/provider/bank identities, and backup policy, last successful job, retention and recoverable timestamp. Compare index specifications to the governed code; do not create, drop or repair Production indexes in this task. Missing or incompatible requirements block activation.

Backup file existence is insufficient. Obtain successful restoration evidence into a separately authorized isolated destination, with restored database identity, collections/indexes, record counts and journal balance/identity checks. If restore is unavailable, keep restoration UNVERIFIED and a launch requirement. No restore has been attempted or claimed here.

## Decisions still required

- Production tax rate and its effective timestamp. Synthetic test rates/dates are not defaults.
- Cutover date, exact time and named timezone, scope of owners, and handling of events around the boundary.
- Named owner and substitute, user permission grants, and who may request pause/resume and replay under that owner authority.
- Activation sequence and window: Production readiness/restore evidence, immutable A/B review, independently authorized release protocol, pause confirmation, approved tax/cutover configuration, resume/replay and reconciliation. Since ordinary configuration is rejected while paused, configure it in the approved controlled activation window before reopening other writers; do not bypass the guard.
- Incident plan: pause and drain through this control, retain evidence, diagnose and prepare a corrected governed package; never delete tax/cutover markers or restart the legacy bridge.

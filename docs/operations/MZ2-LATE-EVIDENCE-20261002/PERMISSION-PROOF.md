# Evidence review permission boundary

The user's explicit mapping authorizes the existing
`accounting.shipping.contracts.review` grant for reviewing a late delivery
attachment only. No permission registry, role definition or implicit grant is
changed. Existing uses of this permission remain unchanged.

| Boundary | Enforcement and executed evidence |
|---|---|
| Reviewer identity | Existing fresh `_actor` permission check and owner resolution; inactive/revoked/foreign actors fail closed |
| Queue metadata | Existing `accounting.shipping.view` grant |
| Original image and approve/reject | Explicit `accounting.shipping.contracts.review`; verify retained bytes, SHA256, MIME and complete source identity |
| Review mutation | Append one sealed decision to `store_delivery_late_evidence_events_v1`; no automatic recognition or settlement call |
| Database capability | `driver_late_delivery_evidence` allows **insert_one only**, into the new event collection and retained delivery-proof artifact collection. Update/delete and financial/control collection writes are refused |
| Posting | Existing recognition endpoint still requires `accounting.settlements.post`; the review-only fixture receives **403** |
| Attempted escalation | Real Mongo test deliberately attempts a journal insert and original collection/proof mutation, catches the error, and still observes full transaction rollback |
| Financial pause | An approved attachment does not bypass the existing native writer's **423** guard or change `writes_paused` |
| Historical integrity | Existing C3 bytes/seal/reference and original delivery timestamp remain unchanged across attachment, decision and matching retries |
| Later consumption | Only the separately authorized existing Track F writer consumes the approved event. It pins exact source snapshots transactionally, retains original economic time, and seals late provenance; no new financial writer |

R4 ran the real HTTP routes on an isolated Mongo replica set: **37 PASS** in
49.00 seconds. Raw log, XML and exact command/environment policy are under
`evidence/backend-focused-r4*`. This includes actual concurrent driver/workflow
changes causing transaction retry then fail-closed rejection with no journal,
history, control revision or source-pin leftovers. Matching upload/review and
recognition retries produce no duplicate event or journal.

The capability's shared owner serialization revision can advance during a
successful evidence transaction. This is concurrency metadata, not a change to
write-control policy, account balance, posting authority or historical C3.

These are isolated implementation proofs, not Production financial execution,
final business UAT, a release authorization or proof that an arbitrary account
with additional independent grants cannot post. The reused **review grant
itself** adds no posting authority. Production financial writes by this task: 0.

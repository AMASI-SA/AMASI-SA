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

## Independent review and connected execution

Independent read-only review of c7326be found no concrete permission,
owner-isolation, C3 or write-control bypass. The source transfer to PR1240 has
identical Backend/Frontend/verification source blobs. Explicit authorization is
rechecked inside the transaction; the existing permission-update endpoint also
uses owner serialization. No permission registry or automatic role grant changes.

Executed cases in `backend/tests/test_mz2_late_delivery_evidence.py` include:

- `test_review_permission_is_fresh_and_separate_from_read_and_post`
- `test_owner_can_read_metadata_but_needs_explicit_grant_for_original_and_review`
- `test_paused_evidence_approval_preserves_controls_and_writer_stays_423`
- `test_operational_profile_cannot_write_finance_or_patch_originals_even_when_error_caught`
- `test_original_bound_proof_and_sealed_c3_remain_immutable`
- `test_concurrent_identical_upload_and_approval_have_one_event_each`

Connected real-component/HTTP/replica-set browser execution passed **5/5** on
c7326be. Retained evidence is
`evidence/full-browser-c732/browser/browser-results.json`: unchanged protected
fingerprint `afa12ae9bc00e09d9284bd929433a610a9144770f6fe6cc7b16b10d4edf7ee7a`,
`writes_paused=true` before/after, Legacy accesses zero, two unique attachments
and two unique decisions despite a lost-response upload retry. Only new proof
artifacts/events and the explicitly excluded owner-serialization revision
change. The actual stored artifact/original binding and C3 are separately hashed.
The owned server exited and its UUID database was removed (cleanup artifact).

This browser run uses a bounded harness with actual components and endpoints.
It is not the full application's Business UAT, Smoke B, or Production proof.

These are isolated implementation proofs, not Production financial execution,
final business UAT, a release authorization or proof that an arbitrary account
with additional independent grants cannot post. The reused **review grant
itself** adds no posting authority. Production financial writes by this task: 0.

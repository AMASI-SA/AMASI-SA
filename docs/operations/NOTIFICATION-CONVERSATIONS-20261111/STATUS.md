# Product notification conversations — Android78 companion backend

## Current checkpoint

Source implementation and independent review are complete: 26 focused real-Mongo conversation tests pass; the final nine affected instruction, push, permission, product-card and directory suites pass 130 tests plus six subtests. Root's edit-flow projection finding, the independent historical-authorization finding and item-scope product-card routing are fixed and covered through actual HTTP routes. Exact-source CI and the governed v5 A/B release remain pending. No shared source change, release lease, production database mutation, real employee message or publication has been performed for this task.

The user authorized combining notification conversations with safe Android receiving-storage cleanup and publishing one application update. Mobile Draft PR348: https://github.com/AMASI-SA/amasi-mobile/pull/348. Mobile branch: `codex/android78-notifications-storage-20261111`, intended Android78 / 1.0.63, based on Android77 source `6f082c566a66386b6c0abf987f36b8bdbe338109`.

## Integration boundary

- Initial backend base: `d3c9b0c946afe2418aecf0b96629e46d7f14f196`.
- New checkpoint uses reviewed Production `245a0beccbdbc2585a3ec2214e808eeef3734b57` as parent and full base tree `d5240e4fe0e72890d4e06d6da43e07f5ae680962`. Its dashboard changes are retained; only this task's explicit paths are overlaid. The governed release intent is unchanged.
- Task branch: `codex/notification-conversations-20261111`.
- Dedicated local checkout, fast-forwarded to the same reviewed Production base after proving the upstream paths did not overlap the task changes and checking all task file hashes before/after. Other checkouts remain untouched.
- Backend Draft PR1885; mobile Draft PR348 candidate `bd435b910697097b5b95d9d2454142c3579387ba`. The earlier backend checkpoint is WIP and must not be deployed.
- Dashboard1884 completed its publication and official verification. Root independently ran the official guard `status` in an isolated code-server terminal at approximately 20:13 UTC; it reported `active:false`. Repeat this check before any shared source mutation or rehearsal; this observation does not reserve the workspace.

## Behavior and invariants

The opt-in `conversation_only=true` contract projects one private conversation per instruction and assignee. Existing clients keep the original notification endpoints and semantics. Directory/count queries use conversation metadata; explicit detail loads the authorized product context. A bounded historical cursor migrates old notifications without replaying the whole history on every poll. Existing notification/progress loops retry projection and detect completion; no new worker or polling cadence is introduced.

- A recipient sees only their applicable assigned product cards. The creator's selected thread excludes other employees' events, evidence and private messages. An ambiguous old creator notice requires choosing its employee before product/message disclosure.
- Item-scoped instructions retain order-item target IDs separately from physical piece IDs. A shared item assigned across employees returns only each selected thread's pieces; a piece-scoped note remains limited to that piece. The regression demonstrates the pre-fix empty-card failure and the corrected A2/B1 isolation.
- Read receipts name only rendered event IDs and are silent, idempotent and separate from business acknowledgment. Reply identity is scoped to merchant, conversation, actor and client request ID.
- Proven completion suppresses a recipient's active alert even unread. The creator receives one completion result, retained unread until opened; the authorized history remains. One employee's completion cannot suppress another employee's outstanding work.
- Supplier receipt requires durable received status/execution/timestamp and completed required services. Draft scans and mere stage movement cannot claim completion. The observer does not write products, invoices, ledger, holds or business-task status.
- Supplier-receipt custody transfer is distinguished from ordinary reassignment. It can retain a proven former-assignee result with a frozen authorized product snapshot. Routine reassignment revokes the former employee's access; terminal state alone is insufficient to keep access.
- Pre-upgrade data is covered with empty conversation/event collections and already transferred business tasks. Bootstrap requires an owned historical delivery plus the recorded custody evidence. Authorized terminal history retains explicit provenance across repeated GET/read calls, without restoring old business assignments or exposing the replacement employee's current product data.
- Assignment dates resolve the actual employee work assignment, excluding supplier transfer and file-registration dates.
- A stale concurrent observer cannot restore an old active state after completion. New assigned products reopen only the relevant employee's conversation.
- Cancellation/closure/rejection have explicit outcomes and labels; they are not falsely shown as ready. A human message after completion remains deliverable; stale fulfilled task push jobs are suppressed.
- Direct preparation readiness requires both `assembly_status=ready` and a valid `assembly_ready_at`; a bare stage/status is insufficient. Manual closure of a nonblocking note is `closed`, not a green product completion, and does not notify the closing creator of their own action.
- Native permission allow-list additions cover only the two exact conversation read/messages POST routes and retain existing authentication/account ownership checks.

## Verification

Synthetic data only, on fresh isolated loopback MongoDB 7.0.17 replica sets. The focused suite has 26 passing cases, including actual HTTP routes/native request permission checks, merchant/assignee isolation, partial whole-order completion, unread result/read lifecycle, concurrent event/read timing, idempotent replies, reassignment/future-stage privacy, historical bootstrap and repeated access, assignment dates, stale observer CAS, stale push vs new message delivery, direct preparation timestamp proof, item/piece card scope, and opt-in stop/edit/reject/approve flows. No test uses a production fallback URI.

The final nine-suite run on integrated Production245a passed 130 tests plus six subtests in 33.56 seconds (exit0), recorded locally in `.test-runtime/final-notification-suite-item-scope.log`. The independent reviewer separately ran the preceding 25 conversation cases in 20.93 seconds, then the item/piece and corrected financial-negative cases (2 passed, 24 deselected, two subtests, 3.50 seconds). All exited0 with source hashes unchanged before/after. The financial-negative assertion now checks the actual `SUPPLIER_INVOICES` collection. The new cases are wired into the existing Mobile Order Tracking CI job on its real MongoDB replica set; it requires at least26 conversation cases without skips.

The opt-in detail route now combines frozen, scoped historical product/message context with current authorized operational task fields. Only the selected employee's tasks/evidence are returned; edit preview contains safe changed/revision/error metadata. Actual opt-in HTTP tests cover stop -> send revision -> submit, and legacy submit -> reject selected employee -> resubmit -> approve. A conversation read does not execute those actions.

Independent reviewer accepted tenant/participant isolation, read versus business acknowledgment, completion/CAS, historical authorization, item/piece scope and live-control separation. Final reviewed SHA256 values: conversations `ed87a0fb1893ad16737bf9e850594068ab5cc5c20e6fef2471cd43204fddd9ff`; routes `a73724ae2e584d73f79f502f3b7363346b24e4ba7ba4788d37529f554e114701`; conversation tests `71e7b35ce87988eb0f557ddfa7e425e7ceb5320b8ebd7e5f7b2f21ca41c47c6e`. The reviewer proved the late patch changes only the target helper and its scoped regression plus the financial collection assertion. Tests use ASGI HTTP and real Mongo with synthetic actors/push transport; they do not claim external push delivery or full production authentication UAT.

## Release order and continuation

Checkpoint exact final source A on the latest reviewed Production base; run required CI and obtain that exact source's governed v5 build/intent. Freeze intent-only B without squash or rebase, recheck ancestry, then follow `AGENTS.md`: inactive shared guard before any workspace mutation or rehearsal, verified adapter, own prepare/prepublish, one authorized publication, newer explicit success, three official probes and final inactive lease. Never reuse an earlier WIP candidate's release artifact after a source change.

Publish and verify this backward-compatible backend before the companion APK. The APK uses the observed authenticated Expo GitHub build path with the existing package/project/profile/signing identity, OTA disabled and store auto-submission off. Physical phone keyboard/layout and affected-phone SQLite size are not established by synthetic tests.

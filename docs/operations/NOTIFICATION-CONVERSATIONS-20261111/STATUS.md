# Product notification conversations — Android78 companion backend

## Current checkpoint

WIP implementation with 18 focused real-Mongo conversation tests passing. The previous nine affected instruction, push, permission, product-card and directory suites passed 120 tests plus four subtests. The opt-in edit-flow preservation issue found during root review is still being corrected; this checkpoint is not deployable. No shared workspace change, release lease, production database mutation, real employee message or publication has been performed for this task.

The user authorized combining notification conversations with safe Android receiving-storage cleanup and publishing one application update. Mobile Draft PR348: https://github.com/AMASI-SA/amasi-mobile/pull/348. Mobile branch: `codex/android78-notifications-storage-20261111`, intended Android78 / 1.0.63, based on Android77 source `6f082c566a66386b6c0abf987f36b8bdbe338109`.

## Integration boundary

- Initial backend base: `d3c9b0c946afe2418aecf0b96629e46d7f14f196`.
- New checkpoint uses reviewed Production `245a0beccbdbc2585a3ec2214e808eeef3734b57` as parent and full base tree `d5240e4fe0e72890d4e06d6da43e07f5ae680962`. Its dashboard changes are retained; only this task's explicit paths are overlaid. The governed release intent is unchanged.
- Task branch: `codex/notification-conversations-20261111`.
- Dedicated local checkout; other checkouts and the other conversation's active dashboard1884 release lease remain untouched.

## Behavior and invariants

The opt-in `conversation_only=true` contract projects one private conversation per instruction and assignee. Existing clients keep the original notification endpoints and semantics. Directory/count queries use conversation metadata; explicit detail loads the authorized product context. A bounded historical cursor migrates old notifications without replaying the whole history on every poll. Existing notification/progress loops retry projection and detect completion; no new worker or polling cadence is introduced.

- A recipient sees only their applicable assigned product cards. The creator's selected thread excludes other employees' events, evidence and private messages. An ambiguous old creator notice requires choosing its employee before product/message disclosure.
- Read receipts name only rendered event IDs and are silent, idempotent and separate from business acknowledgment. Reply identity is scoped to merchant, conversation, actor and client request ID.
- Proven completion suppresses a recipient's active alert even unread. The creator receives one completion result, retained unread until opened; the authorized history remains. One employee's completion cannot suppress another employee's outstanding work.
- Supplier receipt requires durable received status/execution/timestamp and completed required services. Draft scans and mere stage movement cannot claim completion. The observer does not write products, invoices, ledger, holds or business-task status.
- Supplier-receipt custody transfer is distinguished from ordinary reassignment. It can retain a proven former-assignee result with a frozen authorized product snapshot. Routine reassignment revokes the former employee's access; terminal state alone is insufficient to keep access.
- Assignment dates resolve the actual employee work assignment, excluding supplier transfer and file-registration dates.
- A stale concurrent observer cannot restore an old active state after completion. New assigned products reopen only the relevant employee's conversation.
- Cancellation/closure/rejection have explicit outcomes and labels; they are not falsely shown as ready. A human message after completion remains deliverable; stale fulfilled task push jobs are suppressed.
- Native permission allow-list additions cover only the two exact conversation read/messages POST routes and retain existing authentication/account ownership checks.

## Verification

Synthetic data only, on a fresh isolated loopback MongoDB 7.0.17 replica set. The focused suite has 18 passing cases, including actual HTTP routes/native request permission checks, merchant/assignee isolation, partial whole-order completion, unread result/read lifecycle, concurrent event/read timing, idempotent replies, reassignment/future-stage privacy, historical context, assignment dates, stale observer CAS and stale push vs new message delivery. No test uses a production fallback URI.

The earlier affected suite passed 120 tests plus four subtests in 37.06 seconds. The final combined run must be repeated after the edit-flow contract correction. The new tests are wired into the existing Mobile Order Tracking CI job on its real MongoDB replica set; it requires the conversation cases to execute without skips.

Remaining review issue: return authorized edit-preview metadata and current scoped task execution fields to the opt-in detail route, then exercise the complete stop -> send revision -> submit -> approve flow through that route. Passing legacy-only edit tests does not prove the new projection preserves that flow.

## Release order and continuation

Complete the remaining contract correction and independent review; checkpoint exact final source A on the latest reviewed Production base; run required CI and obtain that exact source's governed v5 build/intent. Freeze intent-only B without squash or rebase, recheck ancestry, then follow `AGENTS.md`: inactive shared guard before any workspace mutation or rehearsal, verified adapter, own prepare/prepublish, one authorized publication, newer explicit success, three official probes and final inactive lease. Never reuse this WIP candidate's release artifact after a source change.

Publish and verify this backward-compatible backend before the companion APK. The APK uses the observed authenticated Expo GitHub build path with the existing package/project/profile/signing identity, OTA disabled and store auto-submission off. Physical phone keyboard/layout and affected-phone SQLite size are not established by synthetic tests.

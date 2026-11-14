# Notification conversations and native recovery — source work held from publication

## Authority and base

The owner requested completion of the remaining fixes. The publication hold remains in force: no production/canonical merge, server deployment, EAS/APK build, OTA, store release or live/test notification send. Native compilation and isolated tests are verification only. Production changed: **no**. The cancelled Assembly barcode ordering request is excluded; do not restart it.

Backend source base: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`, tree `c2d683827c54574fcd60ee4102c9de7c163f5ef8`, branch `hotfix/prod-snap-meta-final`. New task branch: `codex/notification-conversations-recovery-20261114`. The local directory is an isolated source snapshot, not a Git checkout. Unrelated payroll/source-read changes in the current base are preserved.

This selectively integrates the held conversation source of [PR1885](https://github.com/AMASI-SA/AMASI-SA/pull/1885) (`19d1ab486f0c196b693a9b3c912787d967945168`) and the tested push-preview source of [PR1930](https://github.com/AMASI-SA/AMASI-SA/pull/1930) (`7139f91495a6ceaf356621eb2a0eb2d418c40a92`). Their frozen source/intent/evidence branches are unchanged. No old or new release intent is included in this task.

Mobile counterpart uses current published Android81 source `ef3984ee423d2e2645155fc33d154c6376d83a76` and selectively integrates held PR348's conversation UI. It preserves current storage, product-image, Ready, scanner and authentication behavior. No Android78 metadata or version bump is reused. Installed version1.0.66/build81 remains the published artifact.

## Integrated backend behavior

- Keep PR1930's immutable saved sender/action/message preview, current merchant/native access checks, bounded Unicode content and complete4000-byte provider limit. Missing legacy snapshots retain only the prior generic content path; existing foreign/read/mismatched events suppress a new send.
- Opt-in per-instruction/assignee conversations, directory/history/search, selected product context, session-authorized narrow message/read routes and idempotent replies. Only rendered event IDs are read; reads do not acknowledge/complete business work. Existing event/progress hooks maintain projection and per-assignee completion history; no new polling worker is added.
- A resolved conversation suppresses an unsent alert; accepted tickets continue receipt polling without replay or snapshot hydration. New sends recheck the exact device/generation/account/merchant/session/token binding after projection, preview and count awaits.
- Shared read-only active-conversation unread aggregation matches the new mobile inbox. Native delivery uses it only after rich-preview authorization; no private count is attached to missing-snapshot generic fallback.
- Linked employee accounts use the existing merchant resolver consistently for recipients, conversation participants and background catch-up. Owner identity and trusted created_by retain precedence over an Employees V2 link. Historical participant names do not grant current delivery access.
- Conversation projection is attempted before push enqueue. A failed projection does not fail the business command or erase the durable inbox event; an unprojected event omits the optional unread count instead of reporting a false zero. Retries retain the saved event's original delivery age.
- Immediately before a new rich send, recheck conversation resolution, current native grants and merchant, then the exact device/account/session binding. A grant revocation or merchant change during unread-count lookup cannot submit private content. Receipts and genuinely missing legacy generic snapshots retain their prior behavior.
- Completion previews use only versioned, allowlisted saved conversation outcomes. Old or arbitrary progress notes do not become a new task preview.
- Optional registration field `notification_presentation` accepts the exact Expo capability `amasi_android_rtl_v1`. Absent/unknown/web capability is stored as null, clearing stale capability when an old APK re-registers.
- Only devices declaring the installed native capability receive data-only Expo transport. Top-level title/body/sound/channelId/badge are omitted; even channelId would trigger ordinary FCM notification rendering. Data retains scoped event/instruction/conversation navigation and `amasi_notification: {version:1,presentation,title,body,unread_count?}`. Delivery is high priority with existing3600-secondTTL. The installed Android handler supplies the existing `operational-instructions` channel and default sound. Unread count must be a nonnegative32-bit integer. Old clients/iOS keep ordinary notification transport.

## First integrated verification checkpoint

Fresh local command from backend, using the existing read-only Python3.12 environment, `PYTHONPATH=.:tests`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1`, and pytest `--noconftest -p no:cacheprovider -q -rs --tb=short`:

`test_operational_push_presentation.py test_operational_push.py test_operational_push_preview.py test_operational_notification_preview_snapshot.py test_operational_instruction_notifications.py test_instruction_comments_progress.py test_instruction_edit_flow.py test_order_tracking_notes.py test_notification_sender_directory.py test_instruction_conversations.py`

Result: **116 passed,37 subtests passed,35 skipped, exit0** (19.26s). All35 skips explicitly require loopback `MZ2_TEST_MONGO_URI` (7 directory +28 conversation tests). No local Docker/mongod is available and there is no production fallback. Skips are not passes. Exact-head CI must exercise the disposable Mongo8.0.12 replica set and fail on skipped required modules.

New presentation tests first failed for absent capability registration and ordinary title/body transport, then passed after the implementation. They intercept HTTP through MockTransport; no provider request leaves the test. Additional tests prove same-recipient count use, account change during count prevents submission, generic fallback gains no count, old registration clears capability, unchanged receipt polling, payload bounds and preserved old transport.

Prior PR1930 exact-source CI reported194 passed+26subtests with no skips; that is historical evidence only. It does not substitute for this combined source's CI. Current independent backend review, full Mongo tests, mobile integration and native Android compilation are pending at this WIP checkpoint. Device visual/delivery acceptance remains a later separately authorized release check.

## Review corrections and second source checkpoint

Draft [PR1931](https://github.com/AMASI-SA/AMASI-SA/pull/1931) first checkpoint `028262db7325fb4e3146f87b830728de39801f79` ran the isolated Mongo suite: **233 passed,41 subtests passed,1 failed, no skips** in [run43634909362](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43634909362). The failure was an old conversation test's incomplete hand-written push device/outbox fixture. The fixture now creates a real active Expo device/session and uses the real enqueue function, keeping provider submission mocked and all authorization assertions intact. Its retest requires the next exact-head Mongo run.

Independent review additionally reproduced and corrected linked-account omissions, projection/count ordering, preserved original delivery age, completion outcome preview, and native-access/merchant revocation during count hydration. The last two hydration tests now intentionally expect two bounded grant reads (preview and final authorization), retaining zero-provider-send and discarded-outbox assertions. Independent reproductions confirmed both during-count revocations yield zero submissions.

Fresh local focused preview/presentation/snapshot suite: **47 passed,43 subtests passed, exit0** (4.87s). A full workflow-equivalent local invocation ran **190 passing tests and54 passing subtests**, but **20 route tests failed at the explicit missing isolated-Mongo setup check and42 tests skipped for that same unavailable Mongo dependency** (exit1). This is not a passing full suite. No local Mongo/Docker or production fallback was used. The exact-head workflow must run all252 tests with disposable Mongo, including31 conversation cases,10 linked-account cases and32 preview cases, and reject skipped required modules.

This second checkpoint includes those source/test fixes. Final combined-source CI and native Android compilation remain pending; no release approval is implied.

## Exact Mongo result and final tap compatibility correction

Second candidate `d7abbc832587c4c40cd8acc6fc9f7021ffee14f8` passed [run43636274633](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43636274633): **252 tests plus61 subtests, no skips**, with the no-skips guard passing (40.08s test execution). All11 applicable repository workflows completed successfully on that exact source.

Final cross-repository tap review found that the mobile handler dropped the server's scoped notice/conversation references. The mobile candidate now forwards validated references with warm/cold-start, duplicate and session-revision tests. A genuinely missing legacy snapshot previously carried an unresolvable notice reference; its existing regression was extended and failed on that exact condition. This candidate drops only notice_id/conversation_id when the preview function returns the exact legacy generic fallback object. It retains instruction_id/event_id and all existing delivery policy. Read/foreign/mismatched rows do not gain a fallback, and receipt polling is unchanged.

Fresh source verification after this last correction: operational push, preview, presentation and saved-snapshot suites **69 passed plus45 subtests, exit0** (7.88s). The previous252-case CI is evidence for its immutable source, not this final small delta. The next exact-head isolated Mongo CI must pass again; publication remains held.

## Continuity and next safe action

Keep this branch/Draft PR recoverable. Issue1006 is full; a previous body update was accepted but did not appear on independent readback. Existing ledger content was preserved. Do not claim a new ledger entry is visible without readback; use the verified task branch, Draft PR and this status as the durable handoff.

Next: finish independent integration review, run exact-head Mongo/native/mobile CI, remedy concrete findings and update this record with final immutable SHAs/results. When publication is later authorized, refresh the production base and follow the repository protocol-v5 source-A/intent-B handoff; deploy compatible backend before the new app. No release rehearsal or lease exists for this task.


## Final source verification record

Tested source `ec13c2c6a495b36cf5f70795428267d567634f86` passed all11 applicable workflows, including252 Mongo tests +61 subtests with zero skips. See [FINAL_VERIFICATION.md](./FINAL_VERIFICATION.md) for the exact immutable source, complete CI evidence, file manifest and next safe action. This documentation-only evidence branch is not a release intent. Production remains unchanged and publication remains held. The matching mobile native CI is still running at this backend-only checkpoint.

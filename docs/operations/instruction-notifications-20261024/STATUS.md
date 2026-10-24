# Operational instruction notifications — WIP

Production changed: **no**. No APK has been built or published for this feature.

## Contract

- A product instruction targets selected items/pieces only; siblings continue preparation.
- An order instruction targets the whole order. Product cancellation never calls order cancellation.
- Cancellation approval closes the internal task and releases its instruction hold. Final cancellation remains a separate Salla operation, as explicitly selected by the user.
- Notes and urgency requests do not stop work. Acknowledgment means the employee understood the note.
- Blocking instructions require employee execution confirmation, then approval by the original creator. Reading, replying, and administrative permissions cannot release them.
- The inbox is active/completed, one row per instruction; its count is independent of unread events. Each account sees authored instructions and its assigned work.
- Conversations are per task. Stage-targeted instructions notify the responsible employee on actual arrival; courier routing uses the assigned driver's account.

## Source checkpoints

- Backend/web: `codex/order-instruction-notifications-20261024`, base `4e4e064ca9855c3a626968df55ef844de77de947`.
- Staff: `codex/android-instruction-notifications-20261024`, base `56aec7bd6d9595c6e785d53d51a1458c09b040fe`; initial baseline code 56; fresh EAS check found code 58 (1.0.43), source `2324c914433c5454c50514a445f29a4fa73a7127`. Root merged its branch before release. No new APK built.
- Courier: `codex/delivery-instruction-notifications-20261024`, base `7e78ddf3d932221cbb7079fd0728e2609cc3a03d`; last verified courier code 12. Changes are in `delivery-app/`.
- Local recovery bundle: `D:/amasi-build/evidence/instruction-notifications-20261024/` contains tracked patches and complete changed-file archives including untracked source.

## Release prerequisites still outstanding

1. Complete integrated verification and save remote branches/draft reviews. The user explicitly authorized GitHub checkpoints and subsequently publication with rollback.
2. Configure and verify Firebase Android push credentials independently for staff and courier packages. The optional `GOOGLE_SERVICES_JSON` build environment variable points to the matching configuration file; it is not a credential pasted into source control.
3. Server outbound push is disabled unless `OPERATIONAL_PUSH_ENABLED=1`. Web push additionally requires `OPERATIONAL_PUSH_VAPID_PUBLIC_KEY`, `OPERATIONAL_PUSH_VAPID_PRIVATE_KEY`, and `OPERATIONAL_PUSH_VAPID_SUBJECT`. Expo access token configuration is optional when the project requires it. Keep all credentials out of logs and commits.
4. Exercise notifications on physical devices: foreground, background, terminated app, account change/logout, reassignment, scheduled-stage arrival, and tapping the notification. Provider acceptance alone is not proof of device delivery.
5. Build from the verified current mobile branches with new version codes only after the feature and credentials pass review. Do not reuse stale build 35/50 sources.
6. Apply the repository release guard and prove deployment identity before declaring production complete.

## Verification record

- Backend focused suites before final integration: 64 passed, 2 subtests; staff TypeScript passed.
- Web focused tests after creator decision and note acknowledgment fixes: 28 passed.
- Web notification worker tests: 2 passed.
- Root's broader Python collection initially lacked `email-validator`; rerun with the existing isolated dependency directory passed: 88 courier/assembly/physical-cash tests on disposable loopback Mongo databases. No production data used.
- Courier combined verification: TypeScript and 21 instruction/build37 tests passed.
- Staff latest-58 integration: TypeScript passed; strict historical byte comparison needs an explicit allowance for the new instruction-only gate, with authentication/transport invariants retained.
- Native push delivery and production deployment remain unverified.

Next safe action: finish the in-progress backend/native audits, rerun the combined tests, refresh the local recovery bundle, then finish the authorized remote checkpoints and release prerequisites. Both apps lack FCM v1 and GOOGLE_SERVICES_JSON; awaiting Firebase project access. Issue #1006 has reached its comment limit, so task PR/status is the continuation ledger. Preserve unrelated main-workspace accounting changes.

## Remote continuation checkpoint

- Staff draft: https://github.com/AMASI-SA/amasi-mobile/pull/300 (head `e0a0371`, includes latest released 58). All 18 source-addition checks, authentication preservation, instruction scripts and TypeScript passed after integration.
- Courier draft: https://github.com/AMASI-SA/amasi-mobile/pull/299 (head `3de1dd1`). 21 tests and TypeScript passed.
- Backend/web integrated current production `c045341c7153e563ae2c68c736e862f000c4c01f`, preserving source-addition stage policy and mobile web design changes.
- Final web integration: 42 tests passed. New per-piece stage regression first reproduced premature submission by a shared assignee, then passed after filtering current tasks and enforcing the filter on submission/comments. Future-stage creator comments no longer notify the employee before arrival. Focused task/comment suites: 32 passed.
- User explicitly authorized publication with rollback. Publication remains blocked on Firebase configuration/device evidence and normal release gates; no lease, merge to production, APK build, or deployment performed.
- GitHub Issue #1006 reached 2500-comment capacity; task PR and this status file are the continuation ledger.

- Backend/web draft: https://github.com/AMASI-SA/AMASI-SA/pull/1471. Final integrated local backend run: 90 passed, 13 subtests (Junit saved in local evidence bundle), including source-addition and real Mongo assembly tests.
- Remote baseline snapshots preserved: backend `codex/instructions-baseline-web-20261024`; mobile `codex/instructions-baseline-staff58-20261024` and `codex/instructions-baseline-courier12-20261024`.
- Initial CI exposed missing WebCrypto in the comment test environment (2002 other web tests passed); test now installs/restores its explicit crypto fixture. Mobile VM harness integration is being corrected without changing authentication runtime.
- Release guard rejects merge-history intent changes even when final intent bytes match. Preserve the pre-linearization branch, then replay only this feature's commits onto current production before a new release rehearsal; never weaken the guard or freeze an intent for the wrong source.

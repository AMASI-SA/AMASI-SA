# Operational instruction notifications — WIP

Current checkpoint, 2026-10-25: combined Android 60 and revised Salla edit handoff are **not published**. Earlier staff APK 59 and courier APK 13 are built; see the retained historical records below. No new production deployment or business-data write was performed during this checkpoint.

## Android 60 combined release checkpoint

- Staff repository AMASI-SA/amasi-mobile, branch `codex/android-instruction-selects-20261025`, source `c03dd09d4e2b9c94c57a6303b0ff481a0bcf0bdb`, PR 303. Includes Chrome fix from PR 302, compact single/multiple selectors, product photos/options, before/new revision cards and print, scoped frozen piece selectors, yellow notification count badge, and the persistent phone-permission reminder. Profile `mezan-web-assembly-pilot`, root `frontend`, app version 1.0.45 / Android code 60. No dependency upgrade, OTA, store submission or paid plan is authorized.
- Backend/web repository AMASI-SA/AMASI-SA, branch `codex/instruction-product-cards-20261025`, PR 1478; functional source `160b49de663ef00232300e68346fad7dec8abdb2`, based on production `4006ebd7eaf66f0d11824a637a0bc1c8116b0d30`. The source commit containing this status supersedes that functional checkpoint and needs a fresh governed intent before deployment.
- Latest user contract: employees with effective Orders-page access may create instructions. Native permission is derived from the trusted mobile bridge; browser permission uses the effective server Orders permission. Tenant, author-only decisions and task ownership remain enforced. Notes and urgency do not freeze work; targeted product holds do not freeze siblings.
- For the new Salla edit flow: employee acknowledges the stop with a note, creator edits Salla and explicitly sends the new revision, employee prints the current sent revision and confirms execution. The final targeted employee confirmation resumes the edit flow without a second creator approval. Cancellation only closes the internal task; final Salla cancellation remains separate. A further Salla change after sending rejects stale execution and printing; repeated re-send is not implemented in this first trial.
- Fresh focused verification: backend 68 tests passed; web 20 tests passed; staff TypeScript and scoped lint passed at the functional checkpoint. Seven current staff instruction/permission/selection scripts pass, including deselection when a stop arrives, rejection of stopped-piece selection, and re-selection after resume. These instruction checks are now included in PR CI. Exact c03dd09 CI session and print checks passed; full frontend check was still running at checkpoint time.
- Retained rollback APK: `D:/amasi-build/evidence/instruction-notifications-20261024/amasi-staff-59.apk`, SHA256 `063f4913068212ba08b85018664a2aa6ab4375613ab7bdef9b0142ffa5b84c27`, source `19a6bdbd73d4f0edb4ff322506e16c263e67463c`, EAS build `7952353b-d13d-4ca4-9840-0615d247f592`. Do not uninstall/clear data to downgrade; an installed code60 may require a higher versionCode rollback build with the same signer.
- Build60 APK has not been requested successfully or verified. The historical repository authority transport is explicitly not commissioned. The previously used Expo UI route still needs access and quota verification; no replacement token-bearing job or paid upgrade is authorized. ADB currently reports no connected device. User will test physical phone push; Chrome still requires Android validation before claiming success.
- PR1477 was merged into production branch 4006ebd7e and the user supplied successful prepare/prepublish output, but newer Deployment Succeeded and three-probe verification are not retained. Do not infer live deployment from GitHub or APK availability. Remote `/app` lease must be re-read before any workspace change/rehearsal.
- Issue1006 has reached its comment limit; task PR/status remains the continuation ledger. Preserve unrelated production workspace files. Next: exact-source CI, new v5 A/B intent, authorized remote status/rehearsal/deployment verification, then approved APK build, package/signature/version validation and Android Chrome test before distributing its link.

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

## Firebase and release checkpoint — 2026-10-24

- Firebase project amasi-notifications is configured for staff and courier packages. Both EAS projects have the authorized FCM v1 key assigned and GOOGLE_SERVICES_JSON as a secret file variable (staff production; courier preview). No credentials committed.
- Staff source 19a6bdb passed all enabled PR checks. Build 59 / 1.0.44 requested on EAS: 7952353b-d13d-4ca4-9840-0615d247f592. Build completion and physical delivery are not yet proven.
- Courier source 3ae303b includes build 13 / 1.0.12 and a deterministic npm lockfile. TypeScript and 21 instruction/baseline tests pass. The first launch was rejected before building for missing lockfile; the corrected launch is in progress.
- Backend source replayed onto production 1fbb7ea4b54ab5574f3ee6937939e21440f88b0a without conflicts. Pre-integration snapshot is retained on codex/instructions-before-cards-integration-20261024. The only tree differences from the earlier source are the newly published mobile-card changes and their release intent. Fresh CI is required.
- Remote /app guard status reports active=false; only untracked .emergent/testing/ and deployer-agent-docs/ are present. They were not modified.
- Production changed by this task: no. Server push enablement, new governed release intent/rehearsal, actual deployment identity, and phone delivery tests remain outstanding. Native builds are not published releases.
- Next: complete both EAS builds, review refreshed web CI and freeze its candidate intent, configure outbound push, then perform the guarded authorized web rollout. Keep all existing production data and holds.

## Integrated release candidate — 2026-10-25

Production changed by this task: no. Current reviewed production base is b505921915185272d40102d1efef2b8200f2c572. Frozen earlier A/B remain preserved on codex/order-instruction-notifications-20261024. New candidate branch codex/instructions-release-20261025 replays only feature source changes. Integration preserves both payment inbox and operational push startup/shutdown workers. Fresh focused backend checks: 54 passed and 2 subtests passed.

Both native builds are complete: staff 59/1.0.44 (7952353b-d13d-4ca4-9840-0615d247f592), courier 13/1.0.12 (55a43f70-7003-4532-8986-550cc1effea7). Firebase and EAS credentials are configured. Physical phone delivery is pending user acceptance. Server push enablement and governed deployment are pending. Remote /app is reachable and guard reports active=false; untracked files preserved. Next: fresh candidate CI, freeze reviewed intent, adapter rehearsal, prepare/prepublish, deployment success and three-probe identity verification. Do not treat native build completion as server publication.

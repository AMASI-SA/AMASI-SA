# Product notification conversations — Android78 companion backend

## Current checkpoint

Source implementation and independent review are complete: 26 focused real-Mongo conversation tests pass; the final nine affected instruction, push, permission, product-card and directory suites pass 130 tests plus six subtests. Root's edit-flow projection finding, the independent historical-authorization finding and item-scope product-card routing are fixed and covered through actual HTTP routes. The governed local A/B build and intent freeze are complete. Exact-source CI, clean-clone adapters, merge, publication and APK remain pending. No shared source change, release lease, production database mutation, real employee message or publication has been performed for this task.

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


## Frozen release checkpoint — 2026-11-11 20:43 UTC

This copy is on the evidence-only branch `codex/android78-release-evidence-20261111`. Never deploy or merge this evidence branch. Frozen production candidates remain immutable on their task branches.

| Identity | Value |
| --- | --- |
| Production base J | `245a0beccbdbc2585a3ec2214e808eeef3734b57` |
| Source A | `19d1ab486f0c196b693a9b3c912787d967945168` |
| Intent-only B | `ec84e55f33905db7e761fc7c24c61b07930ee4e5` |
| B tree | `2e29beb705c57c8a405d4ffa8ebe81f6724627ac` |
| Runtime release | `rg5-204a8622f87e658d5691a224e02e181064fb8fac93ba809fd5df7404877655cd` |
| Intent SHA256 | `9f50f99031c4bf59e6e05619116cc4607f81f93e749e9d3b4ee60d31b964d06b` |
| Mobile source | `2fe5dfe883009b23c25b83bf09517f116fb0881a` |
| Mobile tree | `150832e5326a3ac02c7722018d45dcf14b475138` |

Exact-A governed two-clean build passed in19.98s. A artifact verification, intent freeze and exact-B tracked-intent frontend artifact/proof validation exited0. Full adapter runtime identity materialization is still pending. The B frontend validator imports a reader which diagnoses absent pre-adapter identity, then validates the retained artifact and intent successfully; this is not a full adapter success claim. Local tracked worktree is clean at B.

A readiness43095867004 remains queued without the candidate artifact. B readiness43096664449 and tracking43096664447 are queued without jobs/checks. Mobile final CI43096023102 and Android7843096023117 are queued without jobs. Do not infer success from older source runs or bypass final checks. No new product-test failure is established for these exact candidates.

Source-derived visual evidence from the unchanged mobile UI at c2034162a1bfbfa107f338172401a1ce3d018628 is saved beside this document. CI artifact13889009005, run43093729761, archive SHA25626571915a4c06fc879c1004c175fede981259aea6d6742b75ac4e8ce603dcb38. The real TSX host tree was rendered through React Native Web with synthetic API fixtures and a synthetic shirt image. Root inspected directory, employee, conversation393px and conversation360px: compact rows, search/main-only push control, employee header, left product cards, peer-left/current-viewer-right bubbles and left send button. No horizontal overflow. Later source changes only corrected retention fixtures and renamed the PDF callback. This is not physical Android keyboard, external push or affected-phone disk-full UAT.

Fresh official shared guard status at approximately20:39UTC returned active:false. Shared source and120 unrelated platform files were preserved. No task merge, lease, production publication, APK build, OTA, store submission or real employee/business test. Expo authenticated GitHub form is prepared only, with Android/frontend/mezan-web-assembly-pilot and auto-submit off.

Next safe action: terminal exact-head CI and compare A CI intent with frozen local bytes; normal backend merge with exact B tree; recheck Production/guard, actual governed shared adapter, own prepare/prepublish, one authorized publication, newer explicit success, official verify3 and inactive status. Then final mobile normal merge and one observed Expo exact-source build; verify APK package/version/signer/bytes before distribution.


## Review readiness and artifact preparation — 2026-11-11 20:54 UTC

PR1885 and mobile PR348 are now ready for review, with exact frozen heads unchanged. This is not merge or publication evidence; final CI is still queued. An unsubmitted APK verification template is saved at `evidence/android78-apk-evidence.yml.example`. It uses only preinstalled Android tools to check actual signature/package/version and ten registered DEX module definitions, with three narrow retired-scanner markers excluded. It preserves source/build provenance as a separate authenticated Expo record, never infers it from a package version, and activates no closed EAS transport. Root reviewed the template; YAML, Bash and Python syntax plus13 bounded in-memory parser/input checks passed. No APK has yet been downloaded or built. Replace only the observed public unsigned Expo APK URL and observed build ID once the final authorized build exists; create/run it on the separate mobile evidence branch, never the frozen source branch.

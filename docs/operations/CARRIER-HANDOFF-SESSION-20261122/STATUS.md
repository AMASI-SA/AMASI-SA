# Employee carrier handoff — 22 November 2026

Date: 2026-11-22 (Asia/Riyadh). This continues the user's authorized Android86 / 1.0.71 update and the preceding provider-statement checkpoint. The user approved the three receiving-screen mockups and requested implementation, then explicitly corrected the date definition below.

IMPLEMENTATION_COMPLETE:true
MOBILE_IMPLEMENTATION_COMPLETE:true
BACKEND_IMPLEMENTATION_COMPLETE:true
READY_FOR_RELEASE:false
APK_PUBLISHED:false
BACKEND_DEPLOYED:false

Mobile parent: `66271584c7fa81e47dbab59d798446a452f110c7`, [PR373](https://github.com/AMASI-SA/amasi-mobile/pull/373).
Backend parent: `100fed6629eba4c4bc47d2500a294c0aea7f40cd`, [PR2050](https://github.com/AMASI-SA/AMASI-SA/pull/2050).
Remote branch for both repositories: `codex/assembly-queues-fast-read-20261121`.

## Accepted behavior and binding date correction

- Redesign existing Stage 6 **استلام موظف التسليم**, retaining the app header and footer.
- **Last 30 days means order creation time (`created_at`)**. The user's latest correction supersedes the earlier assumption that execution time defines the window.
- Default tab **تم استلامها**: current employee custody, with actual employee receipt **date and time** (`received_at`).
- Second tab **بانتظار الاستلام**: nobody has received the shipment; the displayed date is the actual **تم التنفيذ** timestamp (`executed_at`). Missing trusted execution evidence displays **غير مسجل**; it does not exclude a recent order or substitute its creation/update date.
- Both lists contain only external-carrier orders whose current status is exactly **تم التنفيذ**. Store-courier and pickup orders are excluded. Any other current status, including **جاري التوصيل**, removes the order from both queues on the next read.
- Eight rows per server page. Only the visible first page loads on entry. Further pages and the other tab load on navigation; no hidden-page prefetch or periodic polling.
- Search by submitted exact order number or waybill above both tabs. Counts remain independent of the submitted search.
- Press **بدء التصوير** to open an inline camera at about 30% of the available stage body. Reuse the same unchanged shared barcode decoder as assembly/labeling.
- Keep the same camera open through a receiving session. Each confirmed new receipt shows a brief green **تمت الإضافة** notice and adds one to that carrier's session row. Duplicate/rejected scans never increase the count. Updating the table must not delay recognition of the next different barcode.
- A session summary has one row per company, rather than one card per shipment. Successful receipts move waiting → received. **إنهاء الجلسة** closes the session; the successful receipts were already saved individually.

## Implemented backend contract

The new mobile read adapter separates the 30-day creation window from execution display evidence. It returns eight projected rows and the two counts. Only the visible rows load completion history. The existing canonical receipt writer is retained, with current-status validation and correct attribution to the actual native employee rather than the bridge owner principal.

Final actual synthetic HTTP responses from the frozen backend passed the actual mobile parser for waiting, received, new receipt, same-ID replay, session summary and duplicate rejection. The backend tests exercise the real OperationalDatabase fulfillment capability over owner-serialized MongoMock. This is local implementation acceptance, not physical-camera timing, native UAT, production data validation or APK proof.

The preceding queues, notices, shipment matching, unread-preview, financial notifications, refunds, employee balance, compact tracking and platform statements remain in scope and preserved. The courier-count discrepancy (315 versus 254) remains deferred.

## Release continuation

Do not publish the parent commits as containing this new feature. Use the verified source checkpoints and export recorded here. Continue the already authorized release using the existing automation `Automation_7fd7e2fc16f8819199903b78592d5757` without changing its hourly schedule.

App-first release is already authorized while Mezan waits. No new permission request is needed. The new page must handle an unsupported old server explicitly. Required exact-source remote checks and Expo access remain release gates; local tests/export do not substitute for an APK build.

Mezan still requires coordinated integration against actual production and the repository's v5 release protocol. Do not clear another conversation's lease, deploy this task branch directly, recreate a financial writer, or perform live shipment receipts/financial operations to test the feature.

## Verified mobile source checkpoint

The application source is frozen. Independent actual-module verification passed **29/29** behavior groups, and **30/30** with real synthetic HTTP responses from the backend routes. The four behavioral baseline controls fail as expected on immutable parent `66271584c7fa81e47dbab59d798446a452f110c7`: old limit-100 read, missing tab pair, scheduled polling, and camera closing after a successful receipt.

Root independently reran the 30-case contract suite, full-project TypeScript and feature ESLint: all exit 0, with no lint warnings. Adjacent shared-scanner behavior (51 cases), scanner preservation negatives (41), scanner boundary behavior (11), shipment-result behavior (8), assembly print lifecycle, authentication/source-addition, navigation, mobile-first backend compatibility, platform statements (25), statement grants (13), refunds (19), employee balance (12) and global tracking (19) passed. `verify-ci-scanner-source.cjs` also passed in full after restoring the missing immutable historical Git commit objects; no scanner pin, gate, protected byte or dependency was changed.

The final behavior suite exercises slow table GET followed by another barcode while that GET remains unresolved, query-scope invalidation before replacement account publication, a new server scope for the same employee, uncertain receipt recovery, exact replay, and a page which appears before slow session restoration. The camera stays outside the scrollable lower content. The short-screen check verifies component structure, not native rendering.

Fresh production-profile export: **1,835 modules**, **44 unique assets / 46 metadata references**, **6,882,447-byte Hermes bundle**, exit 0 in 17.566 seconds. Bundle SHA256: `a39c8fd1e5cd702a3810dc2802378b41ccfdd5b02b23f374d059857a3536ba14`. All **318 application inputs** were unchanged across the export; aggregate SHA256: `b5cf68973e0563650136bcc33892fc336080e904937f231317e76ec1863c09a8`. The resolved candidate remains `mezan-web-assembly-pilot`, Android86 / 1.0.71, `com.amasi.sa.mobile`, production API `https://mezansalla.com/api`, Expo project `f285e8fe-06a6-48b4-b095-f1c42d63f7c3`, and OTA disabled / `NEVER`. See `ANDROID-EXPORT.json`.

No physical-device camera timing or Android UAT was performed. A supplementary local browser-render attempt could not start because the environment refused Chromium's socket operation. Generated design images and synthetic camera-host tests are not device screenshots. No APK/EAS build, store submission, production deployment or live shipment receipt was performed.

The paired backend verification is now complete. Both implementation markers above are true; release and deployment markers remain false.

## Final backend verification and performance boundaries

Root reran the final frozen backend with `--asyncio-mode=auto`: **100 passed in 3.26 seconds**, with zero failures, skips or warnings, covering the new mobile handoff suite, legacy handoff, fulfillment contract, carrier labels, current-label guard and native permission-owner isolation. The new suite contributes **20 cases**, and its focused run with the nine legacy handoff cases also passed. Independent source/contract review accepted the final patch.

The new GET uses the existing `(user_id, order_date descending)` index and one streamed cursor. The normalized creation-date range plus missing/null/empty helper branch narrows discovery; native creation timestamps decide the exact 30-day eligibility. Metadata is processed in batches of 128. Only the eight visible rows load execution history and revalidate current custody/status. A 145-candidate test reaches page 19 without the old 100-row cutoff. Search totals and the two tab counters use complete eligible metadata, never the number of rows on the current page.

Early discovery excludes known store-courier/pickup facts but does not require the source order to carry a positive carrier name. Final external-carrier proof may come from the confirmed workflow. Regressions cover missing names, an unknown-name sentinel and a non-string source name before and after receipt; none hides a shipment with a valid workflow carrier.

The canonical receipt writer is unchanged in purpose and remains the sole custody writer. The HTTP route uses the existing operational owner transaction, rechecks the original employee's live authority after waiting for the owner fence, checks current completed status, and saves the receipt with the guarded workflow update plus the audit event in the same transaction. Legacy barcode-only requests remain supported; idempotency fields belong only to the handoff scan request model, not every carrier-barcode route.

New scan commands freeze `barcode`, `request_id`, `session_id` and `expected_scope`. Same-ID replay returns the same receipt. Reusing an ID for another barcode/session is refused. Different attempts against an already received shipment cannot add another count. A lost post-commit response is resolved by the saved receipt/session; five simultaneous same-command attempts produce one custody receipt and event. Session totals remain receipt history even after a shipment leaves the current queue. A pending receipt older than the latest receipt is recoverable. No financial ledger write is added.

Safety/performance limits are explicit: 8,192 candidates, a total read timeout of eight seconds and database operation budgets. Exhaustion returns an error without partial counts or rows. No polling, provider refresh, background backfill, financial writer or full order/product hydration was introduced. A nonempty normalized `order_date` that contradicts the native source and falls outside the widened indexed window is not repaired by this GET; missing/empty helpers are supported. This is an existing-data integrity boundary, not use of creation time as an execution date.

No production Mongo `explain`, live latency measurement or replica-set rollback test was performed in this workspace. The isolated tests validate the actual operational capability and owner serialization; the existing transaction implementation supplies production atomicity. No release lease was acquired and no production data was changed.

## Exact source checkpoint and remote release result

Mobile application source: `b115caf662023b68c5889333acbcfc76f545fdf5`, tree `f9d2e14e87cccdd50113b8ba946b553b8f2268f3`, parent `66271584c7fa81e47dbab59d798446a452f110c7`. The remote tree exactly matched the locally staged tree. All other parent files were preserved, and the local worktree was clean after hydrating the exact remote commit.

On this exact application source:

- [AMASI Mobile CI run45061236009](https://github.com/AMASI-SA/amasi-mobile/actions/runs/45061236009), job137327405108: completed/failure, `runner_id:0`, `steps:[]`.
- [Android combined release bundle run45061236074](https://github.com/AMASI-SA/amasi-mobile/actions/runs/45061236074), job137327405536: completed/failure, `runner_id:0`, `steps:[]`.

These jobs did not execute test/build steps. No billing, quota or administrative cause is inferred; no gate was bypassed and no redundant rerun was requested. The previously attempted Expo build UI did not provide a usable session. No EAS submission or APK link is claimed. The passing local Android export is recorded separately above.

Backend source: `5278f6958385937d218afe4735679152b8f433d7`, tree `4622502c91b47f04f7fa11dac63f89a8bea1c3b6`, parent `100fed6629eba4c4bc47d2500a294c0aea7f40cd`. The remote tree exactly matched the locally staged tree. The exact remote commit was hydrated locally, and the worktree was clean before this documentation-only handoff update.

Paired pull requests: [mobile #373](https://github.com/AMASI-SA/amasi-mobile/pull/373) and [Mezan #2050](https://github.com/AMASI-SA/AMASI-SA/pull/2050). At the final source read, Mezan #2050 is draft, `mergeable:false`, `mergeable_state:dirty` against PR base `9b4fb716b430c32073b5ea7abaeaff62646d9633`. That is the observed PR base, not an assertion of the live production SHA. This source checkpoint requires conflict resolution during coordinated integration and the v5 source/intent release procedure against actual production. The task branch itself is not a deployment candidate. No other conversation's release lease was changed.

Following handoff commits change only this status document. They preserve both frozen source trees outside this document and all 318 verified Android application inputs. Release must validate the actual selected release head and must not publish the older parent as containing this feature.

## Reproduce the focused acceptance

From mobile `frontend`:

```sh
node scripts/verify-carrier-handoff-session.cjs
./node_modules/.bin/tsc --noEmit --pretty false
./node_modules/.bin/eslint src/features/carrierHandoff
node scripts/verify-single-barcode.cjs
node scripts/verify-barcode-boundaries.cjs
node scripts/verify-ci-scanner-source.cjs
```

The optional `--contract=<synthetic HTTP fixture path>` adds the thirtieth mobile test; no live server or credentials are required. The new 29-case suite runs in Mobile CI and the Android86 combined release gate. The one legacy source assertion now traces the inline wrapper and real returned operation promise; all existing behavioral protections remain.

From backend:

```sh
python -m pytest -q --asyncio-mode=auto \
  tests/test_mobile_carrier_handoff.py tests/test_carrier_handoff.py \
  tests/test_fulfillment_v2_contract.py tests/test_fulfillment_carrier_label.py \
  tests/test_shipping_label_current_guard.py \
  tests/test_mobile_app_permission_owner_resolution.py
```

The new backend module/test paths and suite are included in the existing fulfillment workflow. Existing gates were retained.

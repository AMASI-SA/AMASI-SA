# Backend source verification complete — publication held

Verified source: `ec13c2c6a495b36cf5f70795428267d567634f86`
Source tree: `0a173f6ffb599dc4a5af09b4aa6403f1bd1ca1b4`
Repository: AMASI-SA/AMASI-SA
Task branch: `codex/notification-conversations-recovery-20261114`
Base: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1931
Recorded: 2026-11-14 UTC

## Outcome and authority

The remaining backend notification source is implemented, independently reviewed and verified. Production changed: **no**. The owner's publication hold is active. No production/canonical merge, deployment, EAS/APK build, OTA/store release, live notification send, production database read/write, release rehearsal or deployment lease was performed for this batch.

This evidence branch changes documentation only. It preserves the tested source above and is not a release intent or deployment handoff. The matching mobile candidate is `4763d3a2d627156c3aaad70ec0b237f7af44047d` in https://github.com/AMASI-SA/amasi-mobile/pull/356; its final native CI is still running at this backend checkpoint.

## Completed behavior

- Saved, scoped sender/action/message previews with bounded Unicode text and a complete4000-byte provider limit.
- Private per-instruction/assignee conversations, narrow read/reply endpoints, rendered-ID reads, idempotent replies, product context and completion history.
- Existing merchant authority consistently resolves linked employees; task membership does not confer tenancy or current notification access.
- Projection is attempted before enqueue; incomplete projection omits an unknown count. Retries preserve original event age. Active unread aggregation matches the conversation view.
- Only an installed Android client advertising amasi_android_rtl_v1 receives the data-only native presentation envelope. Ordinary old-client/iOS transport remains available.
- New rich sends recheck current grants/merchant and exact device/session binding after metadata/count awaits. Resolved conversations suppress unsent events. Accepted-ticket receipts do not hydrate or resend content.
- Genuinely missing legacy snapshots retain generic instruction-only taps without unresolvable notice/conversation IDs. Existing foreign/read/mismatched snapshots never gain that fallback.

## Verification

Exact-head isolated Mongo: **252 tests plus61 subtests passed, no skips**, exit0,27.03s test execution.
Job132576801059: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298715
The workflow used disposable loopback Mongo8.0.12 replica-set data, checked out the exact source and passed its explicit required-test/no-skips guard.

Root local final delivery/preview/presentation/snapshot suite:69 passed plus45 subtests, exit0.
Independent final legacy compatibility slice:6 passed plus5 subtests, no skips.
Independent during-count native grant revocation and merchant reassignment reproductions both resulted in zero provider submissions.
All provider boundaries were synthetic/intercepted. Earlier local missing-Mongo failures and the old incomplete conversation fixture are retained in STATUS.md as historical evidence; the final exact-head run resolves them.

All11 applicable repository workflows are successful:
- Snapchat Direct Active Publication: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298746
- Snapchat Settings Management V2: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298765
- Assembly Mobile Settings: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298680
- Qoyod Payment Freshness: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298710
- Mobile Order Tracking: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298715
- Mezan Release Readiness: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298691
- Security Gate: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298717
- G47 Focused Integration: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298755
- Operational Balance Web: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298696
- Operational Stock Eligibility: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298706
- CodeQL: https://github.com/AMASI-SA/AMASI-SA/actions/runs/43637298703

## Reviewed file manifest

The16 changed source/test/workflow/status paths were selected onto the exact base; unrelated current-base payroll/source-read, existing release intent and other work were preserved. The status document itself is subsequently extended by this documentation-only evidence revision.

| Tested path | SHA-256 at tested source |
| --- | --- |
| `backend/mobile_app_request_context.py` | `076d9ed3cad9c4dfaf2a5d08f60238e57eae2ffcd0626ecb628dccca2ee9bfc2` |
| `backend/operational_instruction_comments.py` | `c2548373549b60ac4fa33cac5773405569cc00ce28f5f1be359759bb592fd53a` |
| `backend/operational_instruction_conversations.py` | `af67c24be03a6f1ed14ae6f945219a9c081ee747ce33836e581610b9b30e3300` |
| `backend/operational_instruction_notifications.py` | `502466d7e24756b407f465f269f325bfe59ff0f816ca0fb6d6a914c18ec7df38` |
| `backend/operational_instruction_progress.py` | `a730c26b3e6d626aa286960b4a94fdcd9ac09fd31a3a732be1e9dfcfdc00fc17` |
| `backend/operational_push.py` | `55545528f2fbc18e49ca48ee741702be6f5681f60f97489fa3884bf59c9ff783` |
| `backend/operational_push_preview.py` | `cddc179762449e576c8261cdbdc051309490e1cdf25e204b9e52d0568ed1f39a` |
| `backend/order_tracking_notes_routes.py` | `9e831226f6587ef0f5ad4bd35ca7e623cc8cde306b9fa9742fddba992a1b4eac` |
| `backend/tests/test_instruction_conversations.py` | `d335ea58c8b52473496e2a8342a3c36fa3f66ac3bd4af69ad8ab355ccc4e24d7` |
| `backend/tests/test_operational_notification_preview_snapshot.py` | `e3810ab912b2163954d109b7bcb7fecea06aa666d51692b0981b001886971c2d` |
| `backend/tests/test_operational_push_preview.py` | `030e3c31da02e30047e362935053f24b47e60d5bba1456a79d6e4edeb9af41bf` |
| `backend/tests/test_operational_push_presentation.py` | `47425bdb059382873afe21e76cf7c0117dd51508c52987f731e9eaf147fbd692` |
| `.github/workflows/mobile-order-tracking.yml` | `9ed68a383213f450ee9251693422729a597077fbffca0df8920a1de4ab36a7b5` |
| `docs/operations/NOTIFICATION-CONVERSATIONS-RECOVERY-20261114/STATUS.md` | `03904ebb67b28a3a0ef086733023a00ab699bce529334ab55d85a9aacf593faa` |
| `backend/operational_instruction_accounts.py` | `6ea7c2f1c4f77e4bdc4c885e82458fc23d4d07cd0c3b97c4871cdc552468a168` |
| `backend/tests/test_instruction_account_scope.py` | `c770f430244164d021f6980b943f6db4edf9429ac1054a463367a34a5edabf77` |

## Continuation

Read this evidence record and PR1931, verify the task branch still points to the full tested source, and verify the matching mobile final source/tests before any later release action. Keep publication held. No action on Assembly barcode ordering is authorized; it was cancelled.

If publication is later authorized, refresh current production state and follow repository protocol v5 with a new reviewed source/intent handoff. Deploy compatible backend before a uniquely identified new native app build. Physical-phone delivery/UI acceptance has not been claimed.

Issue1006 is at capacity. A prior update was accepted by the connector but not reflected in independent readback; no visible new ledger entry is claimed and existing ledger content was preserved. This verified task branch, Draft PR and documentation-only evidence branch provide the durable task handoff.

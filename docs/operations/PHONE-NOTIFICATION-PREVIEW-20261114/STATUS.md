# Phone notification preview — 2026-11-14

## Owner hold and observed result

Status: unpublished source checkpoint; **do not merge, deploy the server, build an APK, send an OTA, publish a store release or send test notifications**. The owner is collecting additional fixes. No production state changed in this task.

The owner created a legitimate new notification and supplied screenshots showing an Android system notification, a launcher dot and an in-app unread increase from 26 to 27. This establishes delivery for that observed event on that device. It does not establish all foreground/background/locked states. The delivered title/body were generic and omitted sender, task and actual text. Earlier delivery-failure hypotheses are not established as the cause on this now-working device.

## Source and continuity

- Repository: `AMASI-SA/AMASI-SA`.
- Dedicated source branch: `codex/phone-notification-preview-20261114`.
- Checkpoint parent: `3e9dc3b25e41c663e001822a666323c802e5ec5a`.
- Parent tree: `355fdae884cf4a849b0acdf1c29081730556c6b0`.
- Work initially used `af9caabb3119aeb9cf450af0a8b89d376cef8c74`. The parent advanced via unrelated PR1928; its 11 changed paths do not overlap this patch. The six changed backend baseline files were updated and verified against their exact Git blob hashes before final integrated testing.
- The local directory is an isolated source snapshot, not a Git checkout. The foreign checkout used to read immutable base objects was not changed.
- The source commit SHA and CI links are recorded in the Draft PR and canonical Issue1006 after creation; this file does not claim a self-referential commit identity.

## Completed source behavior

1. Native Expo notification content is hydrated only from the durable event snapshot matching event, instruction, merchant and recipient. The event must still be unread and the recipient must pass current merchant/native notification-access checks. Owner and store-driver exceptions match the published inbox policy.
2. The preview contains the saved sender name, task/event, actual message excerpt and order context. New-task titles prioritize the action; message text precedes the order context so the collapsed preview begins with useful content. Input formatting/control characters are stripped; output uses bounded UTF-8 text and balanced Unicode direction hints. These hints are not a native alignment guarantee.
3. Saved event snapshots include action type and a message-version marker. Submission and completion callers save the actual response and authenticated actor, rather than reusing the original instruction. Rejection explicitly supplies its actor. Author names use the existing authoritative merchant resolver, including Employees V2 linkage; foreign names fall back to system attribution.
4. Acknowledgments and legacy response events use truthful event summaries. Legacy completion attribution and progress updates use system attribution; progress text comes from saved stage transitions. Current instruction edits or reassignment cannot rewrite a saved preview.
5. A final same-device-generation/binding lookup and active account/session check occur after rich-preview hydration and before provider submission. Account rebind or logout during hydration discards that send. An external provider request already in flight cannot be recalled.
6. Receipt polling does not hydrate or resend the snapshot. Rejected-ticket retries revalidate before a new send. Existing deduplication, generation rotation, age limits, navigation identifiers, sound, channel, priority and TTL remain in place. No old-inbox replay, bulk notification send or business task confirmation is introduced.
7. Existing read/foreign/mismatched events are discarded. A genuinely absent legacy snapshot retains the old generic path under its existing account/session/generation checks; it exposes no new preview content. Web push stays generic. Expo currently has no separate platform field, so this native path also applies to Expo iOS devices.
8. Provider data retains its existing allowlisted keys. Full documents, arbitrary metadata and evidence are not exported. The complete Expo envelope has a 4,000-byte guard, including duplicated title/body in data.

## Changed files

- `backend/operational_push.py`
- `backend/operational_push_preview.py` (new)
- `backend/operational_instruction_notifications.py`
- `backend/order_tracking_notes_routes.py`
- `backend/tests/test_operational_push_preview.py` (new)
- `backend/tests/test_operational_notification_preview_snapshot.py` (new)
- `.github/workflows/mobile-order-tracking.yml`
- This status document.

## Verification

Final local integrated result: **104 passed, 24 subtests passed, 7 skipped; exit 0**, 12.92 seconds. All seven skips are the existing sender-directory integration tests: `MZ2_TEST_MONGO_URI required; no production fallback`. The new preview and snapshot suites have no skips. The real-Mongo directory cases are now required without skips in CI; local skips are not reported as passes.

Exact local invocation (from `backend`, using the existing read-only interpreter at `/workspace/scratch/81f2839b514c/courier-test-env/bin/python`):

```bash
PYTHONPATH=.:tests PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 /workspace/scratch/81f2839b514c/courier-test-env/bin/python - <<'PY'
import sys
sys.path.append('/workspace/scratch/81f2839b514c/courier-test-env/lib/python3.12/site-packages/pip/_vendor')
import pytest
raise SystemExit(pytest.main(['--noconftest', '-p', 'no:cacheprovider', '-q', '-rs', '--tb=short',
    'tests/test_operational_push.py', 'tests/test_operational_push_preview.py',
    'tests/test_operational_notification_preview_snapshot.py',
    'tests/test_operational_instruction_notifications.py',
    'tests/test_instruction_comments_progress.py', 'tests/test_instruction_edit_flow.py',
    'tests/test_order_tracking_notes.py', 'tests/test_notification_sender_directory.py']))
PY
```

Independent source/test-design review found the hydration race described above; it was reproduced and fixed before this final run. Review confirmed no remaining material source blocker. Native visual acceptance is still pending.

Regression evidence already recorded: original delivery preview failed two integration assertions because the old payload was generic; the snapshot/caller suite failed five of six cases before its fix. Account rebind and session revoke during real preview hydration each reproduced a provider submission before the final binding guard, then produced zero submissions after it.

Tests use real routing, merchant and native-access helpers, synthetic data and intercepted provider requests. Local dependencies are reused read-only. The available vendored `requests` is 2.32.3; the existing CI installs the repository's pinned 2.34.2. No production database, device or provider is contacted. Local commands set `PYTHON_DOTENV_DISABLED=1` and use `--noconftest -p no:cacheprovider`.

The existing Mobile Order Tracking workflow now runs both new suites and the real-Mongo sender-directory suite, with minimum-case and no-skip checks. It checks out the exact PR head, uses a disposable loopback Mongo replica set and does not build or publish a mobile artifact. Final CI state belongs in the PR/ledger, not in an unexecuted local claim.

## Remaining acceptance and preserved work

- Strict right alignment in the native Android notification template remains unimplemented and unverified. An English system UI controls its template; Unicode text direction does not force layout gravity. A custom native presentation path needs separate foreground/background/normal-closed/locked validation and compatible handling of old APKs. Do not replace working notification transport with unverified data-only delivery.
- The launcher controls dot/number/color presentation. No universal yellow numbered launcher badge is promised. Badge-count/recovery defects collected in mobile PR355 remain separate work; this patch does not change badge behavior.
- No retrospective replay of the old unread inbox and no requirement to mark those items read are added.
- The previously published Android81 artifact and its frozen source are unchanged. Mobile PR348 and server PR1885 stay held; this task does not merge their conversation design or old release metadata.
- Source changes are not visible on the installed app until a later authorized, governed server release. This content change alone does not require rebuilding the currently working native client; any later native presentation change does.
- Future release requires a fresh current-base review and the repository's protocol-v5 source/intent handoff. No release intent, rehearsal or lease was created here.

## Next safe action

Preserve the source checkpoint and independent review in a Draft PR and Issue1006; inspect exact-head CI. Continue collecting owner-requested changes without publishing. Only after the owner resumes publication, combine and revalidate the approved changes, execute the governed release process and test newly generated legitimate notifications on the intended phones. Opening a notification must continue to navigate without completing or approving the business task.

# Frozen Google acceptance correction handoff

PR: https://github.com/AMASI-SA/AMASI-SA/pull/1357

| Contract | Exact value |
| --- | --- |
| Production base M | 4d4b218a5ce8343236eb3e81f43813f23d460a05 |
| Source A | 48183b1e4e0ee1b54d493870ea7ad6ff79c93666 |
| Source tree | 8651438472b1d99bf7136011baac7f7670215792 |
| Intent B | a71b326b9f80a74ef20bb967375404fa5513db08 |
| Intent B tree | eba4bcee874e152aac93466f9791cb43e4e59e9c |
| Intent blob | 0c7ddac50453ad7ff12fad86c2e09070f9b96c65 |
| Runtime | rg5-2fe4ef5d207627121b38131950de1c6fb296edaee257d4c01fe4e6d9c83e5239 |
| Frontend artifact | 2f86563ef41c28845e1ddad5bbd878133439bf246a29c24cd4471d873d8e463c |

The source/intent pair is immutable. B changes only release/release-intent-v5.json. A retains the production base's reviewed intent. The clean exact-B adapter rehearsal passed and created no lease. Preserve both commits; do not squash/rebase or append runtime changes after B.

## State and evidence

Source 37 and intent 40 checks completed without failure; separate Advanced Security CodeQL succeeded with zero new annotations. Backend 331 Google tests with real Mongo 8.0.26 and official v25 SDK/protobuf, frontend 92 tests in 14 suites and exact four-platform 43 tests passed. Source reproducibility/freeze and exact-B isolated package/health proof passed. PR is open, ready for review, unmerged and unpublished. No new lease or provider/financial mutation occurred.

These fixes align native/shared/URL account scope, reject stale account responses and avoid querying unrelated campaign-family inventory. AI preparation runs at most two provider reads concurrently and drains cancellation before close; editable generation uses low reasoning and bounded shared timeout. The whole AI endpoint still lacks one deadline and a sanitized per-request phase timing record. The retained production504 cannot be assigned a precise cause; legacy ad-sync process stalls are a separate preserved finding.

## Blocking boundary and next safe action

Code Server is at its per-session password wall. The user cancelled the secure credential request. Do not read/copy/email session passwords, retry authentication, reset access, use paid chat, or infer that static backend/release_identity.json is an operational lease. Support says no delegated web-app release service exists.

The previous PR #1355 matching lease was closed by successful public verification. Authenticated support reply at 2026-10-14 04:35:30UTC confirms exact original-checkout status exited 0 with active=false and no incomplete lease directory; no lease changed. Repeat this time-bound status before future work. Its command is:

```bash
cd /app
PYTHONDONTWRITEBYTECODE=1 python scripts/production_release_guard.py status
```

A future authorized authenticated continuation must first refresh this status and require active=false, preserve unrelated files/worktrees, and recheck the remote production base and PR checks. If publishing this exact pair, merge preserving the exact B tree, restore that merge in the actual workspace without reset --hard, rehearse its exact adapter without a lease, then follow AGENTS.md prepare/prepublish. A newer explicit successful deployment plus three matching public probes and inactive matching lease are required before live AI/product/validate-only/shared dashboard acceptance.

If a deadline/timing correction is needed first, make a new source from the current reviewed production base retaining its prior intent, incorporate the owned changes, verify, freeze a new intent-only commit, and supersede this pair explicitly. Never publish changed source with this intent.

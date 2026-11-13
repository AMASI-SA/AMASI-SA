# Post-deployment campaign snapshot refresh — source only

Base: 8d25f7a6fa9a310b319b753c32457e70987f7c51. Prior live guard verification passed; financial matching still has unresolved cases.

Confirmed defect: both pages watched only the campaign-ID set. A new order in the same campaign did not refetch the report. Backend TTL alone cannot refresh retained component state.

Fix: a shared bounded hook observes order IDs, status, amounts, quantities and totals; a new order revision gets a new tenant-scoped server snapshot. Repeated identical lists coalesce, old responses cannot overwrite the latest state, and no request is triggered on hover. A three-minute freshness bucket is noticed only through existing visible order refresh; no new polling/cron is added.

Tests: same campaign 30 to 31; unchanged lists; no independent polling; quantities/cancellations; delayed result; tenant isolation and request coalescing. Exact CI must pass before readiness.

Unresolved separately: TikTok visible name but no trusted campaign ID; Google campaign/account mapping; screenshot timezone America/Los_Angeles conflicts with earlier owner description America/New_York; missing Snap account-local spend. Do not guess campaign IDs from names/products or change account timezones. Read-only live evidence is needed.

No production merge/deployment, /app or release lease, provider sync, database or accounting/ad write. Preserve the accepted partial-spend policy. This patch does not claim the attribution/timezone issues fixed.

## Resumed source verification — 2026-11-14 (Riyadh)

The owner requested completion, direct Mezan verification and the previously
authorized update. Continuation is PR #1907; Issue #1006 has reached GitHub's
2,500-comment limit, so this task record and the PR retain the handoff.

The screenshot's reported day-start failure was superseded by exact-source
evidence. Do not change account timezones or alter `_window` without a fresh
reproduction. The USD Snapchat account's observed timezone is
`America/Los_Angeles`.

Recovered the exact nine-file final patch on task head
`5c08685d42811c1642a7acb2459d5225b5c27620`, using the original guarded scripts
from `a18ce6265325a979d3c797c9610c24e9de742e81` and
`442f2596635915edc604f36c58690d211a58a5db`. No hash guard was changed.
The resulting binary patch exactly matches `proposed.patch` in artifact
`14076642787`, run `43507119296`: SHA256
`13a6e8fbbd751cebb4e73f3719811117f7420f6469cc8b3024749800754c1391`.
The downloaded ZIP independently matches SHA256
`8ab8c05f63a3813ce8c6362836d5d0a86de644386c18d8fb91faad31c43ef6b0`.
That historical run passed 77 backend, 28 focused frontend and 3,693 full
frontend tests; its checkpoint push failed because its runner token lacked
workflow-write permission. A failed push is not a failed acceptance test.

Recovery checkpoint: `d1a0297e`. Latest production base
`53acc03a178362530f2cb45206fe11a4fb724d88` merged without conflict into the
isolated task checkout; integration checkpoint
`23903722c7e0a8a518e24875ca32301220084c5f`. All unrelated production changes
are preserved. No source from the isolated ops branches is merged.

Fresh integrated verification:

- Backend five-file campaign/native/Snapchat acceptance: 77 passed, exit 0.
- Frontend eight-file campaign/OrdersV2 acceptance: 28 tests / 8 suites passed,
  exit 0. Existing React test-environment warnings remain nonfatal.
- `git diff --check`: passed.
- Independent source reviews and final integrated CI/build remain pending.

Completed behavior: same-campaign new orders invalidate the snapshot; canonical
OrdersV2 totals/items participate in revisions; stale responses cannot overwrite
new results. Existing native Google/TikTok campaign snapshots and Snapchat v3
account-local daily facts supply bounded read-only evidence. Exact Salla UTM
labels without verified campaign IDs show commerce only, explicitly using the
store day, with spend/CPA/ROAS unavailable. No fuzzy name-to-ID matching, new
polling, provider calls, schedule changes or fabricated missing values.

Live baseline was inspected in Mezan: the existing card still rejects
Riyadh-day Snapchat spend for the Los Angeles account. This is baseline
evidence, not acceptance of the new source.

Production changed by this continuation: **no**. No shared `/app` changes,
lease, publication, provider/database/accounting/advertising writes. Next:
complete reviews and exact-source verification, freeze a new governed v5 A/B
pair against the latest production base, then serialize the already-authorized
publication and verify the actual runtime and cards.

### Final source preparation on the current production base

The recovered WIP and its ancestry remain preserved locally, and the original
guarded scripts/artifact remain remotely recoverable. The final source branch is
`codex/campaign-hover-final-20261114`, built directly above production
`53acc03a178362530f2cb45206fe11a4fb724d88`. Its initial scoped snapshot is
`c1d94301a69f835c0a4c842812d8f4d9a6267849`. This avoids importing an old intent
through merge history; the unchanged source-A classifier requires no intent-file
commits since J. No release guard or acceptance requirement was relaxed.

Independent frontend review found a real upstream integration defect:
`campaign_enrichment` inferred an advertising ID from any text of length 24+.
An actual OrderDTO containing a long Arabic TikTok UTM name reproduced the
failure before the UI. The minimal fix removes only that arbitrary-length rule;
explicit IDs and inferred numeric/UUID IDs remain supported. Six focused cases
cover label preservation and correct explicit identities; the existing Meta
lookup assertion now checks the tenant-scoped query rather than lookup order.
The regression was red (5 failures / 1 pass), then all 8 enrichment tests passed.
The normal source acceptance workflow includes this module and suite.

The combined final backend acceptance (six suites including enrichment) passed
85 tests, exit 0. Independent backend review also passed 53 existing campaign
tests and confirmed the native writer schemas, tenant/account/day boundaries,
FX evidence, incomplete-value semantics and bounded read-only queries. Focused
frontend acceptance passed 28 tests / 8 suites. Full integrated frontend and
fresh CI remain required before final readiness.

The existing Security Gate exposed GHSA-vvf4-9x63-8xvv in dnspython 2.9.0.
Both deployed dependency pins now use the maintainer's fixed version 2.9.2;
`backend/requirements.txt` and `scripts/release_backend_requirements.lock`
remain byte-identical. Source advisory:
https://github.com/rthalley/dnspython/security/advisories/GHSA-vvf4-9x63-8xvv.
The strict audit is unchanged; no exception or ignored vulnerability was added.

Direct Git push from the scratch shell has no configured GitHub write
credentials. Save the exact reviewed tree through the connected GitHub API,
verify tree equality, then use its canonical remote source SHA for all governed
builds and the intent. Do not claim a local commit was uploaded until the remote
branch has been read back. Production publication and live acceptance remain
pending; no shared workspace, lease or business data has changed.


## Continuation checkpoint — 2026-11-13 23:14:53 UTC

**Merged and release-ready; production publication is BLOCKED by the Emergent agent-session state. The new campaign-card release is NOT live. The owner release lease has been closed and final guard status is inactive.**

This record is saved on a separate documentation-only continuation branch. It is not a deployment candidate. Preserve the frozen source/intent/merge commits; do not merge this documentation change into the current release, amend the release, or regenerate its intent merely to record status.

### Immutable reviewed release

| Role | Identity |
| --- | --- |
| Baseline J | `53acc03a178362530f2cb45206fe11a4fb724d88` |
| Source A, sole parent J | `9fc331b8cd6b3c77129090c9661af5fa9d0ca186` |
| Intent-only B | `4e31be12c3f6053d07a946f0cfd7f2639e1d75d9` |
| Ordinary merge M, PR #1911 | `5d158f6006b63ac7663c293696d501f595ce8754` |
| Shared B/M tree | `4dd3da0ee493c1de15b4a2f1c0a6438271f8baa5` |
| Intent SHA256 | `199a4bc20b5e9849a0e7b9b7e2da518464d484aff4536e452a103851d5192fb2` |
| Frontend artifact SHA256 | `9a34bf9ba4d65a89b3431002ab8e729d6b5311a332ec17800abffb88c2406ab6` |
| Reproducibility proof SHA256 | `172a7ab404359fc148111d42d2dc3f19ad4abdc6526c81ca913fd593e8f66f3c` |
| Intended runtime release ID | `rg5-b5c7997f2bac6f0278d492c1ef8e47deeefcbe2dc043cb32a104828a68e825e7` |

PR #1911 was merged at 2026-11-13T22:55:58Z, preserving A/B ancestry and exact B tree. The frozen task branch remains `codex/campaign-hover-final-20261114`. Older #1907 is superseded and must not be merged separately.

### Completed behavior and acceptance

The integrated source observes existing order-reader revisions for same-campaign order/count/quantity/total changes, ignores stale responses, reads bounded saved native Snapchat account-day/TikTok/Google evidence, and preserves commerce-only Salla labels when no trustworthy ad ID exists. A separate OrderDTO enrichment defect that treated long readable labels as IDs was corrected. dnspython is pinned to 2.9.2 in the identical runtime/lock files without an audit exemption.

- Fresh integrated backend: **85 passed**.
- Focused frontend: **28 passed /8 suites**.
- Fresh complete frontend: **3696 passed /400 suites**.
- Exact-B Operational Balance: **3802 passed**, followed by **16 passed +18 subtests**.
- Exact-B final rollup: **50 success /10 expected skips /0 failed or pending**.
- Strict backend/frontend dependency audits, both CodeQL languages, and Node20/Node24 clean-clone adapter rehearsals passed.
- Local and GitHub source-A intent candidates were byte-identical; official candidate and committed-B validators passed.
- One source-A concurrent driver-approval test failed once. The unchanged baseline and B passed the same suite. That job does not collect the added campaign tests or use the changed dependency files. No operational-source change, waiver, skip or retry of the failed A run was used; its underlying concurrency cause is not claimed to be fixed.

### Actual Emergent preparation

- Separate clean-clone B adapter rehearsal passed in **88.04 seconds**.
- Shared `/app` advanced to M via fast-forward, with tracked files clean. Its 120 pre-existing untracked files had no incoming-path collisions and were preserved.
- The first shared-app adapter invocation refused prepackaged bytecode. One ignored `__pycache__` directory containing two regular generated `.pyc` files was validated as untracked/ignored and moved, not deleted, to `/tmp/mezan-campaign-hover-bytecode-s4oogutw`.
- The fresh official `cd /app/frontend && yarn build` passed in **61.74 seconds**, with exact artifact/identity and isolated package proofs (backend 1205 files; frontend 1469 files). Log: `/tmp/mezan-campaign-hover-app-build-clean.log`.
- Official prepare succeeded at **2026-11-13T23:04:24.717990+00:00**, actor `codex-campaign-hover-final-20261114`, source A and deployment M.
- Official prepublish returned `ready_to_publish=true` for the intended runtime identity.
- Evidence files: `/tmp/mezan-campaign-hover-prepare.json`, `/tmp/mezan-campaign-hover-prepublish.json`.

### Observed publication blocker and safe cleanup

One **Publish changes** action was refused with:

> Agent is Running — Please wait for the agent to finish or stop it manually before re-deploying.

The view displayed Loading/Thinking and a disabled pause control. Automatic approval review rejected closing the project tab because it could disrupt an active session and lose non-trivial state. No agent/project session was stopped or closed, and no indirect bypass was attempted. A separate read-only view also showed the loading state and disabled pause control. No paid chat, support message, credit purchase or settings change was made.

No newer Deployment Succeeded was observed. The displayed published version remained the prior 100th publish, `11d385a`. A read-only production health check after the refused action returned:

- `ok=true`
- Source: `47e474fa896e09db353b9928261fe96186ea24e1`
- Release: `rg5-8bd428e3e93319a15361587b91a09dbd5b0f97f6bbea49d0fd8b4d4343166b9a`
- `verified_identity_available=true`, `critical_file_hashes_match=true`, `frontend_build_verified=true`
- Boot: `2026-11-13T20:41:20.652963+00:00`
- New campaign release live: **false**

This is read-only evidence for the previous release, not the required three-probe verification of A. The new release's production verification and live campaign-card acceptance remain **NOT PERFORMED**.

Because publication did not proceed, the owner used the official guarded abort with expected deployment M and the exact intended release ID. It returned `aborted=true`; the subsequent official status returned **`active=false`**. Only this task's lease was closed. Evidence: `/tmp/mezan-campaign-hover-abort.json`; live status: `/tmp/mezan-campaign-hover-live-status.json`.

### Exact next safe action

1. Resolve the Emergent agent-session blocker through the normal UI. General deployment authorization is already present, but no explicit authorization to stop/close the active agent session was found; ask the owner for that specific approval before attempting it.
2. Do not repeat Publish while the same agent-running condition remains. Do not modify platform state through hidden APIs or terminal workarounds, use paid chat, buy credits, or clear someone else's lease.
3. Before any new shared-workspace change, run the official guard status and require inactive. Re-read GitHub production HEAD, shared HEAD/tree, clean tracked state, and reviewed artifact identity. If M/source changes, stop and reassess the frozen release rather than assuming this evidence still applies.
4. Once the UI blocker is resolved and the exact release remains valid, run official prepare and prepublish again for the intended source A/deployment M, then perform one authorized publication.
5. Require a newer explicit Deployment Succeeded, official `production_release_guard.py verify --url https://mezansalla.com` with three matching probes, and final inactive guard status.
6. Inspect live campaign cards for native account-day spend and commerce-only labels, and distinguish observed behavior from the 30→31 automated regression. No order/ad/accounting writes or manual provider sync are authorized for this verification.

Issue #1006 has reached comment capacity. PR #1911 comments plus this continuation record carry the current handoff. Production changed by this task: **NO**; source merged and shared preview/deployment workspace prepared: **YES**.

## Resumed publication coordination — 2026-11-13 23:35 UTC

The owner explicitly approved closing/reopening the `salla-analytics` project session and stopping its coding agent if needed. Through the normal Emergent UI, the internal project tab was closed and reopened successfully. The previous initial-message/Thinking view is no longer shown; Manage and the free Republish dialog load. No paid chat, credit purchase, agent request, hidden platform call or new Publish action has occurred in this resumed turn. The previous permission blocker is resolved; this is not yet evidence that publication would succeed.

Fresh shared-workspace inspection found a newer production candidate owned by the concurrent Ready task:

- Production and clean tracked `/app` HEAD: `304e8793a15fd3269fd6f6d4cfba903d1760e285`, PR #1912.
- New source: `cdf175841376d2f68605d66c1979273d23aed70d`; base is our campaign merge `5d158f6006b63ac7663c293696d501f595ce8754`.
- New intent B: `a948f4592fd725792fe354dfa72fd40e99000915`; B/M tree `baf88eeeb7eec15a043da14358fa77f2ba2bc149`.
- New intent SHA256: `2c4868e9312d9633cf536d908d5799892f7ac0156f458397bd40e50f91cdfabc`.
- New runtime identity: `rg5-3e41b3f0b14d180f45cd3b242798ea348797b06e8edc770592130ac87b2a9cb0`.

The descendant preserves every campaign implementation/test/frontend/dependency change. Its only source additions are the Ready repair, its tests and status record. Exact new-B CI was independently read back: 18 successful workflows plus one expected skip; 36 successful check runs and six expected skips, none failed or pending. PR #1912 records its isolated adapter rehearsal passed in 77.44 seconds.

The Ready owner's rollback branch advanced to intent B_R `28adddb16c9e5b17bdd69e88c15be4a7451fbc18` at 23:31:48 UTC. A subsequent read-only shared-process snapshot found a current `yarn build`, `build-entry.cjs` and governed toolchain process. Official guard was inactive while that build was underway. Canonical generated build/identity outputs were absent during the adapter, which intentionally removes inherited outputs at entry. This campaign task has not modified the newer workspace, run another build, acquired a lease, or interfered with the active owner.

A fresh read-only production health response still showed the prior source `47e474fa896e09db353b9928261fe96186ea24e1` and release `rg5-8bd428e3e93319a15361587b91a09dbd5b0f97f6bbea49d0fd8b4d4343166b9a`, with `ok=true` and verified identity/hash/frontend flags. No campaign deployment success is claimed.

Next safe action: let the current Ready release owner complete its governed build, required rollback rehearsal and serialized publication. Any fresh lease and production verification must use its new source/deployment/release identity above, not this task's superseded candidate. Following successful official 3/3 verification and inactive guard, complete read-only live campaign-card acceptance. Do not restore old M/intent, clear another owner's lease, repeat a concurrent Publish, trigger provider sync or write order/ad/accounting data.

Production changed by this campaign continuation: **NO**. New release verification and live card acceptance remain pending. This documentation-only branch remains outside the deployment candidate.

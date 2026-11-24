# INVENTORY-LOCATION-CODES-20261124

## Current status

**COMPLETE — the numbering change is merged, published and verified on production.** The unchanged original verifier completed all three probes at **2026-11-24 19:47:41 UTC**, returning exit 0 and `verified:true`. The immediately subsequent original guard status returned **`active:false`**. Independent final evidence review passed.

The live create-cabinet form shows the new numbering guidance. An unsaved preview of **one row × three columns** correctly showed **three slots**, then was closed without creating anything. The owner can now create the cabinet themselves. No live cabinet creation/deletion or inventory/financial action was performed by this task.

Complete immutable identities, checks, receipts, hashes and recovery history are in [RELEASE-EVIDENCE.json](RELEASE-EVIDENCE.json). This summary replaces the earlier chronological status stack. The complete history remains in the evidence branch commits.

## Agreed behavior and scope

The owner chose **cabinet → column → row** and will create the cabinet themselves. New cabinets created through the updated form use `cabinet-column-row`, including cabinets created inside an existing legacy warehouse. Three single-digit coordinates are concatenated; if any coordinate has multiple digits, all three are separated with hyphens.

| Cabinet | Column | Row | Visible code |
| --- | --- | --- | --- |
| 1 | 1 | 1 | `111` |
| 1 | 2 | 1 | `121` |
| 1 | 2 | 3 | `123` |
| 1 | 12 | 3 | `1-12-3` |
| 11 | 2 | 3 | `11-2-3` |

Rows and columns remain user-selected, including one row. The shared warehouse counter allocates above existing valid cabinet numbers across sections and statuses. If cabinet 1 remains, the next cabinet may be 2, with one-row labels such as `211`, `221`, `231`; this change does not reset the counter.

Short labels reach the map, list, search, details, printing and relevant location selectors. Existing location IDs, full scanner/barcode/QR values, legacy labels and historical records retain their meanings. **No live cabinet or location was created or deleted, and no inventory quantity or financial record was directly changed by this task.** Creation and possible removal of the old cabinet remain deferred to the owner's later action. No migration or warehouse reset is included.

## Frozen release identity

Repository: `AMASI-SA/AMASI-SA`. Production branch: `hotfix/prod-snap-meta-final`. Release branch: `codex/inventory-location-short-codes-release2-20261124`.

| Role | Exact identity |
| --- | --- |
| Prior reviewed base J2 | `20fc28f3432a78996f1d70f3356a38bd78b0309e` |
| Source A2 | `a611477be8d52b18f9ad851f44facf9c78be2905` |
| Intent-only B2 | `6005a1be25a7a9402442a389f3052ffab8664e07` |
| Deployment merge D | `cb98e5660380d362e78f937d60abc7139cd1c462` |
| B2 and D tree | `abcdea0a41cb694cdd229492a96a7b68e25d8a3a` |
| Runtime release ID | `rg5-1a6146f1c27f7bf287f7185591bcbfa1f88ea0ecc066ae398b4bbb3c4259b72e` |

[PR2113](https://github.com/AMASI-SA/AMASI-SA/pull/2113) merged normally at **2026-11-24 17:30:16 UTC**, with parents exactly `[J2, B2]` and D's tree equal to B2's. A2 has sole parent J2 and exactly 20 owned changed paths; the 19 non-status blobs match the previously reviewed feature. J2→A2 never edits the prior intent; A2→B2 and A2→D change only the reviewed intent. Independent source, manifest, candidate and ancestry reviews passed.

The [frozen source status](https://github.com/AMASI-SA/AMASI-SA/blob/a611477be8d52b18f9ad851f44facf9c78be2905/docs/operations/INVENTORY-LOCATION-CODES-20261124/STATUS.md) retains the full implementation and fixture rationale. Superseded PR2106 and PR2110 are closed and unmerged. Evidence updates belong on `codex/inventory-location-codes-evidence-20261124`; that branch must not be merged into the frozen release.

## Verification completed

| Boundary | Observed result |
| --- | --- |
| Exact A2 feature checks | 56 focused backend tests and 10 tests against an owned MongoDB 7.0.14 replica passed, with no skips. |
| Governed source build | Node 22.23.2 / Yarn 1.22.22 completed two clean builds with identical artifacts and proof. Original build verifier, freeze and candidate validator passed; normal tracked B2 validation passed without bootstrap override. |
| Exact B2 CI | 56 check records: **46 success, 10 conditional skips**, no pending, failure or neutral result. Source integration passed all 17 steps, including **491 frontend suites / 5,552 tests** and **290 backend tests + 237 subtests**. Operational, stock, security and other applicable gates passed. |
| Exact deployment D CI | **11/11 checks succeeded**, with no skips, across [Readiness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45449291028), [Security](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45449290986) and [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45449291302). Host Node 20.20.2 and 24.19.0 adapter rehearsals each passed 21/21 steps with the frozen identity and proof. |
| Canonical `/app` rehearsal | **40/40 steps passed**, observed **19:13:28 UTC**. The unchanged original adapter, tracked intent, frontend proof, isolated package boundaries and final production-ref checks all passed. All 432 current foreign entries were identical. |

No required gate was waived or manually rerun to conceal a failure. Full check records and raw-evidence references are in JSON fields `ci`, `ci_key_results`, `deployment_ci`, `normal_tracked_B2_validation` and `latest_canonical_rehearsal`; see also [TRACKED-INTENT-VALIDATION.json](TRACKED-INTENT-VALIDATION.json).

Canonical receipt: `/app/.git/mezan-pr2113-recover-d-vb9syvia/receipt.json`, **7,805 bytes**, SHA256 `0385a7ebd81bac2b5b12b32b7a4356df7b2506a771bd0518c3c7d641451d8efb`. Full artifact/build-meta/proof and isolated package identities remain in `latest_canonical_rehearsal`.

## Failures resolved and preservation limits

- The first host rehearsal was interrupted by an editor disconnect; its `/tmp` receipt and before-snapshot were lost. Its outcome and historical byte preservation remain **unknown**. Recovery used exact D and a fresh baseline with persistent `.git` receipts, without reset or rollback.
- The next canonical install failed with **ENOSPC**. Its final receipt write could not persist and stayed `RUNNING`; terminal/log evidence establishes failure. Foreign workspaces were not cleaned.
- Initial pack preparation also hit ENOSPC because Git wrote a temporary file into the app object store despite a `/tmp` output prefix. The incorrect `app_writes=false` field was corrected, and its 1,179,648-byte temporary artifact was retained. Corrected preparation used a private `/tmp` object database and separate reconstruction without alternates.

Capacity APPLY passed after verified archiving of only owned generated outputs. The original dependency backup was durably archived before its raw copy was removed. Exactly **5,760 redundant loose copies** were pruned after verified pack installation; all **82,750 logical Git objects**, old packs, refs, indices, worktree metadata and 432 fresh foreign records were preserved. The original archive, restore manifest and receipt remain in `/app/.git/mezan-pr2113-capacity-applied-wdr7ug6g`.

Available app space measured 706,215,936 bytes after APPLY and 260,780,032 bytes after the subsequent successful canonical rehearsal. These readings do not guarantee future build headroom. Successful fresh-baseline checks do not retroactively prove preservation across the interruption. Full failures, corrections and recovery receipts remain in JSON fields `host_recovery`, `pack_preparation`, `capacity_apply` and `latest_canonical_rehearsal`.

## Publication and final completion record

Original `prepare` passed at **19:16:56 UTC** and `prepublish` returned `ready_to_publish:true` at **19:18:44 UTC**. One authorized **Publish changes** submission followed. The platform then explicitly reported **Deployment finished Nov 24, 10:29 PM**, with new published identifier **`7fe1672`**. The UI displayed “100th publish”; newer success is established by the later timestamp and changed identifier from prior `d0a7e31`, without assuming an ordinal increment.

| Final condition | Observed evidence |
| --- | --- |
| New platform success | **PASS** — `7fe1672`; displayed Nov 24, 10:29 PM (Riyadh). |
| Original production verifier | **PASS** — invoked once, **19:30:41.880782–19:47:41.892352 UTC**, exit 0, `verified:true`, `checks:3`; exact A2/J2/D/RID/protocol5. |
| Public frontend files and metadata | **PASS** — **770 requests per probe, 2,310 total** over the governed public files, canonical and cache-busted. Build metadata returned the exact governed **591,784 bytes**, SHA256 `b8aad6fcdd48f782ff9e8deaf57e1b701bedf567b7dfa2b27f532638e5323433`, with `application/json`. |
| Matching lease closed | **PASS** — the successful original verifier closed its matching lease; immediately subsequent original status returned **`active:false`**. |
| Live form preview and cancel | **PASS** — optional cabinet name, row and column fields, cabinet→column→row guidance and example `121`; unsaved **1 row × 3 columns → 3 slots**, closed using **إغلاق**. The create button was never clicked. Existing cabinet and nine legacy slots remained displayed. |
| Independent final evidence review | **PASS** — `numbering_release_review`; no extra build, CI, probe or publication required. |

The complete verifier output is retained at `/app/.git/mezan-pr2113-publication-o4xyp2sz/verify.log`: **43,129 bytes**, SHA256 `a2f3167afcfaf2c72d2a9971c768159696d566b2aa9f662519ca7f46fe34e6d2`. It observed two worker boot timestamps, `19:28:52.814653+00:00` and `19:28:52.835041+00:00`, both after preparation and both bound to the same release. HTML shell cache headers and the two retirement service workers passed the unchanged verifier's checks. Individual probe timestamps are not separately emitted by the original result and are not inferred.

Publication evidence is under `/app/.git/mezan-pr2113-publication-o4xyp2sz`. The former lease owner was `chatgpt-inventory-location-codes-20261124-pr2113`; no active lease from this task remains.

## Owner's next step

Open [the warehouse page](https://mezansalla.com/components-v2?workspace=warehouse), choose **إضافة دولاب**, select **one row** and the desired number of columns, then create the cabinet. Number allocation is automatic; the examples above describe the format and do not promise a particular next cabinet number. The previous cabinet has not been deleted. Any later cleanup remains a separate user-directed action after the new cabinet exists.

No further deployment or verification work is pending for this change. The task's bounded [Issue #1006 ledger section](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123) records completion; source and candidate identities remain frozen.

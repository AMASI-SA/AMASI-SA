# Courier delivered-status release — completion record

**Task:** `COURIER-DELIVERED-STATUS-20261118`
**Repository / PR:** [AMASI-SA/AMASI-SA #1986](https://github.com/AMASI-SA/AMASI-SA/pull/1986)
**Release evidence:** canonical PR checkpoint updated **2026-11-18 02:27:54 UTC**, checked against the owner's compact verification result. Subsequent live UI observations, saved exports and the final independent cohort comparison are recorded below from the owner's confirmed results. All times below are UTC unless explicitly marked Riyadh.

**Current state:** production publication, selected live functional acceptance and the independent saved-cohort comparison are **accepted and complete**. The single unchanged official verifier exited 0 with `verified:true`, `checks:3` and 594 public requests per probe; subsequent official status exited 0 with `active:false`. The shared-host publication handoff is complete. The selected case's pending state was resolved and remained resolved after a full browser reload. This record and its verification receipt form the final evidence-only Git checkpoint. No further code change or release is required for the accepted behavior.

This is an evidence-only completion record on `codex/courier-status-policy-release-evidence-20261118`; it must never be merged or deployed. It must not change the frozen source/intent pair, deployed source, release protocol or tracked task status in that frozen checkout. It contains no customer/order identifiers or business amounts. Actual-host and platform observations below are attributed to the release owner's canonical checkpoint and compact result; independent review covered that compact result, Git provenance, candidate/intent validation, CI rehearsal logs and protocol requirements.

## Scope and immutable release identity

Current authoritative Salla delivered status suffices for delivery recognition when `started_at < order_created_at <= as_of`. Independently proved actual delivery dates are retained; the status fallback does not invent a delivery timestamp. Orders at/before the opening boundary, existing dated financial facts, executor/source authority, currency, shared-waybill checks, tariff pricing, COD collection and completion evidence retain their separate controls. The exact-target receipt and sanitized timing fixes are included. [Behavior and review record](https://github.com/AMASI-SA/AMASI-SA/pull/1986).

| Identity | Exact value |
|---|---|
| Production branch | `hotfix/prod-snap-meta-final` |
| Task branch | `codex/courier-status-policy-v2-20261118` |
| Published predecessor base J | `557c6c52857b0a09feccacd0cdf9bf6ce9d4476e` |
| Source A | `f1189678bddc4c47166692bde8b2e8913a7b6683` |
| Source tree | `46c3bc47252dd5e89c9503bea6bd3d22922ccd13` |
| Intent-only B | `eba3298a660ee236e50142a915e70b23d477d046` |
| Deployment merge M | `37a8211c383079711839557ecad6d5f3f0cdd51f` |
| B / M tree | `430fb6bae82ae3c89233a4428296a0b15d79db24` |
| Reviewed intent blob | `db9968a633876d8f7cbbde70773f6b6574b741c7` |
| Runtime release ID | `rg5-b295fb4dba1d9014e719cb0987af36e2872b49b09f65ed22fef8dcc4d94a2d5b` |
| Frontend artifact tree SHA256 | `8f7f12e96c3986e3319fffeae2cb57abbb439a95d605332a3984a0efe974a6e2` |

Independent canonical Git review confirmed A's sole parent J, B's sole parent A, ordered merge parents `[J, B]`, M's exact B tree, A ancestry, an intent-only A→M content delta, and no full-history intent change J→A. Ordinary merge completed at **01:36:13**. The source reconstruction preserved all 5,882 unrelated base entries and all seventeen reviewed code/test blobs; only the task status record differed from the previously accepted source. [Merged PR](https://github.com/AMASI-SA/AMASI-SA/pull/1986).

## Completed source, CI and build evidence

Exact final-B acceptance: **14 workflows: 13 success, one unrelated conditional TikTok skip; 31 checks: 25 success, six conditional/manual skips.** No pending, failing, cancelled or neutral check remained at merge. Review threads/reviews/comments were empty. Actual CodeQL check **134505842237** succeeded with no new changed-code alerts and zero annotations. [Final acceptance checkpoint](https://github.com/AMASI-SA/AMASI-SA/pull/1986).

| Gate | Completed result / evidence |
|---|---|
| [Operational Web 44215801928](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44215801928) | Job 134505193373: 5,308 main tests, then 16 shipping/calculator tests and 18 subtests; explicit no-skip/failure/error XML gate passed. |
| [Stock 44215801931](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44215801931) | Job 134505193038: 4,989 tests and seven subtests; explicit no-skip/failure/error XML gate passed. |
| [Security 44215802006](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44215802006) | 4,313 frontend tests in 417 suites; unchanged production dependency audit: zero advisories. |
| [CodeQL 44215802022](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44215802022) | Actual scan check 134505842237: success, no new alerts, zero annotations. |
| [Readiness 44215802064](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44215802064) | Actual host Node 20.20.2 job 134505677083 and host Node 24.19.0 job 134505676905 independently reviewed: exact B, clean clone, absent generated inputs, no-Git adapter, governed Node 22.23.2 / Yarn 1.22.22, two equal builds, isolated packages and exact metadata/cache route checks. |

Root and independent official candidate/tracked-intent validators passed. Complete manifests contain **1,240 backend, 304 release-control and 1,240 frontend source files**, with all six critical hashes verified. Official candidate: [Readiness 44214325391](https://github.com/AMASI-SA/AMASI-SA/actions/runs/44214325391), artifact 14387456940; exact JSON 1,345,987 bytes, SHA256 `4c7f925457891b0cc481915735d101902755a6b8910c8ee770fefe3c15eec26e`.

| Built evidence | Bytes / SHA256 or tree |
|---|---|
| `build-meta.json` | 493,191 bytes; `e39703c7b26f9dc1efd24854a364a52f00b55d4527c7e3a8eeeb4310c98ea7ba` |
| Reproducibility proof | 1,521 bytes; `25a6b4fdac63a7a8552765b77a577100faea25e54da48bc166769a329a89c0f3` |
| Backend runtime identity | 446,429 bytes; `bf48231094feff2be6c78097a47da9096c3f726325e7fc30e995db33f2eebecf` |
| Isolated Backend package | 1,241 files; tree `5522916b1145fd1689d5fef86a96513d4c2c4b57e45aff5b3ebd85c8129bf721` |
| Isolated Frontend package | 1,536 files; tree `35bff937663b9c171d48a198f4f57b4ebbee082a1dd387baad92da80c2e242a2` |

## Actual-host and publication milestones

The [canonical owner record](https://github.com/AMASI-SA/AMASI-SA/pull/1986) establishes the following sequence:

| Time, 2026-11-18 | Milestone |
|---|---|
| 01:44 | Predecessor owner handed off after its successful three-probe verification and inactive guard. |
| 01:47:56.467091 | Fresh official status `active:false`; exact predecessor checkout, tracked clean; 120 unrelated untracked paths inventoried. |
| 01:49:42.567296 | Guarded shared-host fast-forward to exact M; tracked clean and all 120 unrelated paths preserved. |
| 01:52:38.311072–01:53:49.644801 | Actual configured `cd /app/frontend && yarn build` adapter passed first attempt, exit 0. Official frontend and isolated-package verifiers exited 0. Generated identity and evidence hashes matched the reviewed intent/CI. |
| 01:59:55.127449 | Own prepare succeeded for actor `COURIER-DELIVERED-STATUS-20261118`, with exact A/M/runtime. |
| 02:00:16.830853 | Official prepublish exited 0 with `ready_to_publish:true`. |
| Before 02:01:01 | One authorized free Publish clicked; controls showed Publishing and were disabled. |
| 02:10 platform minute / 05:10 Riyadh | Explicit **Deployment finished**, new platform version **`5829f12`**, replacing `9f62e46`. Version/time identify the publication; the displayed ordinal is not relied upon. |
| 02:10:24.303200 | Preliminary health observation's new backend boot timestamp. The final verifier separately recorded `02:10:24.979301`. |
| 02:13:58.115884 | All four canonical/cache-busted × `application/json` / `*/*` metadata variants passed exact bytes, SHA256, JSON MIME and parsing. |
| 02:14:51.010795 | Unchanged official three-probe verifier launched **once**, detached wrapper PID 205165. |
| 02:14:51.048897–02:25:44.514091 | Recorded verifier execution: exit 0, `accepted:true`, `verified:true`, `checks:3`; 594 public requests per probe; exact A/J/M/runtime/artifact accepted. |
| After verifier completion | Subsequent official status exited 0 with `active:false`. Exact M remained checked out and tracked clean; all 120 unrelated untracked paths were preserved. |
| 02:27:54 | Canonical PR updated with explicit completed production acceptance and shared-host handoff. Subsequent live acceptance is recorded below. |

The first preliminary host health request returned HTTP 520 and did not launch the official verifier. Subsequent browser health and unchanged guard transport succeeded with intended identity and all verification flags. No source, expected bytes, guard, security rule or second Publish was used to address that preliminary response.

Owner evidence is under `/tmp/courier-status-1986-release`, including adapter, package/build verification, prepare/prepublish and platform/preliminary-read records. The completed run retains `verification-runner.py`, `verification.pid`, `verification-start.json`, `verification.stdout.json`, `verification.stderr`, `verification.exit`, `verification-result.json`, `verification-summary.json` and the final official status. The independently inspected compact copy is `/workspace/scratch/da074d9c9496/courier-1986-verification-summary.json`. These paths locate the owner's evidence; the sanitized accepted result is also recorded in the canonical PR.

Full owner verification-result SHA256: `acec729e8699bc2ccfee5974cd8ec81faa4ced15f3f83d6dc6c087d6c62dec05`. Preserved unrelated-file inventory: 120 paths, SHA256 `2a0200fc4ebb665ebc1fec275ef8db46870809282ef7fcc40a931e631f74eb04`. The compact result confirms exact source/base/deployment/runtime/artifact values in the identity table, `head` equal to M, `tracked_clean:true`, `untracked_preserved:true`, and final verifier boot `2026-11-18T02:10:24.979301+00:00`.

## Accepted release and live acceptance

| Required completion evidence | Current state |
|---|---|
| Official verifier completion time and exit status | **PASSED** — 02:25:44.514091; exit 0. |
| `verified:true`, `checks:3`; exact A/J/M/runtime/artifact | **PASSED** — compact result and canonical PR agree. |
| Complete public frontend verification: 594 requests per probe | **PASSED** — three successful probes. |
| Final official status `active:false` | **PASSED** — status exit 0. |
| Tracked cleanliness and preservation of unrelated inventory after verification | **PASSED** — exact M, tracked clean, all 120 unrelated paths preserved. |
| Shared-host publication handoff | **COMPLETE** — explicit canonical checkpoint at 02:27:54. |
| Selected live functional match and recognition-basis review | **PASSED** — persisted after a full browser reload; selected case absent from both pending sections, with unchanged recorded basis/time/base fee and no duplicate financial evidence. |
| Same saved-cohort comparison and assessment of remaining independent reasons | **PASSED** — independently verified using private exports; financial details unchanged and remaining pending rows stable. |
| Live-acceptance handoff | **COMPLETE** — observed result and independently verified comparison recorded below. |
| Final evidence-only remote Git checkpoint | This status record, adjacent verification receipt and public runtime screenshot; exact commit recorded in PR #1986. Never merge or deploy this branch. |

## Verified live behavior

Live acceptance used **two selected UI actions following one Publish**. The first action saved status-basis delivery recognition and the base fee. An earlier optional completion diagnostic remained because that completion scan had occurred before the new base fee was recorded. Read-only diagnosis established this ordering before the second action.

The justified second selected action recalculated from the persisted fee state. The existing `recorded_base_fee` completion rule then cleared the earlier diagnostic. The UI reported no new native attempt on that action; the specific cooldown-versus-eligibility explanation was not established and is not asserted here. No additional code change, build or publication was needed.

The saved report removed the selected case from both pending sections, and a full browser reload confirmed persistence. Its financial detail remained unique; the original status basis, recorded recognition time and base fee were unchanged. No duplicate fee or COD evidence was created.

The final cohort comparison was independently verified privately. Comparing the two post-action exports showed only removal of the previously identified residual completion-pending entries. Remaining pending rows had no new identities, moves or reason changes. All financial detail rows were identical field-for-field, with no added, removed, changed or duplicated order/executor rows; recorded financial and delivery summaries were unchanged. Private identifiers, amounts and business counts are excluded from this public record.

## Exact next safe action and handoff boundary

The [reviewed protocol](https://github.com/AMASI-SA/AMASI-SA/blob/37a8211c383079711839557ecad6d5f3f0cdd51f/AGENTS.md) acceptance sequence is complete: intended identity, all three official probes and required public artifact/cache checks, followed by inactive status. The [unchanged guard](https://github.com/AMASI-SA/AMASI-SA/blob/37a8211c383079711839557ecad6d5f3f0cdd51f/scripts/production_release_guard.py) closed the matching lease after full success. No courier merge, build, Publish, verifier or lease remains pending. Do not repeat those completed steps or reuse the closed lease. The frozen A/B pair and accepted M remain unchanged.

The next authorized shared-host owner may proceed only after its own fresh inactive-guard and source checks. This publication's handoff does not waive any future owner's protocol obligations. No further publication action is needed for this release.

This sanitized completion record is preserved on the separate evidence-only branch named above, with its exact commit/reference in the canonical PR handoff. Never merge or deploy this evidence branch. Production publication and selected live acceptance passed, the private cohort comparison passed, and the frozen release remains unchanged. Preserve the private exports separately; this record does not claim historical-cutoff replay or resolution of unrelated pending reasons.

## Durable verification artifacts

- [Official verifier summary](verification-summary.json): exact observed identities, successful exit/probe count and final inactive status; full owner-result digest is retained.
- [Public runtime screenshot](runtime-proof.jpg): the deployed public health identity, containing no customer or payment information.

No publication or live reconciliation action remains pending for this task. Unrelated incomplete orders remain subject to their existing evidence rules; this record does not claim that every accounting discrepancy is resolved.

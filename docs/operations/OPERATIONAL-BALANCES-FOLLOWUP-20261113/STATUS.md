# Operational balances follow-up — release continuation 2026-11-13

<!-- RESUMPTION-CURRENT-START -->
## Current state — 14 November 2026, Riyadh

Browser access was restored by resetting the JavaScript connection kernel. The signed-in financial-position page opened successfully. **Fresh financial acceptance did not pass:** the visible page load settled with a source-read failure warning and the old report `as_of` of 12 November 2026 01:07 Riyadh. This is now a concrete application result, replacing the earlier “unobserved due to browser transport” status.

**The pending-order dialog acceptance passed for the inspected 15-row iMile group:** both pages (10 + 5 rows), order numbers, review reasons and search worked. The idle row without valid confirmation metadata did not display the false “جارٍ التحقق وحفظ التأكيد…” message. No confirmation, settlement or payout button was used.

The original PR #1906 release remains fully verified, but production subsequently advanced to PR #1908. Do not republish #1906 or its documentation branch.

## Live observations

[Sanitized acceptance record](LIVE-ACCEPTANCE-20261114.json) preserves this turn's observations without customer data, order identifiers or financial amounts.

- Observation: `2026-11-13T22:30:18.040Z` = 14 November 01:30:18 Riyadh.
- The financial tab was confirmed `visible` while loading and when the settled report was read.
- The refresh button became enabled and the loading state ended.
- Exact warning: “تعذر تحديث المصادر؛ تُعرض آخر أرصدة محفوظة بتاريخها. تعذر تحديث مصدر الأرصدة؛ احتُفظ بآخر لقطة ناجحة مع سبب الفشل.”
- “آخر احتساب”: `12/11/2026, 01:07`.
- “إعداد التقرير”: `14/11/2026, 01:29 · الرياض`; this fresh rendering timestamp is not evidence of fresh balances.
- No manual refresh was clicked after the initial page navigation in this turn. The HTTP response body, number of underlying POST requests and request duration were not captured.

## Failure classification and evidence gap

The exact displayed message maps in the production source to `SourceReadError` in the `source_collection` block of `operational_balance_service.refresh`. That handler returns HTTP409 with `detail.code=operational_source_setup_incomplete`, `detail.cause` and `detail.source_collection`.

This classification is **inferred from the exact source-message mapping**; it is not a direct observation of the HTTP response body. It identifies a source-reading failure before the subsequent calculation, but does not identify the failed collection or its exact cause.

The frontend retains only the human message. The current handler does not log the cause/collection and the failing mutation exits before saving the new state, revision or audit. The existing context/audit views therefore cannot recover the missing diagnostic fields from this failed attempt. Expected production logs expose stage/duration/outcome and HTTP status only. Browser network inspection was unavailable under the existing browser restriction, and no bypass was attempted.

Do not infer a collection timeout, more than 20,000 records, owner mismatch, rejected source or invalid source from the message alone. Do not hide the failure by trimming records, substituting zero, relaxing owner filters or starting background refresh.

The next engineering step is narrowly scoped diagnostic visibility for the existing request: retain or log only a whitelisted error code and collection identifier, without customer/order/amount/credential data, then observe one visible attempt and fix the proved cause. A further blind refresh provides no new diagnostic evidence.

## Production has advanced after #1906

The production ref read in this follow-up is `53acc03a178362530f2cb45206fe11a4fb724d88` (PR #1908), with source `47e474fa896e09db353b9928261fe96186ea24e1`, intent-only B `394deb3a09fbf8a3cec5a8e2c3c21d6bbd2fa264`, and runtime `rg5-8bd428e3e93319a15361587b91a09dbd5b0f97f6bbea49d0fd8b4d4343166b9a`.

[PR1908 completion evidence](https://github.com/AMASI-SA/AMASI-SA/pull/1908#issuecomment-6864777801) records platform version `11d385a`, completion 13 November 23:41 Riyadh, official verify exit0/`verified:true`/3 passes at `2026-11-13T20:55:32.260592+00:00`, and final `active:false`. This is the preserved publication record; this follow-up did not run another official production verification.

Independent comparison found no `operational_*` or frontend changes between #1906 and #1908; the frontend artifact tree remains `f77e96091e737b362accb3c17e2a642081b0fec48fdcbced94ff20672772a4e5`. Do not attribute the current application attempt to the old A7 runtime identity.

## Verified #1906 release record

| Item | Exact value |
| --- | --- |
| PR | https://github.com/AMASI-SA/AMASI-SA/pull/1906 |
| Source A7 | `286e46ce50d0ae1f5f128646e350bbae38b2ef5c` |
| Intent B7 | `9502be1eb4adfc8f28a33a190cc3a5be8b75e241` |
| Prior production J | `8d25f7a6fa9a310b319b753c32457e70987f7c51` |
| Normal merge M | `791b019e20ffe24439393576129783cd4c9dceb3` |
| B7/M tree | `663ff410de7bc6e18933ac1d06fcdd4564bd69ba` |
| Runtime | `rg5-4b111864525e317c313f6358c3c42f5ba2deaa6f1610b6e2db47399029b7a57f` |
| Platform version / completion | `72c5b2e` / 13 November 22:41 Riyadh |

All A7/B7 gates and all 3 exact-M workflows/11 checks passed. Original `/app` fast-forward and actual package adapter passed; tracked files remained clean and all 120 pre-existing untracked-file hashes were preserved. Official prepare/prepublish and one Publish action succeeded. No cleanup, reset of operator files or paid Emergent chat was used.

[Exact official verification JSON](OFFICIAL-RELEASE-VERIFICATION-20261113.json) remains unchanged: 33,299 bytes, SHA256 `c756f0b20cbd728f64df6b0ff6d54a8eb5991fed9c4430e9b1cabe5598d692b0`. Actual exit0, `verified:true`, 3 complete passes, 582 frontend requests per pass, empty stderr and final status exactly `{"active":false}`; wrapper duration822.87 seconds, result written `2026-11-13T19:56:56.421042+00:00`. Independent review matched all 582 request entries and identities to B7.

[Original execution metadata](OFFICIAL-RELEASE-VERIFICATION-METADATA.json) is a historical checkpoint before this follow-up's live application result. The new acceptance record above supersedes only its pending financial-acceptance status.

![Original #1906 platform completion at22:41Riyadh](PUBLISH-CONFIRMATION.jpg)

This documentation-only handoff branch must not be merged into or published as an application release. Any new source work must start from fresh production and follow the current guarded release contract. Issue #1006 remains at its comment cap; PR #1902/#1906 are the balances continuation threads.
<!-- RESUMPTION-CURRENT-END -->

## Historical frozen A7 source record

The following record describes the source checkpoint before this resumed release. Its old pending/lease states are historical; use the current state above for continuation.


### Current state

The corrected source is locally verified and independently reviewed after A5
failed consumer read-shape contracts and A6 missed the actual application DB
adapter. Both have narrow corrections with regression coverage. Exact-source CI and a
new reviewed v5 source/intent pair are still required. No intent has been
committed. This source is not merged or deployed. Fresh financial acceptance
remains FAILED; no synthetic result is treated as production acceptance.

### Repository and continuity

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/operational-live-refresh-followup-20261113`.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1906.
- Base Production: `8d25f7a6fa9a310b319b753c32457e70987f7c51`.
- Initial remote UI WIP: `eb0017ecdb10dc7ab17b27e748f2b0d86532b44b`.
- Rejected source A5: `f5a764999b6e902882d1f7b4fe8a7b951af4ecc1`.
- CI-green but superseded source A6: `41623632b2531e44aa592c06e52bd66b1c6a7b38`.
- Root owns integration and the already-authorized release operation.
- The exact next source SHA is recorded in the PR continuation entry; this
  document cannot contain its own commit identity.
- Issue1006 has reached its comment limit. PR1902 and PR1906 are the linked
  continuation threads. No coordination marker exists; no board was created.

### Completed behavior

#### Pending-order dialog

An idle row without valid confirmation metadata could display the busy label
because both target key and busy key were null. The display now requires a
non-null busy key. The order number and reason remain visible; opening the
modal does not call preview or confirmation. A real explicit request still
shows its busy label until completion. Eligibility, permissions and saved
proof semantics are unchanged.

#### Bounded cost reads

- Within the positively admitted visible-request path only, do not query units
  when no matching plans exist: `plan_id: {$in: []}` can only return an empty
  set. Default callers retain even that empty query as part of their existing
  audited read contract.
- The remaining-bundle adapter explicitly opts into overlapping three
  independent reads: stored snapshots, lifecycle and plans.
- The authority permits this only for exact raw `AsyncIOMotorDatabase` or the
  exact application `AcceptanceConfigDatabase` directly wrapping exact raw
  Motor. The current `DemandRefreshContext` must own the same ORIGINAL DB
  identity and owner, and `require_connected()` must succeed. All queries
  continue through the original adapter; its underlying DB is inspected only
  to prove this narrow admission. It is not used to bypass the adapter.
- Default callers, all other wrappers/session adapters, mismatched owner/DB and
  calls outside that context remain serial and retain their exact read shape.
  Ordinary financial GET receives neither optimization.
- Results and errors are awaited in the previous source order. Failure or
  cancellation cancels and joins all started children; repeated cancellation
  cannot detach them. At most three authority reads are active at once.
- The full order cohort, original source limits, financial holds, monetary
  rules, deadlines, authorization and explicit confirmation are preserved.
  No scheduler, polling, hidden loading or background refresh was added.

The proposed historical-only cohort filter was rejected. Actual-module
synthetic reproduction showed that a newer order's frozen bundle evidence can
prevent incorrectly pricing an older order from its profile. The old filter
removed that hold. `operational_open_cost_exposure.py` is unchanged; the
regression preserving the hold is retained in the new test suite.

### Source files

- `backend/order_cost_authority.py`
- `backend/operational_remaining_bundle_sources.py`
- `backend/tests/test_operational_balance_cost_read_scope.py`
- `frontend/src/pages/operational/PendingOrdersDialog.jsx`
- `frontend/src/pages/operational/PendingOrdersDialog.test.jsx`
- `scripts/testing/operational_refresh_stage_analysis/measure_cost_authority_reads.py`
- This status and `COST-READ-MEASUREMENT.jsonl`.

No manifest, lockfile, release guard or frozen prior release intent was changed.
All fixtures and measured monetary values are synthetic. No live customer,
order, party, financial data or credentials are included in this public record.

### Completed verification

Backend command (repository root, isolated repository-pinned test dependencies):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python -B -m pytest \
  --noconftest -q --tb=short --maxfail=3 \
  backend/tests/test_operational_balance_cost_read_scope.py \
  backend/tests/test_order_cost_authority.py \
  backend/tests/test_operational_open_cost_exposure.py \
  backend/tests/test_operational_balance_sources.py \
  backend/tests/test_marketing_bundle_cost_authority.py::test_marketing_deduplicates_before_one_batched_authority_load
```

Exit0: **214 passed in22.12s**, including35 new cases and the4 cases that failed
in A5's G47 gate. They cover full-cohort
financial evidence, result parity, units with/without plans, all wrapper and
owner/DB boundaries, read limits, error precedence, disconnect and repeated
cancellation with no remaining tasks. Initial import failures from missing
local test dependencies were resolved within the task venv before this pass;
no test expectations were weakened.

Frontend command:

```bash
CI=true python -B scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watch=false --runInBand --resetMocks=false \
  --runTestsByPath src/pages/operational/PendingOrdersDialog.test.jsx
```

Exit0: **33 tests /1 suite passed**, no skips/failures; Jest8.887s. The pinned
Node22.23.2/Yarn1.22.22 bootstrap and single frozen-lockfile install passed.
The three new cases cover absent/malformed metadata and genuine in-flight
confirmation. The original defect was established live and in source; no
pre-fix automated red run was claimed. Package and lockfile diffs are empty.

Root reviewed the combined application diff and `git diff --check` passed.
An independent reviewer signed off the exact backend hashes below, after
checking context gates, serial fallbacks, ordering, cancellation/join and the
unchanged full cohort. That was an independent source review, not a second
execution of the214 tests or a production performance claim.

| File | SHA256 |
|---|---|
| order_cost_authority.py | `cb806ffd2dbdf48ab83bb267326e9b9cc90c5dbf2dcc19b12ae15880d3ec2f47` |
| operational_remaining_bundle_sources.py | `911ebd5bce96a6ff05f12f2d3a8992e58c9ec396868a3f0fbfad8501ec7be2bf` |
| test_operational_balance_cost_read_scope.py | `2fb258b045b2a70636c8aa4ad4548b3e00e6615a9caf6085c2c6393e1715f949` |
| PendingOrdersDialog.jsx | `aeb6d1faa6aaf360f9ec1b1700e68e6382024de7551be473a174fadc7441a831` |
| PendingOrdersDialog.test.jsx | `6a723434f4f7a9e35ec33f9c48c7bde60562a80a8197963d1e3c22e50a536843` |

### A5 integration rejection and correction

All13 exact-A5 workflows ended:9 passed and4 failed. The blocked gates were
G47 `43472485532`, Dashboard runtime `43472485297`, Operational Balance Web
`43472485539`, and Stock `43472485381`. No failure was called a flake or an
unrelated pre-existing defect, and no failed run was rerun or bypassed.

The empty-plan UNITS omission had been global in A5. G47 expected6 authority
collections but received5. Dashboard passed its first frozen JSON comparison,
then rejected the missing audited UNITS command and stopped at that case.
Operational and Stock repeated the2 mapping cases expecting8 reads but seeing7;
their preceding completeness and monetary-total assertions passed. The original
read-shape assertions remain unchanged.

The corrected condition is `if plans or not parallel_reads else []`, using
the already-validated positive request gate. Three new tests prove the default,
explicit false and invalid truthy opt-in retain their empty UNITS read. Existing
wrapper/no-context/owner/DB mismatch tests now also assert their original query.
Local198 cases passed, including all4 G47 failures. The2 actual-Mongo mapping
cases were not run locally because no isolated Mongo exists in this scratch
environment; exact corrected-source CI must run them and the complete frozen
Dashboard gate. No existing service or real data was used for local tests.

A5's own16 new backend cases passed in its3783-case JUnit result, while2
compatibility cases failed. Its frontend artifact proved721/721 and all33
dialog cases passed. These are historical A5 results, not acceptance of the
corrected source. A6's backend scope suite had19 cases; the current suite has35.

A5 candidate artifact14061806909 passed official candidate validation, but
was NOT committed as an intent or used for publication. It is obsolete for
the corrected source. A new source/intent identity must be built and verified.

### A6 CI closure and production-adapter correction

At17:42:37UTC all13 exact-A6 workflows passed:29 checks were23 successful plus6
expected scope/source-role skips, with0 failures. Independent artifact parsing
proved Operational3786/3786 including all19 scope cases and both previous
mapping failures, frontend721/721 and dialog33/33, G47 cost436/436 including
the4 previous failures, and Dashboard runtime/reference/production baseline
40/40 each plus255 regression cases. Frozen expectations and workflows stayed
unchanged. Principal runs: Operational `43474371783`, G47 `43474371880`,
Dashboard `43474371676`, Stock `43474371993` and Readiness `43474371624`.

Before freezing any intent, root traced `server.py:208` and the operational
router binding: the app injects `AcceptanceConfigDatabase(raw Motor)`. A6's
raw-only type gate would therefore leave the optimization inactive in the real
application. The validated A6 candidate14063260084 was not committed or used
for publication. It cannot represent this corrected source.

The current narrow gate admits only that exact, session-free repository adapter
over immediate exact raw Motor, preserving the original request DB identity
and every query through the adapter. New cases use the actual adapter to prove
activation, financial parity, original-adapter query delegation, default/GET
serial reads, wrong owner/context/DB rejection, nested snapshot/private-wrapper
and subclass rejection, and joined cancellation including repeated cancellation.
Root and an independent reviewer signed off the current hashes above. All214
local cases passed; the current35 scope cases and full regressions still require
new exact-source CI and a newly built intent.

### Reproducible synthetic measurement

The tracked measurement script runs the actual `run_visible_refresh` and
`collect_sources` modules through the actual `AcceptanceConfigDatabase` adapter
over in-memory Motor boundaries, with10ms artificial cursor latency. Both old
and new implementations use that same application adapter. It makes no
external DB call. Root ran the saved script
successfully and preserved its exact JSONL output alongside this document.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:backend:backend/tests python -B \
  scripts/testing/operational_refresh_stage_analysis/measure_cost_authority_reads.py
```

| Orders | Source reads before/after | Authority peak before/after | Original A5 raw-only ms before/after | Current actual-adapter ms before/after |
|---:|---:|---:|---:|---:|
| 1,000 |50 /46|1 /3|1185.941 /860.838|1219.543 /1043.727|
| 5,000 |114 /94|1 /3|3304.317 /2343.424|2798.257 /2120.663|

All runs assert exact financial/source result equality, unchanged inputs,
unchanged2 lease operations and0 remaining tasks. The result digests match
between runs. Timing varies with the local environment; the deterministic
read-count reduction and parity are the mechanism evidence. This is not a
measurement of production latency or proof that its timeout is resolved.
An intermediate A5 reproduction measured1908.545/1183.308ms and
3537.936/3155.214ms. A6's raw-only sample measured1122.270/840.167ms and
2301.399/2607.518ms: its5000-order candidate was slower despite fewer reads;
there is no guaranteed elapsed-time improvement claim. Do not repeat samples
to select a favorable timing. The corrected source preserves the optimization
only on its admitted visible path, and preserves all default query contracts.

### Live evidence and previous release

PR1902 source `1f8f1d673854f594086a6e87dec6c98633da0061`, deployment
`b64121c265d8d6e158df0d9fb88debc52c95317d`, is officially verified live.
The original operator completed the official verification2026-11-13 at
17:02:16UTC: exit0, three full passes,582 frontend requests per pass.
Subsequent official status returned `active:false`. Platform completion is
Nov13,18:48Riyadh, version0cdd01a. Do not republish that frozen pair.

The authenticated report displays saved balances and pending-order modals with
working pagination. Its initial visible refresh failed with a source-read
message; two manual diagnostic attempts used the refresh-timeout fallback.
A saved financial GET was observed status200 with duration31839.360ms.

A fourth total attempt was justified after Management returned a newer log
window. It started17:18:00.027UTC and remained visibly open; by17:20:20UTC it
again showed the refresh-timeout fallback. Log retrieval then remained cached
at17:17:23 despite navigation/reload, so no corresponding phase timing was
captured. A later operator status revealed another task had prepared a release
at17:17UTC, so this fourth attempt overlapped that release and is not a stable
runtime acceptance result. Do not infer a single production bottleneck or
repeat blind refreshes before a concrete change. No real confirmation,
settlement or payout was clicked.

Prior release evidence remains on documentation-only branch
`codex/operational-delivery-release-handoff-20261113` at
`e1eb7af40d5604a77b93f92f8dfaa071c8b660d4`, linked from PR1902.

#### Other operator lease now active

Fresh read-only status positively returned an active protocolv5 lease owned by
`arafat-pr1905-terminal`, prepared `2026-11-13T17:17:00.403406+00:00`, source
`c05108b41a3cb603955b210acb0fba32743d5d5f`, deployment
`8d25f7a6fa9a310b319b753c32457e70987f7c51`, runtime
`rg5-9f65056bf0d9f1bc5e40da895de78a8288fee5e532ecaaa26daf4f468522894b`.
This is not the closed PR1902 lease and is not owned by this follow-up. Do not
clear, reuse or verify it on behalf of this task. Preserve its operator state
and Production head until its owner closes it and fresh status is inactive.

### Next safe action

1. Preserve the corrected source checkpoint on this task branch and inspect all
   exact-source CI results, including the new35 cases and every previously
   failed compatibility gate. Do not use either A5 or A6 candidate intent.
2. Recheck current Production. Freeze a new reviewed intent-only commit after
   the clean governed build; do not squash/rebase or add source after freezing.
3. Complete exact intent gates and clean-clone rehearsals. Wait for the other
   lease's owner to close it before changing Production or the operator. Read fresh
   operator guard/coordination, HEAD and tracked/untracked state before any
   operator mutation. The prior closed lease must not be reused.
4. Use the owner's existing upload/merge/publish authorization and protocolv5.
   Require a newer explicit platform completion and official full verification.
5. Inspect the live report without real financial confirmations. Financial
   acceptance requires a fresh successful report, not a dated fallback.

New task Production changed: **NO**. No new operator source mutation, lease or
Publish action has occurred at this source checkpoint. Preserve unrelated
Production8d25 work, operator untracked files and unrelated terminals.

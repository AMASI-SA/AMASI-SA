# CUSTOMER-INVENTORY-UNIFIED-20261122

## Follow-up — live page recheck, 23 November 2026 Riyadh

**Authenticated browser access succeeded on retry. The pages load and the bounded read-only checks completed, with one confirmed inventory-options UI finding. Do not describe all option cases as passed.** The previous browser-access blocker is superseded; its historical record remains in POST-PUBLISH-PAGES-20261123.json. This follow-up does not reopen the completed PR2074 release or hold a subsequent publication.

The live Backend and Frontend both identify source **e9d80ff2e7a3b21fb25221516bb05e02819fd902**, runtime **rg5-9796a26af755f49224fe1787bc922eb669b69f2a63a28192e2a84a0d320fc03f**, protocol 5. Health reported all verification flags true; the separately rendered /build-meta.json source and artifact digest match the Backend's embedded Frontend metadata. This bounded read is not a new original verify3, MIME/cache/file-byte verification or lease observation. Current Git ref **c2d244a3349013c06f3fb7824070bfa12bd0bfe0** was read separately; deployment and runtime-source identities must not be conflated.

### Live results

- Refund and collection pages loaded actual pending lists and distinct colored summaries. Existing-order lookups proposed the displayed differences while keeping confirmed paid amounts separate. Source/bank/provider/receipt controls and empty confirmed histories rendered. Refund pagination advanced successfully.
- Inventory and purchase invoice lists loaded explicit empty states. Purchase form loaded supplier choices and separate receiving/payment choices; no form content was intentionally changed. Its own normal mount effect can persist the restored/default local draft.
- The new **استلام مشتريات المخزون** tab at /inventory-v2?tab=receipts loaded, defaulted to pending and switched to all invoices. Both lists were empty, so an existing receiving form/history could not be inspected.
- Yes/no dependency display and clearing of hidden names worked on the tested inventory samples. All temporary inventory input was canceled.
- Provider report and review-reasons dialog opened. The displayed report is still a saved **21 November 2026** snapshot, not proof of a post-release recalculation. The shared update action was not run.

### Open finding — INV-PHYSICAL-DEFER-01

**Conditional size/colour can be offered as deferred customization.** A live clothing item's size and colour displayed selectable “يُحدد عند طلب العميل” after choosing its parent item type, despite guidance requiring those physical dimensions now. Selecting size deferral succeeded in temporary UI state. A second item's independent size correctly had no deferral. No stock was saved.

Root and independent read-only review traced the exact live source: backend/inventory_option_configuration.py marks physical options through variant selections or explicit physical roles; an entire connected condition group containing text and no recognized physical identity becomes inferred customization. InventoryProductFields.jsx renders the resulting defer_allowed flag. The precise live metadata omission was not inspected, and backend acceptance of an incorrect saved entry is not claimed. Next safe engineering work is an isolated correction and targeted regression for mixed groups with missing physical designation.

Full sanitized evidence, per-page coverage and boundaries: [LIVE-PAGE-RECHECK-20261123.json](LIVE-PAGE-RECHECK-20261123.json). Observed at 16:46 Riyadh. No financial confirmation, stock save, invoice, receipt, supplier-payment allocation, source edit, guard invocation or publication occurred. No customer records/raw DOM/screenshots were committed. The browser's captured application error/warn entries were empty after excluding extension metadata errors.

The read-only review is complete with the stated finding and coverage limits. Do not substitute these UI observations for proof of financial posting or full/partial receipt persistence. Preserve every foreign task/lease and the immutable PR2074 completion below.

## Current state — COMPLETED; combined release published and verified; serial handoff RELEASED

The owner confirmed **«نعم ظهر»** in direct reply to the question whether a **new Deployment Succeeded** appeared in Emergent before the original verification command. This is explicit owner UI confirmation; root does not claim direct observation or an exact UI event timestamp. It completes the sole remaining documentation gate.

The retained original verification already reported **verified:true**, **checks:3**, exact combined source/deployment/base/runtime/artifact identities and final original guard status exactly **{"active":false}**. The customer-confirmation and inventory/purchasing/receiving updates were published together in this one combined release on https://mezansalla.com. Root and independent review accepted the full evidence. **No remaining work or publication hold belongs to this task.**

Completion recorded on 2026-11-22 at 20:39:36 UTC (23:39:36 Riyadh); this is a record time, not a publication timestamp. See [RELEASE-COMPLETION-2074.json](RELEASE-COMPLETION-2074.json) and [full live verification](HOST-PUBLISHED-VERIFY-PASS-20261122.json).

| Identity | Value |
|---|---|
| Unified merged PR | https://github.com/AMASI-SA/AMASI-SA/pull/2074 |
| Deployment D | `24270ebdfdebb83282389308a2349cdb27e2b8e4` |
| Frozen source A2 | `4e6c8a61bd1566e061a4f312e797ce239b582475` |
| Intent-only B | `447e83bfb95d2627877d46c7baa528e454a2e143` |
| Reviewed source base M | `06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35` |
| D/B tree | `6a906db6cf4fd3dda4b550483a0ef96067c6f1ed` |
| Runtime | `rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f` |
| Lease actor | `codex-customer-inventory-unified-2074-20261122` |
| Prepared at | `2026-11-22T19:36:26.794349+00:00` |

The original current Production Git reference was freshly reread after receiving this transcript and still equals D. Do not squash/rebase/amend A2/B. D is an ordinary merge with exact B tree and ordered M/B parents.


## Accepted live verification

- Command: `bash /tmp/mezan-pr2074-deploy-19r0je2x/verify-after-success.sh`.
- Submitted transcript SHA256: `5986ae0c52638ba6bc323df3c41bf33e8b9a351be409ae59c7177dc1068f11a5`; 44,397 bytes; two complete JSON objects and return to the shell prompt.
- Original result: verified true; three consecutive probes; URL https://mezansalla.com; protocol 5.
- Exact deployment/source/base/release/artifact identities match the reviewed combined release.
- Backend runtime and release-control source summaries exactly match the frozen intent.
- Frontend artifact tree: `68278a1d40fc16b2852359eeee963b12bb1820e675e691d74410ce4f47f5123b`.
- The original verifier's returned list contains **774 request descriptors**. Its original implementation checks canonical/cache-busted public-file bytes, build-meta JSON MIME and exact bytes with JSON/wildcard Accept, shell cache contracts and service-worker retirement payloads before it can return success.
- Boot observations: `2026-11-22T19:50:17.049615+00:00` and `2026-11-22T19:50:07.645048+00:00`, both later than prepare. These are returned runtime boot observations, not a claimed UI publication timestamp.
- Original matching lease was removed by the verifier; final original status is exactly `{"active":false}`.
- Fresh Production Git ref after receiving the transcript still equals D.
- Full sanitized structured result and acceptance boundaries: [HOST-PUBLISHED-VERIFY-PASS-20261122.json](HOST-PUBLISHED-VERIFY-PASS-20261122.json).

The successful live checks imply the original guard's health, verified identity, Backend source, critical hashes, Frontend build-proof and public-byte requirements passed. Separate raw health responses are not present in the attachment; no such records are fabricated.

## Completed host staging and retained backups

The earlier original guard was exactly inactive before shared staging and prepare. Candidate and shared source/intent/artifact proofs passed. The guarded fast-forward installed D and only the three reviewed generated outputs were replaced after verified backups. All **432 unrelated untracked files** were preserved. The backend bytecode cache was absent and remained absent.

- Previous outputs: `/tmp/mezan-pr2074-preserved-954nogxv`.
- Retained local stage: `/app/.git/pr2074-stage-ti7z2fet`.
- Original successful rehearsal: `/tmp/mezan-pr2074-icmywyrd`.
- Rehearsal log SHA256: `ef59a7aff3f7ae6c8af6f9d17222d3f3f10c395e7f8644e591dd437df70b9321`.

Original prepare and prepublish passed for the recorded actor and identities. Their historical active lease has now been closed by successful original verification. Preserve the backups and unrelated files. See [HOST-STAGING-PREPUBLISH-PASS-20261122.json](HOST-STAGING-PREPUBLISH-PASS-20261122.json) for that historical stage.

## Final serial handoff

**This task's publication handoff is released.** The original verifier already closed its own matching lease; no lease mutation was performed to record completion. No further staging, prepare, prepublish, publish, verify or cleanup is needed for PR2074.

A subsequent authorized task may continue its own release process after freshly reading the current Git refs, this coordination ledger and the actual original guard before changing /app or starting a rehearsal. The recorded inactive status is evidence of this completed verification; it does not replace a later task's fresh guard check. Preserve all unrelated files, historical backups and foreign leases.

The new explicit UI success is now confirmed by the owner. Prior records that show UI confirmation pending or this task's serial handoff held are historical and superseded by this completion.

## Preserved verification and scope

All 17 applicable exact-B workflows passed, including actual Node 20.20.2 and 24.19.0 clean-clone adapters. Independent acceptance checked eleven artifact ZIP hashes, both internal tar checksums, all original artifact/identity/package proofs, exact JSON MIME CI routing and operational/stock tests. Operational backend6502+90subtests, demand93, shipping16+18subtests, frontend1620/103suites, browser33=14+12+7, worker4; stock5525+97subtests. Counts overlap. All three combined supplier/customer/demand regressions executed on isolated real Mongo.

The complete inventory source and customer confirmations are combined in this single release. PR2065/2067 remain independently unpublished provenance, and superseded PR2073 remains closed without merge. Preserve unrelated PR2075/current tasks and every foreign file/lease. The withdrawn slowness report remains outside this task.

Source/build/host packaging evidence remains in EVIDENCE-4e6c8a61.json, EVIDENCE-447e83bf.json, the Node20/24 and operational artifact reviews, NORMAL-MERGE-2074.json, HOST-REHEARSAL-PASS-20261122.json and MANUAL-DEPLOYMENT-HANDOFF-2074.json. Earlier documents stating that host staging, prepare or live verification is pending, or that this lease remains active, are superseded by this accepted result. Frozen release source is unchanged; this branch is evidence only and must not be deployed.


**Observed changes:** Production Git and shared /app staging YES; live combined release VERIFIED; own lease CLOSED / final active false; new Deployment Succeeded explicitly confirmed by owner; task COMPLETE and serial handoff RELEASED. No source/intent/Production-ref mutation or deployment replay was performed while recording completion. Canonical coordination: https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123

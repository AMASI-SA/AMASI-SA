# Report option recovery follow-up — WIP

Base: ae2fe9ce01c003ff139e22dd54f4641be48c8e38 (preserves PR1773/1774).
Branch: codex/report-raw-option-recovery-20261105.

Production read-only investigation found a Knoud variant canonical line with
options=[] and no raw_item, while raw_by_source.salla_direct.items retained
its exact native line ID, matching product/quantity and the selected embroidery.
Salla's option shape uses product_option_id for the catalog identity and id
for the checkout row. The previous report resolver did not recognize the
product_option_id alias. Catalog service bindings were present. No cost data
was changed. The observed source contains the evidence; missing choices are
not evidence of free service. Other affected products still need live review.

WIP implementation: narrow raw item projection in the existing dashboard raw
read and sold-report read; recover only empty choices using unique order and
native line plus exact product/quantity. No SKU or label join. Keep evidence
in a separate in-memory report field consumed by current catalog and bundle
estimates, not historical fingerprints or financial snapshots. Explicit
provider option IDs survive renaming. Duplicate/mismatched source evidence
remains incomplete. No DB writes or extra per-order queries.

Regression reproduction with synthetic identifiers and observed payload shape:
expected 2*(15+8+1.20)=48.40; before recovery 32.40, one intended failure.
Five focused tests pass, including real route/reducer collaboration through
mongomock, both dashboard reducers, sold-products HTTP, tenant isolation and
read-only collection wrapper. Adjacent suites:197 passed/4 existing real-Mongo
skips. Prior alias focused suite17 passed. These are local tests, not a live
production repair or a complete deployment acceptance.

Next: strengthen malformed/conflicting/oversized evidence and cooperative CPU
coverage; inspect current bundle alias cases; run full affected checks and
source-pinned report gate; independently review exact diff, freeze new A/B,
wait for other release lease to close, merge/deploy/test under existing user
authorization. Do not reuse or clear prepaid-cost-1774 lease. Production unchanged
by this follow-up. Existing PR1772 publication remains verified separately.

## Verified source candidate

Read-only rose investigation also found an empty canonical line whose saved
provider item contains both paid choices, with matching native line identity.
No customer data copied into fixtures. Recovery remains deliberately limited
to empty projections; unresolved/conflicting historical authority is retained.

Added cooperative CPU checkpoints to the existing dashboard/sold-report reads,
revalidation clears stale in-memory evidence, and explicit current_report opt-in
prevents financial catalog callers from consuming report-only evidence. Bundle
explicit option identity conflicts/unknown IDs reject label fallback.

Final local affected suites:254 passed,4 existing real-Mongo skips,5 subtests;
exit0 in5.94s from backend cwd with MEZAN_CGROUP_ROOT set to an absent test path.
Twelve new recovery tests include endpoint/reducer integration, frozen authority,
financial opt-out, duplicate/mismatch/tenant rejection, current bundle identity,
and cooperative yielding. Source-pinned gate allows only the exact sold-report
one-line opt-in hash a05b89134c078eefeac5ca9bc4e0827d9f5c7ec0769bed7674eca64c71327fb5.
An earlier root-cwd run had2 path-read failures; corrected invocation passed.
An initial source-pin rejection was expected and recorded before exact pin update.
Diff whitespace check passes. No production writes, merge or deploy by this task.

PR1775 is the continuation ledger. First checkpoint f4b34360b428276f1fc560d0a04a18efff3ee20b.
Fresh guard status active:true, actor prepaid-cost-1774, prepared21:04:02Z.
Do not mutate shared/app or start release rehearsal until its owner closes it.
Next: exact candidate CI, governed A/B source build/intent, clean-clone rehearsal,
authorized merge/publish and live completeness validation. Never claim all costs
complete from synthetic tests. Costs/prices have not been changed.

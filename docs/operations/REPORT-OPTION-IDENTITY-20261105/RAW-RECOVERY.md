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

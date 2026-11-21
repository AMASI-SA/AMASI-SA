# Catalog memory recovery — source review checkpoint

## Identity and release state

- Task: `CATALOG-MEMORY-RECOVERY-20261122`.
- Branch: `codex/dashboard-catalog-memory-20261122`.
- Review: PR #2054 (draft at this checkpoint).
- Base: `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb`, the normal PR #2053 merge.
- Recoverable WIP checkpoint: `eff3f35a773945aabd91c0faabc8ed86588a781f`.
- The local reviewed source extends that WIP; root will record its exact final
  source commit in PR #2054 and the canonical Issue #1006 ledger.
- Current `backend/dashboard_v2_routes.py` SHA-256:
  `0d9f4fb40beebbaf8aa27c6cb51d962913cfa2d8399afb82bea1a2e52ac42feb`.
- This is a separate follow-up to PR #2053. Its frozen A/B were not modified.
- No release intent has been frozen for this follow-up. No merge, production
  publication, production database write, or production environment change was
  performed by this implementation task.

## Completed behavior

Both product-cost builders return the unchanged empty result without any
database reads when the order cohort is empty, preserving JSON member order.
For nonempty cohorts, a first owner-scoped catalog read retains the original
first-100000 boundary and the complete alias information used by the canonical
resolver. It excludes unrelated raw product descriptions, images and option
payloads. Complete raw/normalized variant values remain in this first pass,
because their type, truthiness and precedence affect historical alias matching.

The canonical global index determines the exact physical documents selected by
order lines. A second owner-scoped read hydrates only those Mongo `_id` values
with the original full cost projection. The original alias maps are filtered
and their values replaced with hydrated records; they are not rebuilt from the
selected subset. This preserves last-winner ID/SKU/variant collisions, globally
ambiguous names, and first-winner name aliases even when physical rows share a
canonical identity.

A selected document removed or reidentified between the two reads produces an
explicit retryable `503 / catalog_changed_during_cost_read` with
`data_complete=false`. Compact alias rows never become cost inputs. A successful
fresh retry uses the current identity; a genuinely missing product remains
visibly incomplete. Price-only changes remain current catalog prices.

The original full list of Salla product IDs still scopes the companion profile,
binding and resource reads. Their limits and order remain unchanged, avoiding a
change to the legacy capped subsets, including nonempty orders without product
lines. The complete companion-read and financial
reducer AST in both builders is pinned to the base commit. Recorded bundle/order
costs, approved supplier authority, status rules, payment rules and financial
writers were not changed.

## Verification completed locally

The new regression file is registered in the existing dashboard card cadence
workflow, including its path filter. Tests use synthetic isolated databases.

Commands used the existing Python environment with
`PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1` and
`PYTHONPATH=backend:backend/tests`:

```bash
python -m pytest -q -p no:cacheprovider \
  backend/tests/test_dashboard_catalog_cohort_reads.py \
  backend/tests/test_dashboard_v2_product_cost.py \
  backend/tests/test_dashboard_bundle_cost_authority.py

python -m pytest -q -p no:cacheprovider \
  backend/tests/test_dashboard_catalog_cohort_reads.py \
  backend/tests/test_dashboard_card_snapshots.py \
  backend/tests/test_supplier_invoice_report_integration.py

python -m pytest -q -p no:cacheprovider \
  backend/tests/test_dashboard_catalog_cohort_reads.py \
  backend/tests/test_dashboard_runtime_source_contract.py \
  backend/tests/test_dashboard_ads_costs.py
```

Results: the first two runs passed **61** and **73** tests respectively. After
adding explicit fresh-retry/name-race and no-product-line coverage, restoring
empty-result member order, and updating the exact observer-oracle source hash,
the last run passed **44** tests and **7** source-contract subtests.
The only warnings were existing Pydantic V1 validator deprecations.
`git diff --check` passed.

The baseline empty-cohort regression failed on the original full catalog read.
The corrected payload regression was also run against the immutable base
function bodies: all three old builder modes unnecessarily hydrated unrelated
large raw catalog records for a one-product order. New tests verify zero empty
reads, selected full hydration, owner scope, alias collisions/ambiguity,
malformed variant-source precedence, exact full response parity, a known
255-SAR mixed-alias result, unchanged companion queries, and safe hydration
races/retries. Existing bundle, supplier and dashboard-card tests preserve
historical financial authority.

Independent reproducible evidence is in `evidence/`. For the synthetic
1000-product catalog and one selected product, catalog BSON returned to the
client changed from **43,203,670 to 181,885 bytes** and full raw rows from
**1000 to 1**, with the same 75-SAR result. Empty cohorts changed from
43,203,670 catalog bytes and six catalog/companion finds to zero. All six
builder/mode cases preserved both canonical and insertion-order JSON bytes.
These measurements describe the fixture's catalog payload; they are not
production RSS, end-to-end latency or incident-resolution evidence.

## Pending review and exact next action

The runtime gate adapter now audits the exact alias/full projections, owner,
physical winning IDs, cursor limits and returned rows. It normalizes only the
extra selected-document read and the six removed cost reads for the seven known
empty frozen scenarios. Negative controls reject widened or duplicate IDs,
writes, missing nonempty/companion/unrelated reads, forged empty cohorts and
financial JSON or member-order changes. The independent post-catalog mutation
fence runs after full selected cost capture and before companion reads, matching
the original semantic boundary. Separate alias-to-hydration race tests continue
to require the retryable 503. None of the original 164 financial assertions or
output expectations is changed.

The final new catalog-audit suite passed **67 tests**. The preceding combined
run passed **215 tests**, including all 150 existing authority, Google and
payment-fee read contracts; the final additions verify valid Mongo index order
without imposing an unrequested sort. Twelve
mutation cases preserve all six frozen financial totals under synchronous and
cooperative fences. The new gate test is registered in the runtime-adoption CI
workflow. Independent final review and actual real-Mongo CI remain required.

Next: finish the independent gate review, capture the exact source commit, and
run exact-head CI, including all 164 immutable runtime assertions. Then freeze
a fresh source A and intent-only B under release protocol v5 and validate B.
This checkpoint is not deployment approval or evidence of a deployed fix.

## Material limits

- The first alias pass still reads all owner aliases and complete variant
  values up to the existing catalog cap. It is smaller, not constant-memory.
- Companion profile/binding/resource reads remain owner-scoped and capped as
  before; other report, advertising and order reads are outside this patch.
- Selected full documents can themselves be large, and a large selected cohort
  can still require substantial memory.
- The two reads are not a database snapshot transaction. Selected alias changes
  are detected; arbitrary concurrent mutations across the full catalog are not
  claimed equivalent to a single snapshot.
- No production data was used to size the benefit. Production slowdown remains
  an open incident until a separately authorized release and live verification.

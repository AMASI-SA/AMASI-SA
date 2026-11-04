# MZ2-CURRENT-BUNDLE-COST-REPORTING-1706 — Release handoff 2026-11-04

**Owner scope:** Product profitability reports must use the **currently configured Mezan unit cost** for all sold units of an unpriced composite/bundle product, including historical sold units, conditioned on the actual buyer selections and multiplied once by quantity. Frozen/actual/previously approved line authority always wins. Financial reporting estimate ONLY; no supplier, stock, order, invoice, Qoyod or journal writes.

SKU AMS13281 user-approved current inputs: base shawl SAR15; optional rose SAR40; optional existing externally supplied name service SAR10. Unit expected alternatives based on original choices: SAR15/SAR25/SAR55/SAR65. Four rose+name units = SAR260, but do not multiply 65 by all 15 units absent individual choices. Future cost updates change only **unfrozen reporting estimates**; they never rewrite actual/frozen history.

## Exact release base and provenance
- Repo `AMASI-SA/AMASI-SA`; Production branch `hotfix/prod-snap-meta-final`.
- **Latest reviewed predecessor J (as checked 2026-11-04):** `c47738b05397104b8b1a22d133b635cd1ce58c49` (merged PR #1734, canonical store-courier print and deployed performance patches). Its tree differs from reviewed source `9382a74e14a239ab6ca99ecf12d98c3d8a21151a` ONLY in `release/release-intent-v5.json`, by exact Git comparison.
- The previous Draft PR #1713 was based on **obsolete** J `26b92e50e2b07dd3deb7f82cabc4c78c36760729`; the old candidate's v5 release-readiness classification failed because PR #1711 had updated frontend source outside its former intent. **Do not reuse any old freeze artifact or release ID.**
- This source A must be a **single-parent commit** on the latest J, and change ONLY the four files listed below. Generate fresh GitHub candidate v5 intent B from this new source A, keeping A→B an intent-file-only change, then pass exact-head release gates.
- **No live deployment proof is implied by a GitHub branch merge.** Verify actual `mezansalla.com/api/health` source identity and Publishing Idle before any host deployment. The last authenticated screenshot previously observed source `0275986594f8759a0a7b6810b9e5c2c6f8aed9c6` and severe Dashboard 524. Do not blindly replace an unrelated active production deployment with a branch-only state.

## Scoped implementation
1. `backend/current_bundle_reporting_estimate.py` NEW: bounded tenant-scoped, read-only costs from current Mezan product, profiles, conditional recipe children, existing option-service bindings and resources. Match source option/value IDs or uniquely matched exact option/value labels; private free-text never priced; reject missing, contradictory, stale or ambiguous choices and duplicate line IDs. CPU-cooperative checkpoints and no N+1 DB reads. `cost_stage=expected`, `historical_cost_verified=false`, `current_bundle_catalog_estimate`.
2. `backend/order_cost_authority.py` minimal read-only integration: selected estimate ONLY for composite sale lines missing frozen cost and ONLY in CPU-budgeted profitability reporting. Historical forensic readers and advertising/marketing frozen evidence remain fail-closed.
3. `backend/tests/test_dashboard_bundle_cost_authority.py` tests 15/25/55/65, 260, updated 288, frozen precedence, original Salla row IDs, no marketing cost promotion, missing/duplicate options and read bounds, and no operational writes. Existing G47 and source-pinned Dashboard tests must pass without skips.
4. This status document.

`backend/dashboard_v2_routes.py` and `backend/sold_products_report_v2.py` stay byte-identical to J. Do not change protected gate assertions or CPU/read contracts. Do not resurrect old owner-attested per-order correction APPLY endpoints.

## Performance incident and deployment gate
- Open P0: https://github.com/AMASI-SA/AMASI-SA/issues/1716; prior /api/dashboard-v2 125-second HTTP524 and 15-second HTTP200 for *one day only*. Employee app and supplier routes also timed out. Latest Production branch has subsequent performance fixes, not necessarily yet published.
- **Hard gate:** exact-source G47, Dashboard runtime immutable/reference + resource/cancellation tests, Security, CodeQL, Operational Stock Eligibility, Mezan Release Readiness, and actual source-vs-live Deployment identity. Decline release if active P0 or route latency worsens vs branch-matched reference.
- No production database migration needed or allowed. No automatic rollback/disable of Snapchat/employee/accounting jobs. No touching Qoyod.
- After a separately approved guarded Publish, verify live Source SHA and 3/3 release guard, then read-only AMS13281 order `297657209` cost, 15-unit cohort breakdown, top-products/Executive/Sold Products parity and employee app response times. Never invent unavailable choices/costs.

**Checkpoint:** source-only preparation, no merge/no new Publish/no live mutation as of writing.
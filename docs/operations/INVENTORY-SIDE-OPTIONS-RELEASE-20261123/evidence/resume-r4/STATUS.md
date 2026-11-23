# R4 frozen-source local verification evidence

## Source and scope

- Source A: `deeefceffd9a1e37968513ac4f3833314bb415fd`.
- Source tree: `a7317c4aa62c3d2266937bf60d4f0b2c4f62a27d`.
- Parent J4 / PR2096 merge: `28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4`.
- Source PR: #2097. At packaging handoff, root reported exact-A CI underway and readiness passed. Live CI status and candidate/deployment evidence are outside this local bundle.
- This evidence is prepared for a separate evidence branch based on A. It is not a source amendment or a deployment input.

## Observed local results

| Scope | Result |
| --- | --- |
| Backend focused options/purchase/supplier/preparation/bundle/count selection | 267 passed in 6.68s; exit 0; 0 failed, errors or skipped |
| Supplier-option module, included in the focused total | 27 passed in 2.75s |
| Frontend inventory/operational | 231 passed across 9 suites; exit 0 |
| Frontend supplier bundles/layout | 138 passed across 9 suites; exit 0 |
| Frontend combined | 369 passed across 18 suites; 0 failed, pending, skipped or TODO |

The four new cross-feature regression cases use both real service-context seams and the real supplier-leaf builder. Informational service options retain two independent physical leaves with frozen planned cost 50 each (100 total); actual_cost remains unset and receipt proofs absent. Genuine product/customization services remain and reject unsupported bundle materialization. These are planned-cost assertions, not receipt, invoice, payment or journal proof.

## Exact source binding

Independent comparison against immutable Git source A confirmed all 3,134 backend Git blobs and SHA-256 values and all 1,437 frontend SHA-256 values, with exact path sets. Before/after manifests are identical. The reviewed structural oracle `cc6ec8bafbe9badb348eccc6d858c93417762fd6` differs from A only in the new regression test and release STATUS update. The merged service-context blob and inherited release intent are unchanged. See `source-binding.json` inside the ZIP.

## Shared-host observation

Root directly observed terminal3 at `2026-11-23T22:51:47.364293+00:00`: marker `INVENTORY_R4_READONLY_BOUNDARY`; actual `/app` HEAD and upstream J4 on `hotfix/prod-snap-meta-final`; +0/-0; no tracked entries; original guard `{"active":false}`. `guard-observation.json` records this **root-reported CUA read-only observation**, not raw host output. The packaging subagent made no host call. This point-in-time observation does not establish publication; recheck before a shared-host mutation.

## Contents, integrity and limits

`r4-local-evidence.zip` preserves the original R4 backend/frontend reports, logs, full source manifests, structural oracle and regression patches. Historical pre-freeze HEAD labels remain intact and are supplemented by the final-A binding. Jest/dependency caches and candidate artifacts are excluded. Original raw files remain untouched.

ZIP SHA-256: `817162e96130f08a2007a40517449e0947691ffe90159b282e474b009ba93602`. The outer `SHA256SUMS` verifies this STATUS and ZIP. Inside the ZIP, `MANIFEST.json` records provenance, sizes and hashes; its `SHA256SUMS` verifies every other member. ZIP construction is deterministic and was reproduced byte-for-byte, then every entry was read back and checked.

This bundle contains bounded local pytest and Jest evidence. Real isolated-Mongo supplier receipt/native-invoice/rollback tests were not selected locally; frontend tests use DOM/pure functions and mocked API boundaries. No full release gates, release builds, live browser, deployment or live financial behavior are certified by this bundle.

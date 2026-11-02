# Production deployment result — MZ2-BUNDLE-COST-20261102

The user explicitly authorized publication. The merged code is now deployed at https://mezansalla.com and independently verified by the repository's Release Guard v5.

## Exact identity

| Field | Value |
| --- | --- |
| Source A | `dc96a12e72ba3e1e1766c44e37e494ba3b69250f` |
| Prior source base J | `70576e2a06237eb193b1c927f11ced92e2229589` |
| Deployment M | `f8235d57e90f8a1271df6350d7e7919f5621ea11` |
| Release ID | `rg5-0f3fe5bc5d7da01a5dd3c052cb36637a7a49db1536afe476d7ff862d0fac285a` |
| Frontend artifact tree SHA-256 | `bb9024962a20bc664efec18c689927b1c5218d68fbeeb00f788bd9ecf7a393f1` |
| build-meta.json SHA-256 | `451af30fbaa8fc02bb94db13be972086962de3d9f86aefdbc876730b182a485f` |
| build-meta.json bytes | 301685 |

## Verification record

1. Guard status was active:false. Tracked remote files were clean; fast-forwarded J→M while preserving all110 untracked files and an unrelated unsaved editor.
2. Governed `REACT_APP_BACKEND_URL=https://mezansalla.com yarn build`: exit0, built:true, exact reviewed source/artifact, isolated Backend/Frontend package proofs.
3. prepare actor `mz2-bundle-cost-publish-20261102`, prepared_at `2026-11-02T16:02:09.473045+00:00`.
4. prepublish: exit0, ready_to_publish:true, protocol5, exact A/M/release.
5. Clicked Publish changes once in the existing Salla Analytics management UI. A new green event reads `Deployment finished Nov 2, 07:12 PM`; event `fe2fe67a-4302-5e1d-8732-2e5efd3bd499`. Published version `7882d26` replaced `5b4a0a6`. This is the observed current UI wording, not a quotation of the older “Deployment Succeeded” label.
6. `python3 -B scripts/production_release_guard.py verify --url https://mezansalla.com`: exit0, verified:true, checks3. Each probe checked42 Frontend requests, including exact build-meta JSON/MIME with both canonical and cache-busted requests.
7. Observed Backend boot times: `2026-11-02T16:11:59.769515+00:00` and `2026-11-02T16:11:57.412518+00:00`. Both are newer than prepare and all probe identities matched A.
8. Final status: `{"active": false}`. The successful verifier closed its own lease.

The successful guard validates `verified_identity_available`, `backend_runtime_source_verified`, `release_control_source_bound`, `critical_file_hashes_match` and `frontend_build_verified`; these are guard-checked health fields, not invented standalone CLI output fields. HTML shell caching is no-cache/no-store/must-revalidate; both retirement service workers have the expected bytes and max-age0 behavior.

The complete sanitized summary is [production-deployment.json](evidence/production-deployment.json). Runtime logs were available, but no complete Cloud Build transcript was exposed. No claim is made about unobserved platform packaging order.

## Live product follow-up

The dashboard loaded and showed orders arriving after deployment. Its executive and top-product cost totals agreed. The target product remains cost-incomplete because the live definition/historical evidence is not yet repaired.

A prospective definition draft was prepared, not saved: retain the selected-rose rule, add one required unconditional current_product quantity1 with auto sourcing, and set the embroidery resource scope to per_unit. A production save was rejected by automatic approval review because the live catalog/fulfillment mutation requires separate user approval. There was no workaround or alternative mutation after rejection.

Read-only policy inspection found unknown embroidery cost treatment and assembly origin, no evidence reference and no shortage supplier. These must be resolved from actual business facts before adopting a complete schema2 recipe. A separate pre-existing conditional-visibility mismatch prevented the no-name live preview; it was not bypassed.

Historical repairs and approved-ledger changes remain unapplied. Acceptance220 and the synthetic+140/−140 delta are tested calculations, not a claim that the original live80 cohort was repaired.

See [STATUS.md](STATUS.md) for the next safe steps and [REPORT.md](REPORT.md) for the implementation/test report.

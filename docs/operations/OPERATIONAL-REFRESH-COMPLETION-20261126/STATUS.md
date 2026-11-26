# Operational refresh completion — 2026-11-26

## Authority and current verified state

The merchant authorized resolving the underlying accounting refresh problem, merging, publishing directly to Production, and continuing testing until resolved. Preserve historical movements, original dates, bank evidence, financial formulas and idempotency. Keep Mezan light: no extra recurring jobs, query fan-out, blind refresh retries, invented transactions, paid Emergent agent chat or Preview. The unchanged original Release Guard v5 remains mandatory.

Repository: AMASI-SA/AMASI-SA. Follow-up branch: `codex/operational-refresh-completion-20261126`.
Verified current production/base D: `317dc183485566ba9e05c5afca5d4ea1b84e291e`, tree `a8aedd1be45cf7dcb26b31078a6c5e580709e30f`.
PR2133 source A: `50cbe07da4e29fa41c7a184addffdeb8d2169f1a`; intent B: `4067edbfbfd0739382a1c390784bc238a7f00b7f`.
Runtime release: `rg5-5064eab0115d74d39ba29eeed04f13511f5a27156260c0cc5a3a4bc2045e1fb5`.

PR2133 was merged, passed exact A/B/D CI, clean-clone rehearsals and the original-host adapter/package checks, and was published exactly once as provider version `bad38ff` at 05:17 Asia/Riyadh. The original verifier ran once, passed all three exact identity/frontend probes, and closed its lease at 02:34 UTC. Raw proof SHA256: `108220774d22e0074127aa10d63b1dbd5e35afbaaf87998ac14c6bed37da6c4b`.

The task-stopped local Backend was restored at 02:41:22.858577 UTC. A separately reviewed continuation retained the original failed preservation receipt: all 432 untracked paths remain, 431 records are identical, and one platform bookkeeping file retained its original prefix with 378 appended bytes. One credential-file inode replacement is recorded using metadata only. Its contents were not read/hashed/copied/modified, and no actor attribution is established. Original baseline and guard were not changed.

Durable prior-release evidence: `codex/operational-refresh-source-publication-evidence-20261126` at `7ddf136d875cf325189b47f2887de9892405edd5`, path `docs/operations/OPERATIONAL-REFRESH-SOURCE-ROOT-FIX-20261126/verification/production-proof.json`. That documentation-only branch must never be merged or deployed. Do not repeat PR2133's publication or verifier.

## Live acceptance failure

The sole manual refresh on PR2133 started at 02:41:38.999 UTC and returned `operational_refresh_timeout` at 02:43:06.228 UTC. The previous saved snapshot remains in use; no new successful financial calculation or recovery report was accepted.

| Stage | Observed milliseconds | Outcome |
| --- | ---: | --- |
| Native evidence | 11142.523 | complete |
| Raw aggregate read | 119.896 | complete |
| Aggregate unpack | 16287.066 | complete |
| Redundant copy preparation | 0.012 | complete |
| Primary orders cursor | 18058.509 | complete |
| Primary source reads | 18124.681 | complete |
| Bundles | 14857.234 | complete |
| Option cost catalog | 2616.166 | complete |
| Order indexes | 1512.510 | complete |
| Source order loop | 17848.574 | cancelled |
| Total source collection | 57312.655 | cancelled |
| Aggregate | 73830.229 | cancelled |
| Profit snapshot | 25845.285 | complete with product authority cancelled |

These stages overlap and must not be summed indiscriminately. All 19,151 primary orders completed under the explicit 20-second allowance; first row was 11.132 ms, application queue 0.033 ms, cursor close 51.534 ms. The unchanged 85-second outer budget expired before source conversion/reconciliation/save completed. Thus source read admission improved, but the live root-fix goal remains open.

## Diagnosis in progress

Read-only independent work is tracing the remaining order loop and bundle normalization, archive/native snapshot materialization, and the request's dependency graph. Native proof acquisition intentionally precedes primary order reads because adapters write version-bound source evidence. Profit already overlaps collection after primary reads. Do not substitute profit and operational cohorts or authority results without exact projection/freshness/correction parity.

A concrete candidate for investigation is duplicate full validated financial/issue packet decoding: native gap preparation decodes packets, then aggregate unpack decodes the same contents again. Any request-local reuse must bind exact packet/owner/metadata, retain validation and authorization, own cancellation, and avoid mutable aliasing or retained stale data. No implementation decision or production speed claim is established yet.

Current follow-up production source edits: none. New lease/publication/manual retry: none.
Next safe action: identify and measure substantial repeated work, implement only a proved equivalent scoped optimization, run relevant regression and independent review, then create a fresh A/B with all required gates before any further authorized publication. Continue to live saved-calculation and historical-preservation acceptance.

Issue #1006 is at its comment cap; update this task document, its PR, and the evidence branch. Keep private financial reports outside GitHub.

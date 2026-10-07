# Minimal shipping alias hotfix (Draft; not a release)

Base: `a7977f4cfc1ef0a721fc25d783661332f32fe3b6` directly. No #1264/#1270
ancestry, Business Snapshot, Order Change Engine, dependency or financial changes.

## Contract

Only `shipping.company_name` is normalized. A nonempty scalar name equals the
same scalar `company`; when both keys exist, the nonempty strings must match
exactly. With a company object, only its `name` is compared to the alias; the
entire object (including id/code/logo and unknown metadata) is retained. An
object never becomes a scalar. Empty/null aliases are not equated with absence;
conflicting/empty aliases alongside a company reject with
`409 review_completion_source_changed`. No whitespace/case/number/address/
customer/product normalization is added. The input document is never mutated.

New operations persist `source_fingerprint_version=2` before provider I/O.
Missing version means original Production v1; invalid/unknown versions fail
closed. Retry and explicit reapproval compare using the original operation's
version. No historical rehash, operation upgrade, recovery or backfill.

Order/DTO fingerprint, acceptance/config fence, revision, component/generation,
cancellation, provider readback, leases, resume worker and workflow/event/result
transaction remain unchanged. Unknown fields inside the existing shipping hash
remain guarded. This does NOT add #1270's unknown-root/DTO coverage or source
transaction fence. Existing Production limitations are not claimed fixed.

## Fresh isolated evidence

MongoDB **8.0.12**, loopback replica set `shippingalias`, dedicated disposable
fixture databases. Python 3.13 on Windows; dotenv disabled. Provider transport
and authentication are synthetic; refresh, webhook ingestion, mapper, completion,
worker and catalog code are real. No live Salla/Production requests.

Same test, actual refresh enrichment during provider call:

| Source | HTTP | Durable state | Provider confirmed | workflow | completion event | reviewed |
|---|---|---|---|---|---|---|
| Production base | 409 source_changed | provider_confirmed | true | 0 | 0 | absent |
| Candidate | 200 | completed | true | 1 | 1 | visible |

Baseline executed with the Production completion module explicitly loaded from
the baseline worktree and pytest importlib mode, not candidate imports.
This reproduces a representation class, NOT any historical customer's order.

Review suite: **157 PASS + 36 subtests**, zero failures/errors/skips. Includes
the new shipping tests plus existing acceptance, provider timeout/readback,
transaction rollback, auto-resume, preparation uniqueness and catalog suites.
New tests cover object metadata, conflicts/null/empty, unknown shipping,
product/quantity/SKU/options/payment/totals, independent DTO rejection, immutable
old operation version, unsupported version, repeated requests, two workers plus
manual retry, and an actual killed/replacement worker process after enrichment.

Local evidence directory: `D:/codex-test-runtime/review-minimal-shipping/`:
`before.log/xml`, `review.log/xml`, `g47.log/xml`. GitHub exact-HEAD workflow
artifacts provide remotely retained test evidence after push. Existing CI gets
only one additional test-file selector; no trigger/security changes.

Security Gate remains mandatory. Inherited dependency/provenance blockers are
not waived by these Review results. No Merge/Deploy/Prepare/Prepublish authorized.

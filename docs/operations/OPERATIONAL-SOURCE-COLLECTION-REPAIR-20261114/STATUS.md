# Operational source-collection repair — verified source, release pending

## Production failure and base

The production base J is `9c71435f4a19977c09f4331dee8fddbfa12a45eb`, tree
`c5bd7fb6c357e17c17104282d1d175676e95ed3e`. PR1916 was merged, published as
platform `bfa6a5f`, and passed official identity verification, but the visible
financial report retained its previous snapshot.

The controlled visible attempt begun 2026-11-14T03:57:55.990Z completed native
evidence in 2926.310ms and snapshot loading in 9084.282ms. Source collection was
cancelled after 72942.769ms at the 85040.132ms total-request deadline (HTTP408).
Reconciliation did not begin. This identifies the remaining phase, not the
specific subphase or the share of time attributable to the fee-history scans.
Three financial attempts occurred on PR1916; no additional attempt was used
while implementing this follow-up.

## Change

`operational_courier_fee_history.build_recorded_courier_fee_lookup` builds one
attempt-local lookup over complete saved facts, obligations and allocations.
The collector creates it through the existing owned `prepare_in_thread`
lifetime and calls it at the two existing recorded-fee checks. Every collector
invocation and CAS retry builds a new lookup.

The original `recorded_courier_fee` implementation remains byte-for-byte
unchanged. Ordinary dict/list/string structures use the index; unusual
structures use the original reader. The index does not interpret amounts
during construction. It preserves literal prefix matching, early proof
precedence, original obligation insertion before set construction, lazy
Decimal conversion and global allocation order. No history or contradictory
evidence is omitted. No database read or financial write is added.

Fixed-name `refresh_stage` wrappers distinguish source collision, primary
reads, fee settings, setup, statements, delivery history, transactions, native
links, bundles, option catalog, orders, invoices, other sources, native overlaps
and finalization. Existing finally-based logging identifies the cancelled
subphase at the end of the request. Only fixed stage names, elapsed time and
outcome are logged. The timing-only edit was independently AST-equivalent to J
after removing just these wrappers/import; the integrated collector was also
AST-equivalent after removing the intended lookup substitution.

## Verification

- 129 new cases: 116 fee-index/parity/integration cases and 13 source-stage
  timing cases. Each executed and passed locally.
- Root integrated selection: **339 passed, 3 explicitly deselected, exit0**.
  The three deselected cases require the unavailable local Mongo replica set
  at loopback port27305. The first unfiltered run recorded 223 passes and those
  three connection-refused failures. They remain mandatory in exact-A CI with
  its real replica set; no CI skip or exclusion was added.
- Independent comparison used the exact J function in the same process under
  hash seeds17 and73: **18,693 comparisons per seed, 37,386 total**, both exit0.
  It compared return values, exception type/message, Decimal flags and input
  preservation, including malformed structures, NaN/Infinity, colon-containing
  identities, empty obligation sets and short-circuit behavior.
- Whole-collector tests compare source results, reconciliation, ordinary
  reports, financial position and source read order against the legacy lookup,
  including replay and old/new delivery paths.
- Actual visible collector disconnect/deadline tests stop and join the
  preparation worker before returning, release only the owned lease, leave
  financial/source state unchanged and retain no orphan tasks.
- Independent operation counts: old work16448→65664 visits for64→128 rows and
  calls (3.992×); indexed work including construction514→1026 (1.996×).
  This counts instrumented loop visits, not every operation in sorting.
- Independent negative control replaces the lookup with J in memory and fails
  the doubling bound as expected (exit1). The implementation's own regression
  negative control independently fails its intended growth assertion on J.
- Timing tests cover failure/deadline at collision, primary/settings/transaction
  reads and order conversion, hidden/unauthorized admission, private-canary
  redaction and retention of the cancelled stage among the last100 log lines.
- `git diff --check` and syntax checks passed. The old delivery reader's SHA256
  remains `0a64e6a27dcf9e49afe23b0240c15f0ba4bd74c3ea79aafe6b6f5e056c7eabcc`.

These test selections overlap and must not be added together as unique cases.

## Paired synthetic measurements

Each row is one bounded, sequential old/new pair on identical in-memory
inputs, with the old function extracted from immutable J. New time includes
the complete index build. All four children and the runner exited0, returned
equal expected results and preserved their inputs. No database was opened.

| History rows per group | Lookups | Evidence path | Old wall | New total wall |
| ---: | ---: | --- | ---: | ---: |
| 1000 | 1000 | No direct saved fee | 0.396461s | 0.008789s |
| 4000 | 4000 | No direct saved fee | 5.915601s | 0.030327s |
| 20000 | 250 | No direct saved fee | 1.821549s | 0.056824s |
| 20000 | 20000 | Direct saved fee control | 0.007265s | 0.065864s |

The direct-proof path already avoids historical scans; indexing adds about
0.0586s in that control, including a 0.0491s build. It is not a speedup there.
These are local synthetic observations, not measured production acceleration
or proof that the full refresh now finishes within85 seconds.

## Preserved scope and release requirements

Owner scope, complete history, contradictions, source row caps, authorization,
CAS and the85-second request deadline are unchanged. No assignment, product
cost, reconciliation or bank-audit optimization is bundled. No payment,
delivery confirmation, payout, settlement or provider write is used as a test.
Original /app has120 unrelated untracked files and unsaved Untitled-1; this
follow-up has not changed that workspace or created a release lease.

The new helper is automatically governed by the complete backend runtime source
manifest once tracked in A. The separate critical-file subset is unchanged.
Both new tests match the existing Operational and Stock workflow globs.

Source branch: `codex/operational-source-collection-fix-20261114`.
Documentation-only continuation:
`codex/operational-source-collection-handoff-20261114`. Issue1006 reached its2500-comment
limit; the final ledger entry documents continued tracking through task PRs.
Exact immutable A/B/M identities and subsequent CI/release evidence belong in
the task PR and continuation branch, without editing frozen source A.

Next: freeze source A, verify all exact-A CI and all129 new cases in both
official JUnit artifacts, then freeze reviewed intent-only B. Require normal
merge with the B-equivalent tree, the actual original-operator build,
prepare/prepublish, one publish, explicit newer platform completion, official
three-probe verification and fresh inactive guard. Finally trigger one visible
financial refresh and require a fresh as_of. Do not claim resolution from
publication or generated_at alone.

## Immutable source checkpoint

Source A `8839d3aeb32b4bda2de2015efb0deca7fbb844a3`, tree `492727ceae6b6cada3894e7a450f837186f9a962`, parent J `9c71435f4a19977c09f4331dee8fddbfa12a45eb`, is frozen on [https://github.com/AMASI-SA/AMASI-SA/pull/1917](https://github.com/AMASI-SA/AMASI-SA/pull/1917). All six source/test/status blobs were verified against local Git blob hashes before the ref was moved. Exact-A CI is running. The new helper/test and source wiring match independent review. Local proof files, exact129 new case names, independent harness/results, raw failed local-Mongo attempt and successful339-case selection, and all paired synthetic evidence are preserved under evidence. The local-Mongo connection failures are not represented as passing tests; exact-A CI must execute them with the replica set.

No intent B, merge, original build, lease or publish has occurred for PR1917. Original workspace preservation remains required. After publication/official verification, use one visible refresh; avoid opening cached Logs beforehand and capture its fresh substage output near the request deadline.

## Candidate identity verified

Readiness run43557214320 passed on exact A. Candidate artifact14097739901 is376375 bytes, ZIP SHA256 b4d0327379c7a8144909b49e57be719f2f9b3bc0299b8c4de8ac1e552ee521eb. Unmodified intent is1302197 bytes, SHA256512e8d5b4c459ad664f9c33abd8f8e127896bf54e64e058840383e55cc59a53c, Git blobcc47829c141bb997089a05baac832207f3a1537f. Official candidate loader actual exit0 (2.805837974s), A/J match, R `rg5-e81e97c26945236de423fc17e997db852574b424ba799a3fbba37f4a71d4b7a4`; frontend artifact tree9a34bf9ba4d65a89b3431002ab8e729d6b5311a332ec17800abffb88c2406ab6. The harmless missing local generated backend/release_identity.json warning was retained; this loader is validating the external candidate, not asserting a local deployed identity. Independent module audit confirms the4385-byte new helper appears exactly once in both complete backend manifests with the reviewed Git blob and SHA256, and matches git show A. Local owned branch/index/worktree are now clean at A without changing working file bytes. Exact-A Operational, Stock and Python CodeQL remain in progress; no B is frozen yet.


## Reviewed intent B frozen — 2026-11-14T04:46:46.970Z

All 11 exact-A workflows passed; 27 checks comprise 21 success and 6 expected skips. Official Operational artifact has 4202 passing cases; Stock has 4011 passing cases plus 7 passing subtests. All 129 new cases and all 3 locally deferred Mongo cases occur once and pass in both XML reports. See evidence/exact-a-artifact-verification.json and evidence/exact-a-final-ci.json.

B is `1469f4916715458b8374beb3bfddfc1f3c01f125`, tree `9d8844f32f9db18cd036ff1b55edaec2c0dbe1b8`, direct parent A. Its only changed path is release/release-intent-v5.json, whose exact candidate Git blob is `cc47829c141bb997089a05baac832207f3a1537f`. The full official tracked-intent loader returned actual exit 0 in 2.292 seconds. Local owned worktree is clean at B. Runtime remains `rg5-e81e97c26945236de423fc17e997db852574b424ba799a3fbba37f4a71d4b7a4`.

B CI, including actual Host20/Host24 clean-clone adapter rehearsals, is pending. No merge or PR1917 original /app mutation, lease, publish or financial refresh has happened. Original operator browser remains connected; 120 pending changes and one unsaved editor file are still present. The new rehearsal helper is source/AST checked and awaits independent review; the verify helper has independent read-only PASS. No helper has been executed.


## Normal merge complete; original build pending — 2026-11-14T04:52:56.512Z

B final gate passed: 35 checks = 25 success + 10 expected skips, no pending/failure/neutral. All 13 applicable workflows passed; one TikTok workflow was expected skipped. Both real Host20/24 clean-clone adapter artifacts independently prove built:true, no Git during adapter, matching A/R and frontend/backend identities.

Normal merge M is `2db0edc9edb315026eee4b6edd43b420a569c37d`, parents [J,B] in that order, tree `9d8844f32f9db18cd036ff1b55edaec2c0dbe1b8` identical to B. No source differences were introduced by merge. Original preflight finished 2026-11-14T04:47:47.678319+00:00 with active:false, tracked/index clean and all 120 unrelated untracked files. Unsaved Untitled-1 was separately observed in UI.

Rehearsal helper independent source/AST review passed at SHA256 `3a38a680fcf5e7696b510c5edd73de515b919bf5927f8759e64ee1361a596f4d`. Next run it once with --build --deployment-sha 2db0edc9edb315026eee4b6edd43b420a569c37d. It performs fresh inactive checks before FF/build and preserves all existing files. No PR1917 build, lease or publish has begun yet. Actor planned: codex-operational-source-collection-rootfix-1917.

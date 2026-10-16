# Supplier invoice performance release and rollback

## Current integration checkpoint (supersedes earlier release targets)

Production base: `1ac8b530b7238cbbb985e770019dc897f158eb13` (PR #1397).
Branch: `codex/supplier-performance-integrated-20261016`.
Preserves PR #1395 supplier option surcharge refresh and all Snapchat changes.
The three performance runtime files match #1391 source `7f33125` exactly.
Fresh integrated CI, source/intent build and deployment proof remain required.
Earlier intent artifacts and rollback branches below are historical and must
not be deployed on this base.

For rollback, revert only performance commits `9e17d34da` and `69050d67a`
on a new branch from the actual deployed HEAD, preserving later unrelated
changes. Keep financial data and optional image cache intact. Verify code
equivalence to this base for the affected runtime files, rerun invoice and
option-refresh tests, and create a fresh reviewed source/intent release.
Do not reset Production or deploy an earlier whole-repository snapshot.

Owner authorized deployment and a recoverable code rollback on 2026-10-16.
Ready/shipping automatic completion is excluded: its provider conditional-write
contract remains unresolved. Do not activate #1321 as part of this release.

## Verified preceding release

- Production deployment J: `1674bb8b0a7a995e6ea87c83db93feb7d1a12e70`.
- Source A: `f603b4ef8fd3c3a4238b5aafb7ae4f6fa73d5bba`.
- Runtime identity: `rg5-4d03da391a1d172993cfc7353b5505a94ecf0bfed9bdc035a97531ccc558f262`.
- Build44 release owner handed off `verified:true`, `checks:3`, `active:false`.
- Their retained verification: `/root/build44-release-evidence-20261016/verify.json`.
- Public health independently returned this source and verified backend,
  frontend and critical-file flags. Public health alone does not replace guard
  verification or lease inspection.

The supplier candidate was rebased onto J before intent freezing. Against its
previous CI-tested HEAD `6920f55fc57a522685b19f388767c0ef7838436f`, runtime source
is unchanged; the inherited release intent changes to J's reviewed intent.
The new candidate and intent need their own release evidence.

## Scope and activation checks

The change prepares optional thumbnails asynchronously, reuses persisted
tenant/URL-scoped images, and renders PDFs in a worker thread. It caches successful
index initialization per router. It does not change invoices, totals, posting,
payables, receipt/recovery journals, scan locks, native close identity or Android.

Before release: require fresh exact-head CI, the governed two-build A/B proof,
intent-only commit B, clean-clone adapter rehearsal, clean Production checkout,
fresh fetch, inactive lease, prepare and prepublish. Only then publish once.
Require explicit deployment success, three guard verification probes and an
inactive lease afterwards. Do not use financial writes as a smoke test.

Optional missing images are an expected tolerated outcome. Sustained new PDF
endpoint failures, invalid PDFs, authorization regressions, financial integrity
errors or material resource regressions are reasons to stop rollout and assess
rollback. Do not call a transient image miss evidence of financial corruption.

## Rollback contract

Rollback restores only this task's changed runtime/test surfaces to the preceding
verified release. Preserve Build44 recovery and every later unrelated change.
Prepare a new task branch from freshly fetched Production; never reset or force
push Production and never deploy an old intent over a different source tree.

The supplier source changes originated in these commits after rebasing on J:

1. `eb6792ab65091aa2f042985aec0c065850b08380` (image/index implementation).
2. `cf835de2b03bb24ac685821aab9a222fc81c8982` (financial source test adaptation).
3. `a894150d7d485a2e6563d489e7f870819be91c0c` (compatibility tests/evidence).

On the rollback branch, revert those three in reverse order with `--no-commit`,
inspect and resolve any later overlapping edits without discarding them, then
commit a new source A. Retain the currently reviewed release intent unchanged
until a fresh governed A/B build creates the rollback intent-only B. Validate
invoice authorization, native posting/rollback/replay and Build44 recovery on
the rollback tree. Publish that new reviewed source/intent pair through the same
lease, prepare, prepublish and verify gates. Rollback therefore requires a new
governed build; it is not an instantaneous database or package switch.

No database migration reversal is needed. Leave optional thumbnail cache rows
and indexes in place; the previous renderer ignores the cache. Never delete
invoice records, reset request/attempt identities, erase uncertainty markers,
replay supplier closes or resend Salla POSTs to perform rollback. An application
rollback cannot undo a financial/provider side effect; this release introduces
neither such side effect nor a data repair.

An isolated rollback rehearsal must demonstrate that the reverted runtime/test
files match J, with the current release intent untouched. The release controller
must recheck the actual final Production history before using this recipe.

## Replacement candidate after concurrent Production advance

Before this task merged or created a lease, another authorized task merged
PR #1386 and advanced both Production and /app to
`d29b1a3ad0c61f7ace686b7709a7577ebda40d13`. The frozen candidate in
PR #1384 remains preserved and must not be merged over that new intent.
The replacement branch is `codex/supplier-invoice-current-release-20261016`,
based directly on d29b. It keeps all Snapchat and Build44 changes. The three
supplier commits to reverse on this replacement are, in reverse order:

- `2ffec7e47f5bb9b0c1c4980b7493cb7b040141dd`
- `c7a53ba78e2f2f81e0569234b66f51aa98ad7a5f`
- `56c6e915a2510ca7d02c8ac96afac4432cba6557`

Supplier runtime and tests are byte-identical to the validated #1384 source.
Its 88-test rollback rehearsal is historical evidence; the final replacement
requires its own ancestry/rollback comparison, CI, build identity and deployed
verification. Preserve d29b Snapchat changes during rollback. Do not assume
that a merge alone proves d29b is deployed; verify release ownership and runtime
before publishing this replacement.

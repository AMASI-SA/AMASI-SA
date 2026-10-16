# Supplier invoice performance release and rollback

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

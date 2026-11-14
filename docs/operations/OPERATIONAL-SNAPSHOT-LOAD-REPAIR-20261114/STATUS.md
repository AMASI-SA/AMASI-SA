# Snapshot-load repair — verified source A

Updated 2026-11-14T03:01:42.945Z. This documentation branch is a continuation checkpoint only; never merge or publish it.

## Current state

PR [#1916](https://github.com/AMASI-SA/AMASI-SA/pull/1916) source A is frozen at `ff9f8b69b980acfda8dffd210feaf72e17e6727a`, parent J `4860548f0b032e8e3beca18a4776413cd7e0368b`. All 11 A workflows passed; 27 checks are terminal (21 success, 6 expected skips). The exact-A official artifacts prove all 43 new regression cases pass in both the 4073-case Operational suite and the Stock suite (3882 cases plus 7 passed subtests). No test failure, error or skipped case appears in those JUnit artifacts.

The candidate ZIP artifact `14094887136` from readiness run `43548544254` was downloaded and its SHA256 `292052e41d49917462181ee1c09d88f966cf83f7754a585a5bfefce94bf33a44` verified. Its unmodified intent is 1301645 bytes, SHA256 `9c4374110d7cb7ad4107acd968b419982cbecd5be934c34ebe80ebe30cd2dd47`. The official candidate loader passed with actual exit 0 in 3.556 seconds, exact A/J, release `rg5-6e9b6b1216ad24c83d92f78483511cb6d6b5539a29bae69614dd088063b8a5db`, and frontend tree `9a34bf9ba4d65a89b3431002ab8e729d6b5311a332ec17800abffb88c2406ab6`. The missing local generated backend release identity warning is recorded separately; it did not invalidate the exact candidate/source checks.

## Problem and scope

The previous PR1915 is merged and published, but live financial acceptance failed. A deliberate visible reproduction measured native_evidence 23.437 seconds, then snapshot_load cancellation after 59.694 seconds at the unchanged total request deadline, before source collection or reconciliation. PR1916 bounds fresh authorization/lease gates around groups of at most eight archive pages for the exact active demand database and owner. Every page is still loaded by exact owner and ID, sequentially, and fully validated before the next. Full history, manifest integrity, cancellation, deadlines, strict session readers and commit safeguards remain covered. The code changes authorization cadence within bounded groups; it does not cache authorization.

## Verification and limitations

Local regression: intended RED 136 checkpoints versus the 24 bound; final 43 focused tests passed. Integrated adjacent selection: 106 passed. Independent selection: 79 passed, including the same 43 new cases; independent exact-J negative control failed only the intended bound. These selections overlap and must not be added as unique totals. Three paired synthetic 64-page runs retained all 64 reads and complete state equality while reducing median simulated latency from 2399.883 to 665.631 ms and gate count from 136 to 24. This is not a measured production speedup.

The old WIP test checkpoint is explicitly historical; its bytes differ from both the first RED file and final A. See STATUS.json for exact hashes. Source A's own status remains frozen. Current CI and release progress belong here and in PR1916.

## Next required work

1. Create intent-only B from the exact reviewed artifact; validate it and pass B CI including actual Node20/24 adapter jobs.
2. Normal merge with current production J unchanged, two expected parents, and identical B/M tree; pass merge checks.
3. Perform the actual build in the original operator and preserve its 120 preexisting untracked files and editor work. Prepare with an owned lease, final prepublish immediately before one manual Publish, observe explicit completion, and run official verification through three probes and normal lease closure.
4. On the visibly open financial page, require a new as_of and no refresh warning. Release identity success alone does not meet live acceptance.

No production deployment has been made for PR1916 yet. No confirmations, settlements, payout, provider or ads writes are part of verification.

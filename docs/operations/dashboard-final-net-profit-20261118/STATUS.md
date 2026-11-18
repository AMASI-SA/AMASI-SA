# Dashboard final net profit — publication approval handoff

## Task and exact release identity

- Task: `dashboard-final-net-profit-20261118`.
- Repository: `AMASI-SA/AMASI-SA`.
- Implementation branch: `codex/dashboard-final-net-profit-20261118`.
- PR: https://github.com/AMASI-SA/AMASI-SA/pull/1987 — merged ordinarily.
- Reviewed production base J: `37a8211c383079711839557ecad6d5f3f0cdd51f`.
- Reviewed source A: `f8bf7be75dbd4976a04c1f77b0722267427ab889`.
- Intent-only B: `e5c4df4ab7431ca28309d8de1f14288b87109329`.
- Production merge M: `68035e55018d5b75bdd6b9e0ab6000d7528a956f`.
- B and M have the exact same tree: `2afebe36d1cf92c1709896260e02b2e680e805e4`.
- M parents are exactly J and B. A-to-M changes only the tracked release intent.
- Runtime ID: `rg5-c63a56a1935d1a8493c3751adfb9d721c651f137183132ccda139c2cdaf67b0b`.
- Frontend artifact tree: `a79500c615ace0792560a52004407e9b7773c0b529d2a24cf6f3a466cf548be3`.
- Build metadata SHA-256: `f47d9d29dd2096be4aea4ea33ae5621bcde9dd765e342b5b27fa89840aa49f3a`.

This documentation-only handoff is on a separate branch based on M. It is **not a deployment candidate** and must not be merged into the immutable A/B/M release merely to update a status document. PR1987 records this handoff's full commit SHA. Issue1006 is at its comment limit; do not overwrite its body or retry comments.

## Completed behavior

The latest user screenshot confirms that the existing unassigned Snapchat row appears, but mapped account profit is still blank and the existing total is before operating expenses.

The correction exposes independent known-cohort cost completeness without changing full-account attribution completeness. It displays a separately labelled contribution for mapped Snapchat orders only when identity, catalog, time, currency, costs, account spend and bank-fee evidence are valid. It subtracts the full account spend and bank fee once. Full-account profit remains incomplete while unknown orders exist. Unknown orders stay separate and are counted once in authoritative totals; no invented spend/profit allocation or total aggregation is introduced.

Two rows are added inside the existing seven-column table: operating expenses and final net after all expenses. Final net is taken directly from the same canonical executive snapshot and eligibility helper as the executive summary. Fees and operating costs are not subtracted again. Missing/currently mismatched dates, spend, costs or currency hide stale profit. Signed zero and negative values remain valid. Account and half-day contributions are labelled as before operating expenses.

## Verification completed

- Focused backend locally:85tests passed.
- Actual executive-route ASGI/cache round-trip: all six new evidence fields survive JSON and the process-local60s cache, including false/null and true/zero. No migration or cache purge is required.
- Source-A four-platform run44339059541:580frontend tests /17suites, production build/UI contract;362backend tests +5subtests.
- Source-A executive-date run44339059601:175tests /9suites, build/contract.
- Source-A profit-date run44339059362:81tests /6suites, build/contract.
- Source-A readiness44339059874:57build-contract tests, reproducible A/B build/proof, candidate freeze, backend preflight and host20/24dispatcher contracts.
- Candidate artifact14441654390 ZIP SHA-256 `d77e03705b4509245e61fa59a7bc6323d66bbdd0966cd51a67323541acfc13fa`; downloaded bytes and official local `load_candidate_release_intent` validation passed.
- Reviewed-B readiness44340048729:successful frontend build, backend preflight, both host dispatchers, and clean-clone deployment-adapter rehearsals under host Node20.20.2 and24.19.0.
- Reviewed-B Security Gate44340049506:backend contracts passed; frontend419suites /4,408tests passed; zero production dependency vulnerabilities and both CSP builds passed.
- Reviewed-B CodeQL44340049454:Python and JavaScript/TypeScript both passed.
- Independent integrated source review and follow-up fixture/workflow review found no remaining blocking issue.
- Independent clean clone in Emergent:actual configured `cd frontend; yarn build` passed, exit0 in102.41s, observed host Node24.19.0/Yarn1.22.22 and governed Node22.23.2 linux-arm64. Intended runtime/artifact/build-meta hashes matched; package boundaries passed and clone stayed clean.
- After an inactive-guard check, /app fast-forwarded J→M with unrelated untracked files preserved. Its actual configured adapter passed exit0.
- Guard prepare and prepublish passed for M and the exact runtime identity above.

Suite counts overlap and must not be added as a deduplicated total. Local rehearsal evidence is not a Cloud Build transcript; no new Cloud Build was submitted.

## Publication blocked by automatic approval review

The last attempted action was Emergent **Publish changes** for the existing https://mezansalla.com website. Automatic approval review rejected the action before execution because there was no explicit authorization for publishing this new PR1987 fix; the earlier authorization was scoped to PR1969.

Do not retry the publication, use an alternate deployment route, or treat this document as authorization. Ask the user explicitly to approve publishing PR1987 to the existing website. This is the only remaining authorization boundary; code implementation, merge and release preparation are complete.

The task-owned lease had actor `codex-pr1987-dashboard-final-net-profit`. After the rejection, the owner safely aborted it with the exact deployment SHA and runtime ID. The official status then reported **active:false**. No other lease was cleared.

- Production Git branch changed:yes, merged M.
- Shared /app source/build changed:yes, M and verified local adapter output.
- Running production website changed by this task:no.
- Publication submitted:no — final click rejected by automatic review.
- New production identity/live table acceptance:pending publication; do not claim the visible issue is fixed.
- Advertising/provider refresh, budget, financial data and attribution writes:none.

## Exact next safe continuation

1. Read AGENTS.md, this record and PR1987. Confirm the user's new explicit publication approval.
2. Fetch production and the task branch; confirm M/A/B/tree/runtime identity still match. If production advanced, do not silently publish this older candidate.
3. In /app run `PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py status`; require active:false. Verify the branch/head and preserve unrelated files.
4. Revalidate current materialized artifacts; rerun the configured adapter if state changed. Do not reuse a lease from this handoff.
5. Run `prepare --actor codex-pr1987-dashboard-final-net-profit`, then `prepublish` immediately before the authorized Emergent Publish changes action.
6. Observe a newer explicit Deployment finished success, then run the official `verify --url https://mezansalla.com`. Require all three probes, intended release/source identity, critical hashes, frontend verification and exact JSON build metadata/MIME. Require final status active:false.
7. Reload the authenticated dashboard normally. Confirm mapped partial Snapchat profit appears where eligible and the table final net equals the executive net for the same period. Verify operating expenses and single bank-fee deduction; unknown orders remain separate. Do not use provider refresh merely to replace an old UI snapshot.
8. Record the actual deployment/live acceptance and final checkpoint.

Emergent remains at the Publish changes confirmation for the existing website. The browser tab is marked for continuation. The user need only authorize the new publication; no code work needs to be restarted.

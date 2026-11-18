# Dashboard final net profit — published and verified

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

The reported issue was that the existing unassigned Snapchat row appeared, but mapped account profit was blank and the existing total was before operating expenses. The new production UI has now passed acceptance for these corrections.

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

Suite counts overlap and must not be added as a deduplicated total. Local rehearsal evidence is not a Cloud Build transcript. The separately authorized production publication and official verification are recorded below.

## Completed publication — 2026-11-18

The user explicitly approved publishing this PR1987 correction to the existing https://mezansalla.com website. This resolved the previous automatic approval rejection. The same normal Emergent publication route was used; no alternate route or approval bypass occurred.

Immediately before publication:
- An independent GitHub check confirmed production still at M and PR1987 merged at M. All 11 postmerge check-runs passed.
- Postmerge Security Gate 44341161203, Release Readiness 44341161303 and CodeQL 44341161352 all succeeded.
- Official /app guard status was inactive, tracked files were clean, the production branch was correct and HEAD was M.
- Fresh task-owned v5 lease actor:`codex-pr1987-dashboard-final-net-profit`.
- Prepared at:`2026-11-18T16:45:48.756242+00:00`.
- Prepare revalidated the frozen source/deployment/runtime and materialized artifact bindings.
- Prepublish returned `ready_to_publish:true` for source A, deployment M and the exact runtime identity above.

Emergent accepted **Publish changes** and displayed **Deployment Started Nov 18, 07:47 PM**. The UI later showed a newer explicit **Deployment finished Nov 18, 07:57 PM** (Asia/Riyadh), with platform version `8abac0f`. The observed stages were packaging, building, and publishing to the cloud.

The UI exposed recent runtime logs with zero reported error/warning log entries. It did not expose a complete Cloud Build argv/cwd transcript or adapter stdout. Do not claim those unobserved platform details; local rehearsal and deployed byte/source verification are separate evidence.

## Official production verification — passed

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py verify --url https://mezansalla.com
```

The official command completed successfully. The owner parsed its JSON and asserted the exact source SHA, deployment SHA and runtime ID. A subsequent official status check reported inactive.

- `verified:true`
- `checks:3`
- Protocol:`5`
- Source:`f8bf7be75dbd4976a04c1f77b0722267427ab889`
- Deployment:`68035e55018d5b75bdd6b9e0ab6000d7528a956f`
- Runtime:`rg5-c63a56a1935d1a8493c3751adfb9d721c651f137183132ccda139c2cdaf67b0b`
- Backend boot:`2026-11-18T16:56:34.209657+00:00`
- Frontend artifact tree:`a79500c615ace0792560a52004407e9b7773c0b529d2a24cf6f3a466cf548be3`
- Verification completed:`2026-11-18T17:11:00.093804+00:00`
- Final lease state:`active:false`

The unchanged official verifier requires three consecutive successful health/source/runtime checks, verified Backend source and release-control bindings, matching critical file hashes and Frontend build proof. It checks the reviewed 293 public files, the SPA shell routes, and exact build metadata bytes/MIME under canonical and cache-busted requests. It also checks safe shell/service-worker cache behavior. No shortened or substitute verification was used.

## Live UI acceptance — passed

The authenticated https://mezansalla.com/dashboard-advanced page was reloaded after deployment. Acceptance was repeated after official verification, with the date control showing **18 November 2026**.

- Both mapped Snapchat account rows display signed partial profit with **جزئي: الطلبات المسندة**.
- The positive and negative values both render correctly.
- **سناب غير محدد** remains separate with **بانتظار توزيع الصرف**. Unknown orders are not allocated an invented spend/profit and are counted once in the authoritative total.
- Operating expenses appear in the new table footer.
- **صافي الربح بعد جميع المصروفات** appears and exactly equals the executive net in the same live DOM snapshot.
- The table total before operating expenses minus the displayed operating expense equals that final net.
- The UI explicitly states that bank fees are counted once.
- The current-period final net retains the same estimate-until-ad-sync status as the executive summary.
- The live dashboard tab was preserved for the user to review.

Live business amounts, customer/order details, credentials and environment values are intentionally excluded from this repository record.

## Final state and next safe action

- Production Git branch changed:yes, ordinary merge M.
- Running production website changed:yes, explicitly successful new Emergent publication and all three official probes.
- Live requested behavior:accepted.
- Task-owned lease:closed automatically by successful official verification; status inactive.
- Provider synchronization, financial-data, advertising, budget and attribution writes:none.
- Unrelated Emergent files and the user's existing editor state were preserved.
- No remaining blocker for this publication.

The task is complete. Do not republish, recreate the completed lease or rerun a deployment to resume this record. If the user still has the prepublication page open, a normal reload shows the new UI. Future changes require a new task and the ordinary AGENTS.md release protocol.

This record is on the documentation-only handoff branch and is not a new release candidate. Frozen A/B/M and the production branch are not changed by this status update. PR1987 is the current continuation ledger because Issue1006 has reached its comment limit.

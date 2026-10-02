# C5 rehearsal finding — Stage11 saved source status

The complete connected setup rehearsal saved an effective fee policy, its explicit selection and provider evidence. Server preview accepted those facts. On reload, however, Stage11 navigation and the Stage15 review row displayed missing evidence. Root caught the discrepancy by reading the actual reviewed-screen screenshot, after the original harness had reported16 scenario passes.

The financial adapter intentionally projects monetary provider rows but has no separate monetary `payment_fees` section. The saved policy and its evidence belong to the existing shared `providers` section. The view incorrectly read absent `sections.payment_fees` metadata. This is presentation wiring, not a missing economic contract or writer.

`OnboardingWizardView.jsx` now reads that existing provider metadata for the bound, supported Stage11 display, in both navigation and review. It explicitly says `قسم المزوّدين: ...`: completion of the shared section is not represented as verification of a selected fee policy. The unchanged server preview separately checks selected effective policies. Unsupported and unbound views retain their prior behavior. No monetary projection, request, backend, guard or write-control changes.

Evidence:

- Connected before-fix execution passes stages1–10, then fails the new actual Stage11 visible-status assertion.
- New component cases fail4 times on the original view. They retain complete/incomplete/missing-evidence/N/A distinctions and prevent stale local fee-draft evidence from replacing the saved provider source.
- After fix, three affected suites pass47 tests; UI build passes.
- Fresh post-fix connected rehearsal passes all16 declared setup cases, including Stage11 navigation and Stage15 status/evidence rendering. Independent24 preview legs and4929/4929 balance remain unchanged. Both test owners retain their initial controls (only the setup owner's existing serialization revision advances). Native journals, opening and physical stock approval remain0. The task database is removed.

Rehearsal artifacts are retained in `evidence/c5/stage11-before` and `evidence/c5/stage11-after`. They are not the final frozen-source C5 certificate. The reusable harness and declared synthetic register are in `scripts/testing/mz2_business_uat`; after this source commit, execute and record the clean-HEAD acceptance run. Stage16 acceptance proves its intended lock; actual opening/activation/physical-stock approval remain HELD and are not claimed.

Production financial writes=0. Merge/Deploy/Opening Post/Activation=NO. Write-control unchanged.

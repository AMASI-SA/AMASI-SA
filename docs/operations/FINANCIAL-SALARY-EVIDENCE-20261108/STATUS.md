# Financial salary evidence continuation

Base: de76aed339be775e2878071c92063d0ab9df06df, production branch fetched after published PR1820. Isolated branch: codex/financial-salary-evidence-20261108. No /app changes, release lease or financial writes in this increment.

Live prior read-only preview: first day Nov1 unapproved, 16 missing employee-days / 1456.66SAR, incomplete employee source and recorded-day conflicts block approval. Existing daily rows cover Nov2–8. Opening overlap still requires review. No live backfill authorized by this preview alone.

Synthetic reproduction with unchanged contracts and a moved cutoff produces 40 one-cent daily differences, matching the pattern observed in the live preview but not proving every live row's cause. Root mechanism: cumulative rounding begins at the selected cutoff. Existing financial rows must not be rewritten or accepted using a cent tolerance.

This increment adds exact review evidence (recorded/proposed currencies and amounts, day, source reference). A conflict is explained as cutoff rounding only when the entire recorded candidate equals the candidate under the existing cutoff with unchanged contract inputs. The same conflict still blocks approval. The UI renders unavailable evidence as unavailable, never zero; older responses remain compatible. No calculation, policy approval, opening, payment or courier behavior changes.

Verification: backend regression failed with missing reason before implementation, then all47 salary-start tests passed. Frontend regression failed because exact conflict evidence was absent, then all30 SalaryStart tests passed. git diff --check passed. Production integration, CI and V5 release not performed for this increment. Prior PR1820 publication/verify3 remains complete; do not repeat it.

Next: review exact-head checks and expose this evidence through a governed integrated release; match live conflicts and incomplete employee source, review opening coverage before salary approval. Continue canonical source gaps, Aramex live proof, provider coverage, profit and print/export acceptance. Do not mark all financial tables complete.

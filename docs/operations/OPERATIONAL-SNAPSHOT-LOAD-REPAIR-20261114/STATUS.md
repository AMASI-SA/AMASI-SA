# Snapshot-load repair — intent-only B verified

Updated 2026-11-14T03:05:56.499Z. Documentation-only continuation branch; never merge or publish.

PR [#1916](https://github.com/AMASI-SA/AMASI-SA/pull/1916) has frozen source A `ff9f8b69b980acfda8dffd210feaf72e17e6727a` and exact-artifact intent-only B `bd9607ba2b21c4d4e3e2bc6bced7680bc9c6f287` (tree `c5bd7fb6c357e17c17104282d1d175676e95ed3e`). B has one parent A and changes only `release/release-intent-v5.json`; Git blob `63e053f7ffc32f4f77b416048816ecf42e67499e` matches the unmodified candidate artifact. Official tracked loader and explicit Git verifier passed with actual exit 0, exact A/J/B/R and frontend tree, in 3.869 seconds. Repository remains clean. An earlier wrapper had already completed the validation calls but exited1 on a summary-only wrong JSON key; that real result is retained, and the corrected summary-only rerun passed without any repository/source/intent change.

All 11 A workflows passed (21 successful checks, six expected skips). Exact A official JUnit proof includes all 43 new cases in each Operational/Stock suite. Local and benchmark evidence is preserved under evidence/. B CI is running; both actual Node20/24 adapter jobs must succeed. Current production remains J `4860548f0b032e8e3beca18a4776413cd7e0368b`; no PR1916 merge or deployment yet.

Original operator UI read-only check shows one unsaved buffer, Untitled-1, with no tracked-file overlap, and120 pending changes. No buffer was saved, closed or discarded. The later disk preservation manifest does not represent unsaved editor buffers.

Next: finish B CI, normal merge with parents J/B and identical B/M tree, merge CI, fresh guard and actual original build preserving existing work, owned prepare/final prepublish, one Publish, explicit newer completion, three official probes/normal lease closure, then fresh visible financial as_of acceptance. The previous published release still has old balances; release mechanics alone are insufficient.

See STATUS.json and evidence/ for full source/test/artifact identities and exact next-action constraints.

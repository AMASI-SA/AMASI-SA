# Snapshot-load repair — merged; production deployment pending

Updated 2026-11-14T03:10:36.593Z. This branch is documentation-only; never merge or deploy it.

PR [#1916](https://github.com/AMASI-SA/AMASI-SA/pull/1916) was normally merged as M `9c71435f4a19977c09f4331dee8fddbfa12a45eb`. Root independently verified ordered parents J `4860548f0b032e8e3beca18a4776413cd7e0368b` and B `bd9607ba2b21c4d4e3e2bc6bced7680bc9c6f287`, with M/B tree `c5bd7fb6c357e17c17104282d1d175676e95ed3e`. Source A remains `ff9f8b69b980acfda8dffd210feaf72e17e6727a`, runtime release `rg5-6e9b6b1216ad24c83d92f78483511cb6d6b5539a29bae69614dd088063b8a5db`.

All29 exact-B checks finished:23 success and6 expected skips. Both actual Node20/24 clean-clone adapter rehearsals passed their adapter, artifact/proof/identity/package and runtime JSON stages. All A source and real-Mongo evidence is retained in this directory. The candidate and tracked-B loaders passed with actual exit0. See STATUS.json for job IDs and evidence digests.

Exact-M push CI is pending. The original operator has not been mutated for this release yet. The live site still runs PR1915 and its financial snapshot remains old. Deployment and live acceptance are not complete.

The two reviewed PR1916 operator helpers are preserved under evidence/. Both independently reviewed helpers bind A/B/J/tree/R, require M explicitly, preserve actual process exit before summary assertions, refuse mismatched/foreign lease ownership, and do not add a short timeout to actual build or official verification. The build helper preserves120 preexisting untracked files and refuses unexpected tracked/index changes. UI inspection separately confirmed unsaved Untitled-1 with no tracked-file overlap; no buffer was saved, closed or discarded.

Next: exact-M CI; fresh guard inactive; actual /app/frontend yarn build and preservation proof; own prepare and final prepublish immediately before one Publish; explicit new platform completion; official3 probes and normal lease closure; then visibly open financial refresh that must advance as_of without a warning. Source changes are frozen and no further modifications belong in A or B.

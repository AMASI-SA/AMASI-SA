# Exact B operational and stock regression evidence

Verified at 2026-11-14 15:05 UTC. Verdict: PASS for both requested official regression gates.

## Immutable target

- Repository: AMASI-SA/AMASI-SA; PR 1920.
- Source A: `2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb`.
- Intent commit B, tested head: `dcb97791d6ef8ad394575542eb4048c6c867bbba`.
- B tree: `14dfe135d6633f72c92cb1cb359f4b9933ac176a`.
- Read-only local diff A → B changes only `release/release-intent-v5.json`.
- B and its tree matched local Git, Operational source artifact files, worker/browser source evidence, and the Stock job's explicit `git rev-parse` output.
- Both workflow APIs report the exact B head, completed, success.

## Official results

| Check | Official run / job | Result |
| --- | --- | --- |
| Operational Balance Backend | [43607551281](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43607551281) / 132485374605 | 4,672 passed; XML 0 failures, 0 errors, 0 skipped; log runtime 259.27 seconds |
| Shipping summary and calculator contracts | Same backend job | 16 passed + 18 subtests passed |
| Stock eligibility, reservation and consumption | [43607551295](https://github.com/AMASI-SA/AMASI-SA/actions/runs/43607551295) / 132485374796 | 4,481 passed + 7 subtests passed; XML 0 failures, 0 errors, 0 skipped; log runtime 356.39 seconds |
| Operational frontend | 43607551281 / 132485374548 | 733 passed / 51 suites; 0 failed, pending, or todo tests |
| Canonical worker process | 43607551281 / 132485374378 | 4 passed; XML 0 failures, errors, or skips |
| Operational browser/mobile/visibility acceptance | 43607551281 / 132485374665 | 9/9 PASS; errors array empty; synthetic_only=true |

Operational and Stock case counts overlap; they are not a count of distinct tests across gates. Stock XML's tests attribute is 4,488 because the log also reports 7 passing subtests; there are 4,481 testcase elements.

## Coverage and environment boundary

Both official jobs created isolated, loopback-only Mongo 8.0.12 replica fixtures; Stock additionally created an isolated standalone fixture. The declared no-skip acceptance steps succeeded. Independent XML inspection found zero skipped, failing, or error cases. All modules explicitly selected by each gate produced cases: 185 Operational modules and 179 Stock modules, with no missing module. These correspond to 187 and 182 distinct XML class groups, respectively.

These are operational/stock regression gates with real Mongo available for their integration cases. This does not mean every case individually uses Mongo. Neither XML contains campaign_hover cases, and neither workflow invocation collects the new campaign hover profit or operating suites. Campaign hover acceptance separately exercises those financial suites using their committed mock fixtures with the real shared calculators. No claim is made that the new financial suites ran against real Mongo in these two gates.

## Artifact integrity

All five downloaded ZIP byte digests match the corresponding GitHub artifact metadata.

| Artifact | ID | SHA256 |
| --- | --- | --- |
| Operational backend | 14119122235 | e034d55e3451caf25ae72f8cbc049f1b9d500315d10c3758f472cb54c859a228 |
| Operational worker | 14119066682 | a2e4e0f99c4e493cb169b9e713492acb1e7938e282112614d5766cae4f44d978 |
| Operational frontend | 14118787292 | 25374046ccaf02755a8f8fb4c3101c1fa1ed24b4d5cbc0eef1c9a803e98c1b2d |
| Operational browser | 14118692672 | 711801b120810b81eeab6a5e355770882f4a4cfca458a6adbe4692f7d9f61e6d |
| Stock backend | 14118552535 | 469a7e6ff07c0a8a520b7d882c05314bff95ed8e142467bafcd59d8b54f05a93 |

## Retained evidence

- `run-metadata.json`: final official run/job/artifact metadata, source and tree, independent report counts and coverage boundary.
- `download-summary.json`: matched digest and retained ZIP filename for each artifact.
- `operational-backend.xml`, `eligibility-backend.xml`, and corresponding `*-summary.json`: independent case counts and declared module coverage.
- `operational-backend-log-evidence.txt`, `eligibility-backend-log-evidence.txt`: targeted sanitized source, fixture, pytest command, and result excerpts.
- `operational-source-sha.txt`, `operational-source-tree.txt`, `operational-worker-source.txt`, `git-source.txt`: exact source evidence.
- Frontend JSON, worker XML, browser evidence JSON, source manifest and original ZIP files retained alongside this readout.
- `source-workflows/`: exact B workflow definitions used for coverage checks.

Only read-only GitHub and local Git/file inspection was performed. Evidence files were written outside the repository. No source edits, Git changes, GitHub mutations, reruns, waivers, browser or /app interaction, lease changes, merge, or deployment was performed. This report is CI evidence, not a release or production verification.

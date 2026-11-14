# Campaign operating pilot exclusion — PR1929 merged; not yet published

Updated 2026-11-14 19:19:48 UTC.

## Current verified identities

- Repository: AMASI-SA/AMASI-SA; production branch hotfix/prod-snap-meta-final.
- Source A6: `fcb827f556c40bebd843d8c9d85fedc0b60cf8fa`; base J6: `3e9dc3b25e41c663e001822a666323c802e5ec5a`.
- Intent-only B6: `93efe593ebe2c401763c5d8faa4ba8547a8a8016`; intent blob `84ab82046397eaac1e2e7b76adf6418e56d3bd4e`.
- Normal merge M6: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`; tree `c2d683827c54574fcd60ee4102c9de7c163f5ef8` equals B6.
- PR https://github.com/AMASI-SA/AMASI-SA/pull/1929 merged19:17:59UTC.
- Ordered parents [J6,B6]; A6→M6 changes only release/release-intent-v5.json. Root and independent checks passed.
- Runtime `rg5-4a98965dc9ad5922b4dbd25e5c960360af8b09f8e5590cced0b87c8c8a0f07a2`.

## Behavior

An explicit disabled pilot payroll contract with empty normalized salary history was wrongly blocking the campaign operating-expense basis. The canonical executive salary reader excludes this non-contributor. Project its existing policy fields and exclude only that precise shape after normalization. Valid history/legacy baseline still contributes; genuine contributing missing costs stay incomplete. Five regression cases. No formula, frontend, currency, provider, sync or business-data change in this final follow-up.

## Acceptance completed

All11 source-A6 workflows passed. Stock attempt1 failed only the unchanged measured CPU-ratio assertion after functional equality passed; one unchanged diagnostic retry passed4513 tests/7subtests and no skips/failures/errors. The timing variation remains unexplained; original failedjob/artifact and exactmetrics are preserved. ExactA6 Operational Balance independently passed4799 including the same benchmark.

All11 B6 workflows passed attempt1, plus declaredTikTokskip. Coverage253backend/50frontend; OperationalBalance4799,16shipping/18subtests,783frontend/52suites,14browser scenarios; Stock4513/7subtests with zero skips/failures/errors. Both readytransition Snapchat runs43631264285/43631264327 passed before merge.

Both actualHost20/24 adapter packages and JSON-route jobs passed. Root and independent reconstruction validated293files/3directories,289frontend records, exactidentity/meta/proof, backend1217/frontend1472 isolated package records, all bytes/modes and package digests. Full evidence in RELEASE_CHECKPOINT.json and verification/.

## Outstanding exact-M6 checks

Push Readiness43631333866, Security43631333754, CodeQL43631333690 are running. Do not substitute historical results for their actual exits.

## Original release coordination

ForeignPR1928 cloudpublication explicitly completed (Nov14,10:08PM, platformversion3a22e2d) and its official verifier started once. Latestownerrecord is ffbe30bac4bf33b3dfe34598c5d47f9f7d9979b6; closure/preservation not yet observed. Original /app and activelease were not changed by this task. Root/independent inspection of the actual verify/preservation code proved no remote-ref comparison after publication, permitting this GitHub-only merge. Originalwrites still require foreignverifiedclosure/preservation and freshofficialinactive.

The original-build operator at operators/original-build-1929.py is pinned to actualA6/B6/M6,15488bytes,SHA25601263d94478e74075a5833fe9700d263c9e3a56263bedb9c563945a46d519e69. Root and independent fullstatic/AST reviews passed. It has not been executed. The release-gate template remains disabled because the actual original evidence directory does not yet exist.

## Next safe sequence

Finish exactM6 pushchecks and foreignverification/preservation. Read official status fresh; only inactive/noincompletelease permits originalwork. Confirm originalHEADJ6/trackedclean, stage the frozen operator from this handoff and verifyitsSHA. Run the one-shot originalbuild for exactM6, inspect actualbuild/packageproof/identity/preservation receipts, then pin/review/freeze finalguard with actualevidencedir and currentUIbaseline. Only then officialprepare/prepublish, one authorizedfreeUIpublish, explicitnewUIcompletion, officialverifyonce3checks, inactive/preservation, livecampaign arithmetic andcroppedtooltip evidence.

GitHubproductionbranch changed: yes, normalmergeM6. Ownlivepublication/originalbuild/lease: no. PR1926/1927 are closed/superseded; neverresumeoldoperators. PR1924 was verifiedclosed; PR1923 oldJ5release closedunpublished. PR1930 hasexplicitownerhold and isunrelated. Issue1006 isfull; thisbranch/taskSTATUS/PR aredurablecontinuation. No private identifiers orfinancialvalues arearchived.

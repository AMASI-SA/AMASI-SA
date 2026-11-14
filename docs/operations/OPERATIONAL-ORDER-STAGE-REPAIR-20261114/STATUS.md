# Operational order-stage repair — measured WIP

Production base is the verified PR1917 merge. Financial refresh still times out inside source_orders; this task continues the authorized fix/merge/publish goal. No new financial attempt will run before a reviewed source change and governed release.


## preserve measured source_orders diagnosis and isolated repair ownership — 2026-11-14T05:36:21.134Z

PR1917's one visibleattempt preservedoldasof afterHTTP408. Fixedstage source_orders cancelledafter61.803392seconds. Allsource/CI/originalbuild/officialdeploymentverification for1917passed; itsfinancialacceptancedidnot.

Read-only syntheticmeasurements prove quadratic growth in four loopswithinthisstage: driverassignmentmatching, courierprior-historyholds, costcatalogreindexing, andgrowingdiagnosticlistmembership. Doublingrows+lookups64→128 causes4×visits fordriver,hold,cost. issue/completion_issue20kuniquediags each require199,990,000dictcomparisons,~9.45/9.77CPU seconds. These are synthetic observations, not attributed production distributions.

Root created cleanisolatedworktree atJ2db0edc9edb315026eee4b6edd43b420a569c37d and assigneddisjointimplementationpaths. Sourcebranch hasnochangesyet. Defaultlegacycallers, malformedinput/errorordering, allfullhistory andvisibility/cancellationmustremain. Bankcatalogfailure-dependent rereads are sourcepossiblebutunobserved; noDBreadchange iscurrentlyplanned.


## preserve integrated order-stage source before final verification — 2026-11-14T05:57:15.489Z

Saved current WIP source copies, including request-local assignment/cost/hold/diagnostic indexes and source_order_indexes/source_bank_catalog fixed timings. These are not a frozen source A or deployment. Focused parity and growth tests are being completed; final manifests will supersede this snapshot. The production ref remains the verified PR1917 merge, whose first financial attempt timed out. Original /app and its 120 pending files have not been touched by this follow-up.


## freeze reviewed source A and preserve integrated verification — 2026-11-14T06:06:51.089Z

PR1918 freezes source A d70b42cda3d70a7f07c78c3efc5fde8331fdc877, tree 38b0dec1fc53cb9bd1ba36236bcc730f0e1d0367, direct parent J 2db0edc9edb315026eee4b6edd43b420a569c37d. All15source paths are saved in the source branch; final changed source copies supersede the earlier WIP snapshot. Local combined validation passed1467 cases with actualexit0 and identical source hashes throughout. Four Mongo cases remain explicitly deferred to exact-A CI. Independent integrated, diagnostic, assignment and hold reviews have no remaining blocker. The expected CI list requires449named cases across the fournew suites, fouradded timing cases and fourdeferred Mongo cases. Candidate review and release gates are pending; no originaloperator mutation or financial retry in this follow-up.


## freeze reviewed intent B after complete A gates — 2026-11-14T06:15:21.852Z

All11exact-A workflows passed. Officialbackend XMLs: Operational4647, Stock4456+7subtests; all449required cases and17timings and4Mongo cases pass inboth. Candidate artifact14102091387 fromrun43564579813 matchespublished ZIPdigest; officialcandidate loader exit0; independentfullmanifest rederivation passes. B9e6746b5ea540ff525b0a4302da88c2e8eb1e0b9 directchildA changesonlyintentblob542c547cb679e78d5050aa537bed370595f353f0; Btree9de141756a4c5dcfa8d1d4dfb6513178c406ac8e, runtimerg5-e44d7833a828fd8cfcff3252b5bad60e6526cb80c98ca9464d8205e39d02800f. TrackedB loader exit0. PR1918 markedready; exactB workflows includingactualHost20/24adapterrehearsals arepending. Originalread-onlypreflight foundHEADJ, trackedclean,120pending,inactiveguard,noGitHubmarkers andseparatelyobservedUntitled-1. Originalhasnotbeenadvanced,built,leasedorpublished for1918. Reviewedverificationhelper andpendingreviewrehearsalhelper savedbeforeexecution.


## preserve independent operator rehearsal review — 2026-11-14T06:17:14.968Z

Both original-operator helpers are now independently reviewed with exact identities and source hashes. The rehearsal retains the120file preservation contract, rejectsGitHubmarkers withoutremoval, requiresinactiveguard beforeFF/build and savesactualyarnbuildexit beforepostconditions. It createsno lease orpublish. ExactBchecks remaininprogress; nofailureobserved butpendingchecks/temporaryCodeQLneutral are notaccepted asPASS.

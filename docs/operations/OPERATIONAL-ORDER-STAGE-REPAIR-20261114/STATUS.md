# Operational order-stage repair — measured WIP

Production base is the verified PR1917 merge. Financial refresh still times out inside source_orders; this task continues the authorized fix/merge/publish goal. No new financial attempt will run before a reviewed source change and governed release.


## preserve measured source_orders diagnosis and isolated repair ownership — 2026-11-14T05:36:21.134Z

PR1917's one visibleattempt preservedoldasof afterHTTP408. Fixedstage source_orders cancelledafter61.803392seconds. Allsource/CI/originalbuild/officialdeploymentverification for1917passed; itsfinancialacceptancedidnot.

Read-only syntheticmeasurements prove quadratic growth in four loopswithinthisstage: driverassignmentmatching, courierprior-historyholds, costcatalogreindexing, andgrowingdiagnosticlistmembership. Doublingrows+lookups64→128 causes4×visits fordriver,hold,cost. issue/completion_issue20kuniquediags each require199,990,000dictcomparisons,~9.45/9.77CPU seconds. These are synthetic observations, not attributed production distributions.

Root created cleanisolatedworktree atJ2db0edc9edb315026eee4b6edd43b420a569c37d and assigneddisjointimplementationpaths. Sourcebranch hasnochangesyet. Defaultlegacycallers, malformedinput/errorordering, allfullhistory andvisibility/cancellationmustremain. Bankcatalogfailure-dependent rereads are sourcepossiblebutunobserved; noDBreadchange iscurrentlyplanned.


## preserve integrated order-stage source before final verification — 2026-11-14T05:57:15.489Z

Saved current WIP source copies, including request-local assignment/cost/hold/diagnostic indexes and source_order_indexes/source_bank_catalog fixed timings. These are not a frozen source A or deployment. Focused parity and growth tests are being completed; final manifests will supersede this snapshot. The production ref remains the verified PR1917 merge, whose first financial attempt timed out. Original /app and its 120 pending files have not been touched by this follow-up.

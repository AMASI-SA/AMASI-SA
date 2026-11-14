# Operational order-stage repair — deployed and live acceptance passed

PR1918 is merged and published as platform 279b7a3. Official production verification passed with three probes, 582 frontend requests, exact A/M/R identity, and a fresh inactive lease guard. The first visible financial refresh after verification returned HTTP 200 and advanced report as_of to 14 November 2026 at 09:57 Asia/Riyadh. Report loading, table pagination, and pending-order page/search/clear/close checks passed. Existing source-data completeness warnings remain and are not claimed resolved.

The sections below preserve the chronological investigation and release evidence; the latest completion evidence is at the end.


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


## preserve normal merge and original build handoff — 2026-11-14T06:23:09.791Z

PR1918 is merged as M23cc06da003c18c0f3fc6c2e749aad1326666166. GitAPI proves normalparents [J2db0edc9edb315026eee4b6edd43b420a569c37d,B9e6746b5ea540ff525b0a4302da88c2e8eb1e0b9] and tree9de141756a4c5dcfa8d1d4dfb6513178c406ac8e, exactlyBtree. FreshpremergeB metadata showed14completedworkflows:13success and1expectedTikTokskip;35checks25success10expectedskips, nofailure/pending/neutral. Productionref wasJ andPRheadB immediatelybeforemerge. Originalpreflight remainsHEADJ, trackedclean120pending,inactiveguard; nineaddedpaths havezero untrackedcollisions. Rootwillrunreviewed originalrehearsal script with exactM; nolease/publish yet.


## preserve original governed build and reviewed guard helper — 2026-11-14T06:26:17.896Z

Original/app advancednormally toM23cc06da003c18c0f3fc6c2e749aad1326666166; actual/app/frontend yarnbuild ran06:23:34.369860–06:24:46.289322UTC,71.919475seconds,actualexit0. Runtimeidentity equalsfullreviewedintent. Before/after120pendingfiles arebyte-identical (actualchangedcount0), tracked/indexclean; freshguardinactive, noleasecreated. Evidence/tmp/mezan-pr1918-original-6jyavqt4. Independentreviewapprovedprepare/prepublishhelper with exactA/J/B/M/R/tree/actor andsameprivateevidence directory. ExactMpushchecks stillpending; do notprepare/publishbeforecompletion. ExactBproofwithbothactualhostrehearsals preserved.


## preserve passed M gate and owned release lease — 2026-11-14T06:32:04.349Z

ExactM all3pushworkflows and11checks passed, includingactualHost20/24adapterrehearsals. Originalbuildpassed andpreserved120files. Officialprepare actualexit0 at06:31:18.048582–06:31:21.012193UTC; freshownedactivelease actorcodex-operational-order-stage-rootfix-1918, Ad70b42cda3d70a7f07c78c3efc5fde8331fdc877, M23cc06da003c18c0f3fc6c2e749aad1326666166, Rrg5-e44d7833a828fd8cfcff3252b5bad60e6526cb80c98ca9464d8205e39d02800f. Evidence/tmp/mezan-pr1918-original-6jyavqt4. NoUIpublish hasoccurred for1918 yet. Nextaction mustfreshprepublish thenoneauthorizedpublish, withleaseownership retained untilofficialverification. Do notclear/abortotherleases orrerunexistingstageevidence.


## preserve authorized publish start after fresh prepublish — 2026-11-14T06:35:47.444Z

Officialprepublish actualexit0 at06:32:35.194956–06:32:38.756641UTC, ready=true andfreshownedactivelease. UIconfirmed mezansalla.com andFree republishes. Rootclicked Publishchanges exactlyonce at06:33:51.345Z. UIshowsPublishing andexplicitDeploymentStarted Nov14,09:33AM; oldplatform120e0fa remainsbaseline. No completion orfinancialsuccessclaimed. KeepOverview open; do notopenruntimeLogs beforefirstpostverificationfinancialattempt toavoidcachedlogwindow. Ownedlease actorcodex-operational-order-stage-rootfix-1918, evidence/tmp/mezan-pr1918-original-6jyavqt4.


## preserve new platform completion and exact live identity — 2026-11-14T06:46:42.313Z

ExplicitDeploymentfinished Nov14,09:44AM observed06:44:36.254Z withnewplatform279b7a3c-11fa-4f20-a6fe-7bad268127b2, prior120e0fa. Rootstarted reviewedofficialverify once viaoriginalTerminal4; prefix/tmp/mezan-pr1918-original-6jyavqt4/28-verify. DOMconfirmedstarted,nostopped/verifiedyet. Do notinterruptorretryduringquietverification; priorfullverification took~12minutes. Publichealth at06:46:23.538Z hasexactAd70b42cda3d70a7f07c78c3efc5fde8331fdc877,Rrg5-e44d7833a828fd8cfcff3252b5bad60e6526cb80c98ca9464d8205e39d02800f,J2db0edc9edb315026eee4b6edd43b420a569c37d andallidentity/backend/control/critical/frontend booleans true; boot06:43:26.598544UTC. Official3probes/frontend checks/freshinactive stillpending. Browseroldhealth66/financial67 werecleaned; newhealth68. Onlyoperator46/publisher59/health68 remain. Financialattempt1918hasnotstarted; createonenewvisiblefinancialtab afterofficialsuccess (mountauto-refresh). Publisher staysOverview; do notopenLogs early.


## Official production verification passed — 2026-11-14T06:57:36.522Z

Original official verify ran 2026-11-14T06:44:36.933528Z to 06:56:21.765981Z (704.832459 seconds), actual exit 0. It verified three production probes and 582 frontend requests against exact source A, merge M, and runtime R, then returned a fresh inactive guard. One authorized visible financial refresh remains; no live acceptance claim has been made.


## First live financial refresh and local controls passed — 2026-11-14T07:03:42.061Z

After official verification closed the owned lease, one visible page mount started at 06:57:54.483Z. The report displayed fresh as_of 14/11/2026, 09:57 Asia/Riyadh, all three sections, no loading, no main error, and no stale fallback. Runtime logs match this request: HTTP 200, 62,848.657 ms; source_orders complete 5,755.266 ms; source_collection complete 21,423.146 ms; reconciliation complete 23,095.890 ms; profit_snapshot complete 9,014.404 ms; snapshot_commit complete 152.634 ms. Table next/previous and pending-order open/page/no-match-search/clear/close passed with unchanged fresh as_of. No financial confirmation, settlement, external provider action, or background refresh was performed. Existing data-completeness warnings remain; individual accounting amounts were not certified. Private screenshot is retained locally and not published to GitHub.

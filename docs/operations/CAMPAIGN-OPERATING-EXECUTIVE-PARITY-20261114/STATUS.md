# Campaign operating allocation: executive parity

Task: CAMPAIGN-OPERATING-EXECUTIVE-PARITY-20261114

## Scope and observed gap

Follow-up to PR #1920. The published card exposes the requested direct costs, operating allocation and net profit rows. Live read-only acceptance found operating allocation/net unknown while the executive operating row remained numeric. No customer data or campaign figures are retained here. The live cause has not been attributed conclusively; a narrow lookup in the original workspace database did not identify the campaign owner, so it did not invoke a financial calculation.

Source review proved two mismatches affecting valid cohorts: the campaign reader rejected recurring obligations whose shared executive calculator legitimately contributes zero; payroll validation considered contracts before filtering excluded employees and noncontributing days. A redundant outer deadline also erased the operating reader's structured timeout reason.

## Candidate changes

- Reuse the shared recurring daily contribution, including valid zero, for the executive expense numerator. Negative, nonfinite or unreadable results remain unavailable.
- Apply owner-scoped employee identity and day eligibility before validating cost; retain original contract amounts, shared payroll/calendar methods, duplicate rejection, read caps and deadlines.
- Let the operating reader's existing three-second timeout return its structured result; direct cost/settings deadlines stay unchanged.
- Preserve full expense/sales ratio, monetary rounding, negative profit behavior, and direct-cost completeness requirements. No provider calls, writes, allocations, scheduling, or refresh jobs are added.

## Verification state

Base is reviewed production M `29ec1e07eb7d1a23647cadff5bac26149accfa99`; its tracked release intent is unchanged in this source candidate. Root static review passed; independent review and repository CI remain pending. The isolated actual-function payroll probe passed 42 cases (the original failed 31); this is not pytest or a Mongo integration run. New repository tests cover canonical zero-contribution parity and timeout propagation as well as payroll scopes. Actual repository suite outcomes must be recorded before readiness.

The candidate has not been merged, built in original /app, prepared or published. The preceding release's already-started official verification must finish and the guard must be inactive before changing original /app or starting its next release rehearsal. A2/B2 must retain ordinary ancestry; B2 changes only the CI-produced reviewed intent. Do not reuse the old lease or runtime identity.


## Final source and review checkpoint — 2026-11-14 16:28:20 UTC

- Final source candidate A2 `0f5bca7427fed7851008d0f7d64e23414746bdc1`, tree `01ef389fd3b167e602fe8a889210641d7b148d4c`, on `codex/campaign-operating-executive-parity-20261114`; draft PR#1922. Two ordinary source commits fromJ2, no intent-history edits.
- Independent review found and corrected the initial candidate's invalid utility estimate fallback. Only a zero daily contribution inspects an explicit configured estimate; positive actual/historical invoices remain authoritative. Final R2 source/patch hashes matched and root/independent static reviews passed.
- Prior source `22832941d5d61d77ed5c6acb2b3a6565423ce675` source-coverage/readiness passed; those results do not certify the final A2. All required tests/CI must run on `0f5bca7427fed7851008d0f7d64e23414746bdc1` before freezing B2.
- Preceding PR1920 official production verification succeeded three probes at16:22:18.751950UTC, fresh guardinactive16:23:12.874031UTC. Its publication/verification must not be repeated.
- No follow-up original build, lease, merge or publication yet. Next: exact-sourceCI and its reviewed-intent artifact; then intent-onlyB2 with fullCI/hostrehearsal evidence.


## Exact source passed; immutable intent frozen — 2026-11-14 16:35:12 UTC

- All11 applicable exact-A2 workflows passed. Source coverage actual logs:248 backend cases (2 warnings,4.27s),50 frontend tests/9 suites (1.978s). Root and independent R2 static review passed.
- B2 `afe6ff699e0de1601b37d2fd81e4291cb69dc75f`, tree `b7e5e6dc359cea6d2ab654fde3a67d8580f963fe`, is the sole intent-only child of A2. Independent GitHub lineage/diff review passed. The source remains A2 `0f5bca7427fed7851008d0f7d64e23414746bdc1`.
- Candidate artifact14121569773 from successful readiness43615534811 was downloaded and exact bytes checked:ZIP73fcba744bb0d5c87ad800d5d7cfd6c355cca56d349b82c5d22c3f0a1ea76cf6; JSONfeab3c376faf8154c9e74a4df88315ab4321aa0cc4f7b36b32dcabd02c4d68ad; Gitblob2eb12801b61c5c62a97c68d278555116bf12b23c. Upload returned the same Gitblob. Source/base, changed backend hashes, reviewed origin/toolchain and runtime/component bindings were validated.
- New deterministic runtime `rg5-fbb9058e74d33e0a4f8d9f1fe58f80be54f02fea3721efab400d41e876ec75b4`. Exact-B2 CI and both host rehearsal artifacts remain pending.
- Disabled new build/gate templates archived here for review. They preserve119 records exactly plus the proven single append-only bookkeeping file, and retain actual official receipts independently of postchecks. They containUNPINNED values andPINS_REVIEWED=False; do not execute these templates.


## Production advanced; fresh candidate on preserved upstream — 2026-11-14 16:44:27 UTC

- PR1921 merged current production J3 `94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894`; original /app independently observed at J3, tracked clean, guard inactive at16:41:25.665266UTC. PR1921's separate operator is running its original build (checkpoint19d6fdb6ea7dcbd488ad73de177ec5becab7a723). Do not interfere with that workspace/release or clear its lease.
- PR1922 B2 completed all11 applicable workflows and bothhostrehearsals. Root and independent archive validation passed:293 generatedfiles byte-identical, exactintent andruntimeidentity links. B2's base is superseded; it was never merged,builtoriginally,prepared orpublished. ClosedPR1922 in favor ofPR1924; frozenbranch and artifacts preserved.
- New sourceA3 `c484b549e8bca017627ce80749c7895198f04bf8`, tree `1afab9057e574c8db551f98411febaeaced5feb4`, ordinaryparentJ3 on `codex/campaign-operating-executive-parity-m94f6-20261114`, draftPR1924. Exactlythree reviewedbackend/testblobs retained plus newtaskSTATUS; allupstreamfinancial-centerchanges retained and inheritedJ3intent unchanged. Exactcompare confirmedfourfiles/onecommit.
- NewA3CI and newintent/hostproofs required; oldB2proofs are not permission to deploy againstJ3. Disabled1922runners must not be used fornewlineage. No follow-up publication orlease.


## New A3 candidate artifact and disabled release templates — 2026-11-14 16:47:06 UTC

- Source reuse independent review PASS: upstream27-file diff has no campaign-cost or shared expense/sales dependency changes. Exactthreebackend/testblobshashes match reviewedR2.
- ExactA3sourcecoverage:248backend/50frontendtests in9suites passed; readiness43617140686 passed. Newcandidate14122553318 exactZIP/JSON/Gitblob and root source/base/component bindings verified. RemainingA3gates pending; noB3frozen.
- PR1924 disabled templates archived; only source/base/PRnaming adapted from reviewed1922logic. AllB3/M3/artifact/evidence/UIpins remainUNPINNED, PINS_REVIEWED=False. Noexecution.


## Exact A3 passed and new B3 frozen — 2026-11-14 16:51:58 UTC

- All11 exactA3workflows completedSUCCESS, confirmedbyroot andindependentmonitor. Relevantactuallogs:248backend,50frontend/9suites. IndependentA3candidate reviewPASS includingfullsourceprojection/runtimehashderivation andPR1921frontendpreservation.
- Newintent-onlyB3 `0a93847af8eb3898ae195c58ad88092388d908ad`, tree `b48a0eefb368ba655c8370f6b2e4435e07612790`, soleparentA3. Root exactcompare shows onlyrelease/release-intent-v5.json. Uploadedblob matches `2159398256b11323a9cce8e09f89508bb6f18818`. Runtime `rg5-c669a417474960a96386ef07047109a8e8cf1bef187873008b650bf00ac23e33`.
- ExactB3CI/hostrehearsals pending. No M3/ownoriginalbuild/ownlease/ownpublication.
- Root readPR1921ledger9bf95138f4158e8b1e54b326ec802f30edb6f10b: its actor owns the activelease, prepare/prepublish succeeded, oneUIpublicationclicked16:46:05.772UTC andstillrunning atcheckpoint. Preserveitsworkflow; neverclearorreuseitslease. Originalworkspaceoperations andmerge aredeferreduntilotherreleaseverified/inactive andfreshchecksagree.


## B3 host proofs passed; independent benchmark failure retained — 2026-11-14 16:58:53 UTC

- Both B3 host rehearsals succeeded. Root and independent archive review PASS:293 files byte/mode-identical;289 frontend content hashes; exactnewintent/runtime/meta/proof links. Runtime identity fileSHA `d71fc8e889e8d35f09d4b5ec090b5e850422c43be5545444cdc54da3ad910313`.
- OperationalBalanceWeb run43617869606 backendjob132516789691 failed one CPU-ratio benchmark, 4759passed. ActualoldCPU0.1652199959999976/newCPU0.2488520640000047, thresholdnew<old*0.65. Diagnostic output equality passed. Failedartifact14123355672 ZIP024a8998613ec2c5832e1a4b5af27569f260ddd9de94a777bb86c10c4942bef0 retained, exactB3/treeverified.
- Root/independent source review: same measuredcode aspassingA3; nointentdependency or indexedfallback defect found. Benchmark measures two single samples withoutwarmup/repetition/GCcontrol. Causeunproven; do notcallharmless schedulingflake. Exactlyone unchangedfailed-jobrerun isallowed asdiagnosis, withallgatesstillrequired. No code/testthresholdchanges orretryloop.
- ForeignPR1921 release stillactive inlatestledger. Noownmerge/originalbuild/lease/publication.


## One rerun started; foreign publication now verifying — 2026-11-14 17:00:12 UTC

- Exactlyone failed-jobrerun toolreturned success:true; actualnewbackendjob132518905107 isrunning onunchangedB3. All10 otherapplicableworkflows nowSUCCESS. Originalfailure remainsrecorded; gate notwaived.
- Root readforeignledger44fff464a2fc3b3be93c5d311bf9c1f167fcf9c9: PR1921publicationecb9679completed; ownerlaunchedofficialverifyonce atprefix/tmp/mezan-pr1921-original-zu179mmf/28-verify inTerminal2. Noactualexit/inactiveyet. Do nottouchitslease, process, terminal orrepeatpublish.


## All B3 gates passed; merge awaits foreign lease closure — 2026-11-14 17:04:32 UTC

- The singleunchangedrerun completedSUCCESS. Root readactualbackendjob132518905107:4760passed/8warnings,202.47s. Originalfailedsample remainsinledger; itscause isnotclaimedresolved. No source/test/thresholdchanges.
- All11 applicable exactB3workflows nowSUCCESS, TikTokexpectedbranchskip. Root andindependenthostarchive proofPASS; rootCIstepsconfirm bothhost artifact/proof/packageboundary andgovernedJSONroute testsSUCCESS.
- PR1924 markedready atsamefrozenB3; no mergeperformed. Awaitotherowner'sofficialverify completion andfreshinactive/currentbasebeforeordinaryM3merge.


## Prior owner verified; exact M3 merged and build runner reviewed — 2026-11-14 17:16:14 UTC

- Root readforeignc9572085cbf2d355752e2d231249bb498781f16b: oneofficialverifyactualexit0/3probes, finished17:10:13.373818UTC, foreignleaseclosed; postpreservation120unchanged17:10:44.989006UTC. ItsmanualrefreshUI remainsownedbyotherconversationanduntouched.
- Rootfreshoriginalstatus17:13:16.210034UTC:exit0,inactive,HEADJ3,trackedclean,120untracked. FreshremoteproductionandPRheadalso matchedJ3/B3.
- OrdinaryPR1924mergeM3 `77e4acf33e8f5432d97bda5d7208983f0fadc599` hasorderedparentsJ3/B3 andexactB3tree `b48a0eefb368ba655c8370f6b2e4435e07612790`. RootandindependentlineagechecksPASS; A3ancestor andintent-onlydiff retained. Gitproductionbranchadvanced; livepublicationbythisfollowup hasnotoccurred.
- ExactM3push Readiness43619968433/Security43619968483/CodeQL43619968456 running. Finaloriginalbuildrunner exactSHA `7bef5ecff3a1c2b717c92633d135f94b1d8f44981498f864dbbb1be7405386d9` (15364bytes), independentstaticreviewPASS; noexecution. Frozenhere forimmutableoriginalstaging. Finalgateremainsdisabledpendingactualoriginalevidence/UIbaseline.

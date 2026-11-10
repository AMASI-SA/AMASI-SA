# Qoyod R13 post-deployment checkpoint

Restores invoice-list completion when Qoyod returns its exact invoice-specific empty-tail response, and reduces synchronous operational-balance work that could delay worker heartbeats. Invoice eligibility, payment verification, idempotency, two-hour cadence and manual-only header refresh remain unchanged.

Release:
- Source A: f1978b301c4096a9bafcc2c461355e3b4eda2536
- Intent B: 2e8a5621ebf240dc36e720d6a4d357af26a0a007
- Merge: 86562e2971f56cad1fde1e61f68744467e2d2253
- Runtime: rg5-941480bd764685497296e40d923f44e12a8bb5e6dfdded39dcc92c5ca6114315
- Supersedes closed, unmerged PR1868; do not deploy intermediate intents.

Validation:
- 1292 local operational unit tests passed;8 optional skipped.
- 60 randomized old/new financial equivalence comparisons passed.
- Synthetic10000-order repeat calculation52.473s→3.675s; production stall attribution remains unprofiled.
- Broad Qoyod tests164 passed from backend cwd; focused provider tests97 passed.
- Exact B real-Mongo operational CI run42886554147:3067 tests+7subtests passed.
- Worker/two-hour real-Mongo42886554228 and required applicable workflows passed.
- Isolated final rehearsal and shared materialization exited0.
- Prepare/prepublish exited0 with matching identities.

Production changed:YES. One publish started Nov10 22:54 Riyadh; explicit Deployment finished23:04, platform version0914cfd. Health confirmed exact source/runtime and identity/hash/frontend flags true.
Official three-probe verification is still running in Terminal3, PID61376, /tmp/qoyod1869-verify.log. Do not repeat publish/verify or clear its lease.

Post-deploy functional evidence:
One manual read-only full scan succeeded20:09:52Z:8950 fetched,8831 in scope,119 skipped;9508 eligible,1000 missing,8363 matched,145 amount differences,323 Qoyod-only. These are reconciliation counts, not sends.
Worker last observed529/773 processed,0sent. One owner-authorized guarded manual retry of a pre-existing404 case is in flight; do not duplicate it. Existing404 quarantines may remain blocked by their classification and require guarded recovery.
Preview backend restored RUNNING after temporary materialization stop. No resource/cost changes.
Shared/app now contains unrelated concurrent work; preserve it. MongoViewer tab15 changed concurrently; our temporary filter removed and further use stopped. No credential extraction.

Next: obtain officialverify result and inactive guard, confirm retry outcome, diagnose worker/quarantine recovery, then confirm remaining eligible invoice sends. No all-sent claim.
Issue1006 is comment-limited; task status document and this PR retain continuation evidence.

Manual retry result update: browser returned a120-second proxy read timeout. Outcome UNKNOWN; no repeat attempt authorized by this checkpoint. Inspect saved sender/quarantine state before another attempt. The read-only fullscan succeeded; sender still needs functional confirmation.


20:19Z milestone: official verify completed EXIT0, verified=true with exact A/runtime; guardstatus active=false. Preview restored. Sender UI still529/773,0sent. Manual retry returned proxy120s; no confirmed invoice result. Read-only configured /app/backend/.env database returned no main cycle and does not match production; never mutate it. Previously supplied MEZAN_QOYOD_MONGO is unavailable in this shell. Independently opened Production Database manager tab requires secure connection again. Original MongoViewer tab belongs to concurrent work and remains untouched. Continue with authenticated production evidence or guarded asynchronous sender repair; never repeat uncertain financial attempt blindly.

# Support communication state

The owner explicitly authorized support email on 2026-10-14 and prohibited further paid Emergent project chat. The exact support@emergent.sh recipient was verified against official documentation and authenticated correspondence. Every outgoing reply below was read back with SENT, the expected recipient and the exact new body. Full correspondence remains in Gmail; no raw headers, private thread identifiers, credentials or full messages are copied to Git.

## Confirmed free builder restoration and publication

Written approval was sent at 2026-10-13 22:25:07 UTC for restoration of the original existing-project AMD64 builder and original base images, removal of exactly the two unapproved automatic build additions, preservation of source/data/secrets/live version, and email-only handling.

Support confirmed at 22:42:51 UTC that restoration was applied at 22:39–22:41 UTC at no extra cost. Original host Node 20.19.5/Python 3.11.2/Yarn 1.22.22 and the nine frontend build steps were restored; governed source still uses pinned Node 22.23.2. Support confirmed no queued or running paid agent requests. No support build/deploy occurred.

The owner-authorized third publication subsequently succeeded. Publish 100 version f85edf36 matches PR #1355 source A and deployment M, and three exact public guard probes passed. The matching owned lease closed; PUBLIC-RELEASE-VERIFICATION.json contains sanitized proof.

## Native AI diagnostic

The 2026-10-13 23:32:04 UTC reply reported successful publication, 21 native campaigns, local draft save and Search validate-only evidence, then requested read-only timing/error-code evidence for the AI-draft 504 and concurrent health stall.

Support replied at 2026-10-14 01:11:17 UTC. Retained logs start around 00:43 UTC, so the 23:28 request duration, returned backend status and model-call entry cannot be recovered. The deployed endpoint performs sequential Google context reads with individual 22-second limits and no whole-request deadline; the model timeout covers only its final step. Safe backend google_ai_timeout proves the model started; generic gateway 504 does not. The response body of the observed request is not established. Health stalls at 23:28:40 and 00:45:30 UTC follow the existing ad-sync process pattern; exact phase and causality remain unproven.

Their recommendation is a whole-request deadline plus a sanitized phase/outcome timing line. Those two gaps and legacy process isolation remain absent from frozen PR #1357; no production observability or scheduler setting was changed. Existing source fixes bound concurrency, scope family reads, improve the generation profile and synchronize account scope. Their live outcome is pending publication.

## Editor authentication and handoff

A 2026-10-14 04:21:37 UTC reply reported tested immutable PR #1357 and requested only workspace HEAD/branch/guard state and availability of authenticated entry or a no-cost guarded web-release handoff. The secure Code Server authentication request was cancelled by the user; no password was read/copied/emailed and no authentication retry followed. No access reset, merge, build, cleanup or publication was requested.

Support replied at 04:29:26 UTC: original workspace remains on production branch at M4d4b218a, no tracked changes, platform diagnostics preserved, AMD64 unchanged. Code Server requires normal per-session reauthentication. They provide no support-mediated web-app release handoff. There is no purchase or upgrade needed to reach the editor. Support also reported near-full storage; no cleanup or worktree removal was authorized or performed.

They inferred guard state from a static runtime manifest and an unrelated old lock. At 04:31:58 UTC, a verified email clarified the protocol-v5 lease path /app/.git/mezan-production-release.lock/lease.json and requested the actual status command, reporting only active/incomplete-lease and ownership/source/deployment/release identifiers if active. Source A intentionally differs from deployment merge M; this is the reviewed contract, not a stale identity to regenerate. No lease removal, prepare/prepublish, source change, credentials or publication is requested.

Support replied at 04:35:30UTC after running the exact status command in original /app: exit 0, active=false, no incomplete lease directory, and no lease write/removal/change. They acknowledged the expected source-A/deployment-M distinction and preserved the AMD64 configuration. This is time-bound authenticated support evidence; repeat actual status before future workspace work.

Current state: PR #1357 is source/intent/CI/adapter verified, ready for review, unmerged and unpublished. No new lease exists from this task. Publication requires a future authorized authenticated workspace, the repository protocol and fresh public verification. No new paid project-chat request, purchase or upgrade has occurred.

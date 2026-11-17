# Android shipping-label read parity — 17 November 2026

## Accepted production result — 17 November 2026 UTC

**Mezan production publication is accepted.** The owner-authorized server repair
was published once and the official verifier finished successfully at
`2026-11-17T22:57:40.639406+00:00`. This result supersedes the earlier
prepublication checkpoint retained below. Physical Android/PDF/printer
acceptance has not been observed.

| Release identity | Frozen value |
|---|---|
| Source A | `7f6bc85333b1189e40dd5f1138b841cf2241ee23` |
| Intent-only B | `996fcfab1350298ecfb9d3472c14e6514c7649de` |
| Production merge M | `b009ee50a6b932368be8ad104c8c1b75e584b4e2` |
| M Git tree | `129beb5d3e5121a9f2ed3a8130dc26a3f5cee8d8` |
| Runtime release ID | `rg5-c13e7f152e61e9ba1f799a1a78744bb998e3cdfb8be7cfdc09f97886868fa17e` |
| Frontend artifact tree SHA-256 | `feca50cf723178a51482b5552d3b5d994ad735e3a946ca067f0256c2814bfae5` |

The detailed sanitized result and its provenance are recorded in
[PRODUCTION-VERIFICATION.json](PRODUCTION-VERIFICATION.json). The official
verification ran once from `2026-11-17T22:46:11.153685+00:00` to
`2026-11-17T22:57:40.639406+00:00`, exited `0`, and reported `verified: true`
after `689.49` seconds. Each of its three verification passes included
**594 successful frontend HTTP requests**, three shell/cache records and two
service-worker records. `frontend_checked_requests` in the original verifier
record is a list; **594 is its length**, represented here only by the summary
field `frontend_checked_requests_count`.

The accepted runtime boot time is `2026-11-17T22:42:04.514248Z`. The newly
observed platform deployment event is
`08d2acc5-d0a4-5d2b-ad97-dcdd429d296d`, artifact `dd9f327`, displayed as
“Deployment finished” on 18 November at 01:42 AM Asia/Riyadh. It was the
100th UI deployment, with preceding artifact `223bc07` now 99th. These
platform artifact labels are not Git source SHAs. Fresh official guard status
was `active: false` after verification. The coordinator observed one Publish
action, no services stopped, and all 120 unrelated untracked entries preserved.

The coordinator read the original official verifier record on the original
remote `/app` host at `/tmp/android-print-1974-verify.json`, checked its
fields, and calculated raw SHA-256
`e5c7b7a9da52cf777cb6afc7e5bc2e91c51d26198a6ad7ca54d1c3d2b8ff445b`.
This documentation contains a sanitized summary, not a local copy of that raw
record. Git commit ancestry, the existing STATUS source, and the matching
frozen intent blob were independently read during documentation preparation.

The outer Cloud Build command arguments and snapshot ordering were not
observed. View Logs could not be reviewed because the administration browser
page failed with Chromium Error 9. No complete build transcript or independently verified outer-build
snapshot ordering is claimed; acceptance rests on the observed deployment,
frozen identity, live runtime and official verifier results above.

## Documentation-only continuation and Android handoff

These records are prepared for a **separate evidence-only, non-deployable
branch** based on server M `b009ee50a6b932368be8ad104c8c1b75e584b4e2`.
Suggested branch: `codex/android-shipping-print-evidence-20261118`.
Only this STATUS and `PRODUCTION-VERIFICATION.json` are proposed changes.
No evidence branch was created by this preparation. Keep this documentation
out of the frozen source/release branch and production ref
`hotfix/prod-snap-meta-final`; it is not a new deployment candidate. Do not
regenerate the intent, change governed source/build outputs, rerun publication,
or reuse the finished release's lease for this documentation update.

Source A, intent B, production M, runtime release ID and frontend artifact
identity above remain the acceptance anchors. The inherited intent bytes
remain frozen: Git blob `cb55cc4d5f643f9f3dfb7feff4c6f260bd1c4c51`,
SHA-256 `f76793d280ddd2c4e0229feaa0b8d6953bfbea00164947e4af351319fb26e307`.
The evidence-only branch must preserve every path outside these two docs.

After server acceptance, Android PR360 was normally merged at
`2026-11-17T23:04:58Z` to mobile merge
`29f444cdc0d46406746cfa9c64b6f175e3dc577e`, preserving reviewed tree
`fe483dfb32f07db5df3c8ec61eaf57ce47dc4d1d` and parents
`3dfcf66d359414c964d20a3b8f36dff853698340` /
`66c453482600638a617e28add33280e3f60cca31`.
Its four required exact-source CI jobs had passed before that merge.

The coordinator then confirmed one EAS Android84 / `1.0.69` build from that
mobile merge: [build 96a6350e-fb9a-40ec-9d51-e686b3e318cd](https://expo.dev/accounts/amasi-sa/projects/amasi/builds/96a6350e-fb9a-40ec-9d51-e686b3e318cd).
It was **in progress at 23:10 UTC**. No completed APK, distribution, installed
phone behavior or physical printing is claimed by this server acceptance
record. APK artifact/source/signing checks and repeated device
download/return/print acceptance belong to the Android continuation.

## Scope and source

The owner reports that shipping-label download/printing fails across Android
orders while Mezan web can print the same completed orders. This server change
repairs the native explicit-fetch route; the independent embedded-WebView
message-origin repair belongs to `AMASI-SA/amasi-mobile`.

Repository: `AMASI-SA/AMASI-SA`.
Release branch: `codex/android-shipping-print-release-20261118`.
Reviewed production base: `b92d3eb8d12920663a052744c9c0d2773a785e1a`.
The original verified source remains recoverable as
`00e8e0c5d97ae71bfd119b95f61633dda2378bdd` on
`codex/android-shipping-print-parity-20261117` / PR1973.

## Reproduced problem and change

The Android `carrier-label/v1/complete-and-refresh` route previously used the
ordinary current-label read first only for merchant couriers. Every external
carrier instead entered manual completion, including already completed
orders. That unnecessarily required the additional local evidence intended
for a status write and could issue a redundant completed-status POST.

The actual-route regression demonstrates two failures before the fix: an
already completed external order sends an unnecessary status POST, and a
durably completed order with incomplete cached raw items returns the reported
completion-required message even though the normal current-label read finds
the correct current provider PDF. The incomplete cache is a synthetic
distinguishing fixture, not a claim about a particular live order.

All carriers now attempt the existing current-label read first. Only a fresh,
explicit `order_status_completed: false` response enters the unchanged fenced
completion operation. Provider failure, cancellation, unknown status,
permission revocation and changed shipping cannot select that fallback.
The merchant-courier completed-stage restriction remains unchanged.

No background job, polling loop, shipment-creation API, automatic retry,
order-specific exception or direct data repair is added. The read retains
the existing durable completion, current-provider, ownership, permission and
shipping snapshot checks. It does not impose manual status-write evidence
on an already completed order.

## Prepublication verification and historical checkpoint

Focused RED evidence: both new completed-external cases failed for the
expected reasons on the base route. Focused GREEN: 19 route tests passed,
with no skips, after the change. The final bounded five-suite run was repeated
independently by the parent: 145 tests and 59 subtests passed, no skips or
failures, exit 0. The pre-existing dispatch-only test was updated deliberately
to require the fresh unconfirmed read before manual completion. Cases cover repeat reads without provider
writes, current unconfirmed completion, incomplete evidence, nonprintable
statuses, provider errors, employee grants, revocation and shipping races. A focused provider-timeout regression also failed before the
read was moved inside the existing protected endpoint boundary; the final
route returns the existing structured 502 response without a status POST or
workflow change.

The new suite is included in the existing Review Completion Resumable CI
path trigger and Android-operation run list. The exact remote source commit,
final broader local checks and CI results are recorded in this task's draft
pull request. Issue 1006 has reached its 2,500-comment limit; use this status
file and that pull request as the continuation record.

On 18 November 2026 (Asia/Riyadh), the owner explicitly authorized publishing
Mezan first, with Android distribution afterward. That authorization permits
this named server repair's normal reviewed merge and protocol-v5 publication;
no repeat permission request is required. At that earlier checkpoint Android PR360 was separate
and unreleased; its later merge/build handoff is recorded above.

All applicable original-source checks passed: 27 successful check-runs,
6 expected scope skips, zero pending/failures. Independent exact-source CI XML
inspection confirmed all 19 new cases executed within the Android operation
suite (348 cases plus 59 subtests, zero skipped/failed/errored cases).

Production advanced through PR1972 to b92d3eb while the original source was
under review. Its 20 changed paths do not overlap this repair's five paths.
The successor source is constructed directly on that newer production commit,
preserving its Qoyod, frontend, security, courier and tracked intent bytes.
The original source is not merged across an intervening intent history.
A new candidate intent and exact final-B gates are required for this source.

At the earlier prepublication checkpoint, PR1972's owner was executing the
original shared workspace adapter/publication and held a preview backend
restoration obligation. Its complete release/handoff and an independent
official inactive guard were prerequisites to this task's adapter and lease;
pre-lease inactivity alone did not permit taking over that workspace. No
earlier release intent or another task's lease was authorized for reuse.

The checkpoint above preceded the production merge/publication. Its pending
server status is superseded by the accepted production result at the top of
this file. Governed source remains frozen; this update records evidence only.
Physical Android/PDF/printer acceptance remains unobserved.

Production changed by the authorized server release: **yes — accepted**.
Production changed by this documentation preparation: **no**.

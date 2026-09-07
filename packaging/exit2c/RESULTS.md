# EXIT-2C delivery — final drain change awaits Linux confirmation

Worker callback classification is fixed. Full isolated Linux acceptance PASSED
at `218d55a1f9142b5e1c422d362bdd1114dd5a9d95`, including actual worker singleton,
fence loss, restart and TERM. A subsequent safety review found direct workers
could continue during scheduler shutdown draining. That ordering/deadline fix
is saved and locally tested, but final-source Linux acceptance remains pending.
This candidate is NOT deploy-ready and has no governed-build approval.

Candidate Source SHA: `c344008a097fdb778b8175ed300b4b556e1682b7`.
Full Linux-tested SHA: `218d55a1f9142b5e1c422d362bdd1114dd5a9d95`.
The final delivery commit adds only README, RESULTS, WORKER_CALLBACKS and sources.
Baseline: `1de6118484ac4fe1d0981e230618dbb573d8c58c`.
Branch: `codex/exit-2c-runtime-candidate`.
Independent checkout: `C:/Users/amasi/.codex/visualizations/2026/09/05/01a073a3-3dd0-7901-82c1-556ca731163b/EXIT-2C/checkout`.
The protected Windows checkout, /app and other task workspaces were not changed.

## Sources and minimal implementation

Exact 40-path source map and exclusions: [sources.json](sources.json).

| Source | Pinned SHA | Purpose |
| --- | --- | --- |
| #1014 | 457de8b94384f944000edc8f54045538f85ce22f | Portable dependencies, baseline/rehearsal evidence, import-index guard |
| #1003 | d68e5fb20837ae935aa7755df436c26368e784a3 | Bounded Mongo, retryable auth 503, worker polling and frontend auth stability |
| #1001 implementation | 5c09aea70033ba6569dff68db07db31d7247105a | Bounded/coalesced Qoyod read workload |

Source assembly commit: `4e022ee11c6f221cba625cd18367c9672da89ba5`.
#1001 Intent commit `4a77d53e2ed2bbd44c1316cb49697d27aea65e95` excluded.
No overlap among the three selected source path sets. Integration subsequently
changes auth installation and the bank-review import guard. #1003 UTF-8 LF/EOF
normalization is separate from exact source assembly. Additional actual-route
fix accepts/forwards `permissions` in the baseline preparation full-assignment
wrapper; the allocation and permission policies stay enforced. Test assertions
were adjusted to the explicit installer keyword, never removed.

#1013 was not imported. Its file overlap is `backend/server.py`, present here
from #1003 only. Latest read-back: #1013 Draft at 6aecb923..., #1014 Draft at
457de8b..., #1003 Draft at d68e5fb..., #1001 Draft at its existing Intent head.
No existing PR, source branch, release files or Release Guard was modified.

Role commands and their limitations are in [README.md](README.md). New code:
`backend/independent_runtime.py` (explicit roles), `independent_schema.py`
(index-only migration), optional index initialization in five real security
installers, and the small preparation wrapper fix. Existing server entrypoint
keeps its behavior. Web serves the full routes with actual per-process security;
it does not dispatch the original mixed startup lifecycle. The Dockerfile is a
synthetic acceptance image, not a live deployment manifest.

## Actual acceptance at Linux-tested SHA (218d55a1)

Successful run: [34058908333](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34058908333),
job 101555777193. Overall conclusion: **success at 218d55a1 only**.
Local-only source delta afterward: c344008a, bounded direct-worker-first shutdown.

| Area | Actual result | Scope / remaining limit |
| --- | --- | --- |
| Clean dependencies | PASS | Python 3.11.16 image, clean pip install/check, Node 22.23.2 + Yarn 1.22.22 frozen install; no final frontend governed build |
| Inherited regressions | PASS: 272 tests, 11 subtests; frontend 27 tests/6 suites | Includes #1001/#1003, #1014 boundary and PDF contracts; focused suites use documented doubles |
| Partial migration | PASS | Duplicate synthetic email fails; healthy Mongo still gives web readiness 503; completion marker is absent |
| Migration retry/idempotence | PASS | Repair only the fixture; initial Owner created once; repeated completed migration is follower with zero profiled writes |
| Protected web x2 | PASS | Actual security installed before readiness, unauthenticated denial; profiler shows no import/startup/shutdown writes, including auto_send=true fixture |
| MFA/OTP/Passkey | PASS | Real password and second-factor HTTP handlers across processes; invalid input/signature and replay rejected; software WebAuthn authenticator, seeded OTP cooldown, no live SMTP |
| Sessions/CORS/CSRF/RBAC | PASS | Target-origin preflight accepted, foreign origin rejected, cross-process refresh and secure cookie attributes; expired access/refresh and password-update revocation rejected; viewer denied manager/owner data |
| Preparation/attachments/PDF | PASS | Incomplete allocation HTTP 409, synthetic completion then successful/idempotent start; actual PNG upload/retrieval hash, generated PDFs, persisted state/history/export deduplication |
| Mongo outage/recovery | PASS | Network-isolated Mongo pause: live stays up, readiness/auth 503, no cookie deletion; recovery succeeds |
| Restart/TERM | PASS for both web processes | Existing sessions, attachment bytes, PDF regeneration and history survive actual process restart; clean TERM exit 0 |
| Worker without arming | PASS denial | Explicit worker flag required, saved settings alone insufficient |
| Armed worker | PASS | Exact callback pairs validated; stable worker claim and heartbeat held |
| Duplicate worker/fence loss/restart/TERM | PASS | Real second process refused; synthetic fence loss stops first; restart succeeds and TERM exits 0 with own claim removed |
| Slow/stalled shutdown safety delta | LOCAL PASS, LINUX PENDING | Final source cancels direct tasks before hooks, bounds drain, retains claim on timeout/error; not in the green Linux SHA |

Runtime enforcement: disposable Mongo 7.0.16 pinned image in network-none;
app/probe/worker share that namespace (loopback only), no host ports, read-only
filesystem and tmpfs, capabilities dropped, synthetic credentials only. No
Production reads/writes/exports or live provider/SMTP traffic. Public fixed
Qoyod encryption fixture exists only inside the isolated regression process,
after boundary validation; it is not a replacement live encryption key.

Limits: no hardware enrollment or real mail delivery; one owner/viewer fixture,
not exhaustive merchant isolation; prepared batch rather than full draft/finalize
UI; legacy one-line PDF rather than physical-piece QR; no byte-identical archival
PDF claim. auto_send=true fixture retains legacy_pipeline_frozen and does not
simulate a fully eligible live dispatch. No final artifact, hosted TLS/proxy,
load/capacity measurement, real backup restore or live schema reconciliation.

## Worker cause, classification and final safety delta

The original guard knew three module names and missed three append-registered
families. [WORKER_CALLBACKS.md](WORKER_CALLBACKS.md) records exact factories,
start/stop names, flags, source registration and web/worker/migration ownership.
The real Linux callback inventory now proves these five active starts:

- snapchat_v2.scheduler.attach_shadow_scheduler.<locals>.start
- integrations_control_center.snapchat_capi_purchases.attach_snapchat_capi_purchase_routes.<locals>.start
- integrations_control_center.ads_auto_sync_scheduler.attach_ads_auto_sync_scheduler.<locals>.start
- campaign_ai_subprocess_scheduler.attach_campaign_ai_subprocess_scheduler.<locals>._start_campaign_ai_subprocess_scheduler
- advertising_product_watch_scheduler_v3.attach_advertising_product_watch_scheduler.<locals>._start

The product-taxonomy pair exists in its source factory and is explicitly
classified, but was absent from this actual server callback inventory; do not
claim its execution was tested. No unknown callback is silently skipped, no
whole module is broadly allowed, and no provider flag is enabled by the fix.

Local red/green classifier test first reproduced the original rejection, then
five tests passed. Real Linux passed 272 Backend +27 Frontend and all role tests.
A follow-up review identified that direct Qoyod/Salla cancellation followed
potentially slow scheduler stop hooks. Two local async tests reproduced that
ordering/deadline failure, then the final seven-test local suite passed after:

- cancelling direct tasks and initialization before starting stop-hook drain;
- keeping heartbeat during normal drain, with a six-second shared deadline;
- failing and retaining the claim if drain times out/errors, then cancelling
  heartbeat/halt; only a successful drain reaches fenced claim deletion.

Final source c344008a has **local tests only** for this delta. No new full Linux
run fits the remaining two-minute conservative allowance. Estimated full clean
acceptance is about five minutes (last observed run 4m07s). Next decision is
review plus sufficient separately authorized CI time, with fresh quota check,
for final-source Linux verification before any governed build decision.
An uncooperative task after timeout still requires an external process supervisor
kill deadline; retained claim is not proof the process has stopped. Real-host
supervisor escalation and active provider subprocess termination are untested.

## CI ledger and release separation (owner extended EXIT-2C cap to 25m)

| Run | SHA | Observed run duration | Conservative debit | Result |
| --- | --- | --- | --- | --- |
| [34032443956](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34032443956) | 5a30cd30... | 2m57s | 3m | Reserved .test employee email rejected by real validation |
| [34032671110](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34032671110) | ad22aba7... | 2m33s | 3m | 249 pass/13 fail: inherited test packaging/fixture/signature assertions |
| [34033146099](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34033146099) | f77ce346... | 2m13s | 3m | 261 pass/1 remaining multiline source assertion |
| [34033337350](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34033337350) | ed28c2de... | 3m12s | 4m | 262 pass; actual start-file HTTP500 from guard keyword mismatch |
| [34033638398](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34033638398) | 08ee9f3d... | 4m42s | 5m | 267 pass; web/migration/HTTP/restart pass; armed worker callback failure |
| [34058908333](https://github.com/AMASI-SA/AMASI-SA/actions/runs/34058908333) | 218d55a1... | 4m07s | 5m | SUCCESS: 272 Backend/27 Frontend, actual role/worker acceptance |

Observed run-wall sum: **19m44s** (includes orchestration; not an exact billed
runner-minute figure). Conservative aggregate debit: **23/25 EXIT-2C minutes**,
**33/45 including prior 10**. Two minutes remain; ten more remain reserved for a
later governed-build decision, not authorized here. A fresh full Linux run needs
about five minutes, so it was not started for the final drain delta.
One standard ubuntu-24.04 job per run, no automatic retries/larger runners/artifact
uploads. All-workflow branch view was checked before run six: five prior runs
only. Final branch/PR read-back checks for unintended workflows are required.
Fresh pre-run-six billing: $0 Actions billable, 0/2000 included minutes and
0/0.5GB storage; gross $3.53 fully discounted. Own debit is retained regardless
of public-repository discounting/UI lag.

The local-only drain commit and final documentation use [skip ci] to avoid
unbudgeted push/PR jobs. This means final-head required checks remain pending;
it does NOT transfer the green result at 218d55a1 to c344008a or waive any check.

Prior accepted evidence remains distinct and was not rerun:
- baseline governed build succeeded at 1de6118...: run 33997731256;
- EXIT-2A runtime succeeded at d379c62a7432873e310edd8459da1cae05f17d0c: run 33998804095;
- EXIT-2A delivery head 457de8b... is documentation delivery, not this candidate.

No new Intent or operational Release Guard lease. Final candidate governed build
and all required final-source checks remain unaccepted. The proposed future
10-minute governed build reserve was not used or treated as authorized.

## Support, ownership and decision

Approved metadata-only support request sent once after account/duplicate checks:
Gmail message/thread `1a076921d10b2c44`, SENT, to support@emergent.sh from
amasi.jewelery@gmail.com. Exact approved subject/body, no attachments/secrets.
It explicitly does not authorize export/restore/access change/upgrade/deployment.

There is still **no proved and authorized path to obtain and restore Production
data**. Emergent/provider must supply deployment-to-DB identity, current account
export/restore authority and actual successful backup metadata or confirm none.
Persistent volume/bucket ownership/copy mechanism and live source identity remain
unverified. Domain next page: owner's mezansalla.com registrar details showing
registrar/account and nameservers/DNS operator only. No independent Atlas account
is assumed. Exact missing evidence/holder is listed in README; no customer data
or old exposed connection value was read/copied/used in this stage.

Decision requested: review the Draft and the local-only shutdown delta, then
provide enough CI allocation for final-source Linux acceptance (about five
minutes versus two remaining). Governed build/source Intent and genuine data
restore remain separate later approvals. No merge, Ready for Review, deploy,
paid resources, DNS/OAuth/webhook change, Emergent mutation or cancellation.

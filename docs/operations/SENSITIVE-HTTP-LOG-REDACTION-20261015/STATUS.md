# Sensitive HTTP log redaction — published and verified

- Task: `SENSITIVE-HTTP-LOG-REDACTION-20261015`.
- Outcome: **published and verified**.
- Repository: `AMASI-SA/AMASI-SA`; merged [PR #1374](https://github.com/AMASI-SA/AMASI-SA/pull/1374).
- Source A: `2fbc6a96b2010f78cbb2e1aac94dbf6c4b40c74f`.
- Reviewed intent-only B: `f75b9db45f48d5a37301fdb06ca6f84c1310e027`, parent A.
- Deployment D: `ce3c6ffaf3bc445c46d178c04b8765829b6552a9`.
- D parents: previous production J `f174e294d12e2d96f0ba03678c4e4768036bea2d`, B.
- B/D tree: `5692bae267a1c1e3945a3711a249b16e8ea5979f`, exactly equivalent.
- A/B were not squashed or rebased after freeze.
- Runtime release:
  `rg5-ddb356568a59c7bf83af43b08ce98f7a7a102b52dff07eb3db76fedcc3cb3837`.
- Frontend artifact:
  `d2dbe74dd4ca72214896feae83a929b88c736cf7e3f140b06aaa112344b7ea95`.
- New runtime boot: `2026-10-15T22:43:37.710459+00:00`.
- Production changed by this task: **yes**, with evidence below.

## Fixed behavior and scope

Meta credential query fields were present in HTTPX INFO request URLs. The new
stdlib-only `backend/sensitive_logging.py` wraps existing root/named logging
formatters before provider imports in `backend/server.py`. Final messages,
tracebacks and stack text redact sensitive query aliases, encoded key names,
punctuated/quoted values, reflected credential fields and bearer values.
Outgoing requests, response behavior, ordinary query/diagnostic fields and
existing structured Uvicorn/TikTok filters are preserved.

Implementation files: `backend/sensitive_logging.py`, `backend/server.py`,
`backend/tests/test_sensitive_logging.py`,
`.github/workflows/security-gate.yml`, and this status document. B changes only
`release/release-intent-v5.json`.

Historical retained logs are unchanged. No real credentials were used as test
fixtures or exported; no broad runtime log reads, provider/account writes,
credential rotations/deletions, database/business writes or paid Emergent chat
prompts were performed by this task. The fix covers configured standard web
logging; future external logging reconfiguration must reinstall the wrapper.
Arbitrary unlabelled secrets, direct prints, third-party exports and separately
configured processes were not established covered.

## Review and tests

Independent boundary investigation and one candidate review completed.
Review's delimiter/quote suffix issue was confirmed and corrected before freeze,
with additional real-HTTPX pagination regression tests. No other concrete
finding was reported. The original synthetic token/proof exposure reproduced
before the fix; all focused negative and legitimate controls passed afterward.

Exact B final checks: **29 success / 4 skipped / 0 failure / 0 pending**.
The four required build/security/preflight checks and CodeQL Python/JavaScript/
aggregate passed. Interim CodeQL neutral aggregate resolved after Python
completed. Skips: manual redeploy, two unrelated Snapchat settings jobs and
TikTok release-candidate job; none is counted as a pass.

- `python -B -m pytest --noconftest -q backend/tests/test_sensitive_logging.py`:
  **40 passed**, exit0. Changed-source syntax and `git diff --check`: passed.
- Exact-B Security Gate `38816182837`, backend `116701310970`:
  **57 logging/provider tests**, **83 security tests**, and **216 inherited
  tests + 29 subtests** passed; audit, compile and policy checks passed.
- Frontend security `116701311233`: full tests/audit/CSP/build passed; source
  verification had **275 suites / 1780 tests** passing.
- Readiness `38816182686`: preflight `116701310509`, Frontend
  `116701310387`, Host20 clean-clone adapter `116701766934` passed.
  Rehearsal verifies no-Git package boundaries, governed artifact/proof/identity,
  isolated Backend health and canonical/cache-busted JSON routes.
- Evidence artifact `12011300143`, SHA256
  `516adfdc743fabd0796b2a760c8b03fa22a9c96ecd2229414fc3142b623163cb`,
  expires `2026-10-29T22:21:13Z`.

Earlier unfrozen merge history was refused by the full-history intent gate; it
was replaced by clean source A on J. That failed readiness attempt and earlier
bearer/dependency failures were not relabelled passes. Intermediate source is
recoverable on `codex/sensitive-log-60409d2-checkpoint`. Candidate ZIP digest
`8dc0b658046cfff48698a2879abe171c0be36c36d1c7ba7c0529c62ea658a7c6`
and official A/B intent validators passed before B was attached.

## Local deployment preparation

Shared guard inactive and tracked source clean were checked before /app update.
Initial root disk was full. Generated Yarn cache was moved intact to
`/tmp/mezan-sensitive-yarn-cache-vwh0kzcw/yarn`, original-path symlink preserved;
938775369 file bytes retained, about1.17GB freed. No log/data cleanup occurred.

/app fast-forwarded J→D only, preserving unrelated
`.emergent/system_deps.txt.tmp`, `INTENT-1367.json.gz`,
`deployer-agent-docs/`, original Terminal1 and blank unsaved editor.
No untracked Backend/scripts bytecode cache was found before the adapter.

/app/frontend official `yarn build` with public origin, TMPDIR=/tmp and
PYTHONDONTWRITEBYTECODE=1 completed **exit0 in88.78s**; exact A/release/artifact
assertions passed. Log: `/tmp/sensitive-1374-adapter.log`.
Generated Backend identity263041 bytes, SHA256
`5fb7f9732b3e623530495a1847e5bbaf2ac444ea935cdd15ec65a1b5b0f6adcd`.
No generated build/identity was manually fabricated or force-added.

## Publication and runtime acceptance

- Lease actor: `codex-sensitive-logs-1374-05c4b4df7c11`.
- Official prepare exit0 at `2026-10-15T22:33:42.403220+00:00`.
- Official prepublish exit0 / ready_to_publish:true with exact A/D/release.
- Re-publish changes → Yes, Re-publish was submitted **once**.
- New platform start: **Oct16 01:36AM Asia/Riyadh**.
- New green **Deployment finished Oct16 01:44AM Asia/Riyadh**.
- New Live snapshot: `b9d75b5`; prior `4c6e3b8` is history.
  Displayed Publish numbers renumber; use snapshot/time. Current UI wording is
  Deployment finished/Live; literal older Deployment Succeeded was not exposed.

Official command:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B scripts/production_release_guard.py verify --url https://mezansalla.com
```

Completed **exit0 in67.82s**, **verified:true**, **checks:3**. All three
consecutive source/identity/critical-hash/Backend source/Frontend artifact/proof
checks matched, including canonical/cache-busted JSON bytes and MIME/cache
checks. Proof has **40 Frontend request variants per observation**.
Verify JSON `/tmp/sensitive-1374-verify.json`, SHA256
`d9346e1fd27e3dd98ea415aa939f7bfdcea631a96a9e06e8539ba1f401e67640`.
Immediately following guard status: **active:false**. The guard closed its own
lease; no manual lease clearing was performed.

Post-verification standalone smoke executed the deployed source's formatter
using a synthetic HTTPX-shaped event. Both values were masked, method/status/
ordinary query diagnostics preserved, input unchanged, network_used:false.
No live Meta request was made for this smoke. Final /app tracked status empty.
The source/CI tests separately exercised actual HTTPX MockTransport.

## Final handoff

Publication and requested verification are complete. No republish is needed.
Fresh Git production ref remained D at final handoff. Frozen A/B, merged D and
review evidence remain recoverable. This accurate final documentation checkpoint
is outside D and **must not be deployed or merged as an intent-only release**.
Earlier publication-preparation status branch remains historical.

Future changes need a fresh current-production/ledger/guard read and a new
reviewed source/intent chain; never reuse this closed lease. Historical log
retention and any future credential rotation are separate scopes.

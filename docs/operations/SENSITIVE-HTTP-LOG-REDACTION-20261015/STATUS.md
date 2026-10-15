# Sensitive HTTP log redaction — verified source handoff

- Task: `SENSITIVE-HTTP-LOG-REDACTION-20261015`.
- Outcome: **fixed in source and verified; publication preparation in progress**.
- Repository: `AMASI-SA/AMASI-SA`; PR [#1374](https://github.com/AMASI-SA/AMASI-SA/pull/1374).
- Code branch: `codex/redact-sensitive-http-logs-20261015`.
- Accepted code/source: `2fbc6a96b2010f78cbb2e1aac94dbf6c4b40c74f`.
- Code tree: `79e16198efda289486bc936badefba5e12084002`.
- Direct parent / current reviewed production baseline:
  `f174e294d12e2d96f0ba03678c4e4768036bea2d`.
- Production changed by this task: **no**. No merge to production, release-intent
  commit, lease, /app mutation, restart or publication.
- This final status is a documentation-only checkpoint after the tested code
  source. It must not be deployed or treated as an intent-only B.

## Vulnerable path and invariant

Meta reporting/discovery/OAuth construct credential query fields including
access_token, appsecret_proof, client_secret, fb_exchange_token, input_token and
code. Existing INFO logging emitted HTTPX's complete URL. Provider pagination
can supply the complete URL independently of the original params dictionary.
An offline real-HTTPX MockTransport baseline reproduced both synthetic token and
proof in emitted output, with a legitimate HTTP200/JSON response.

The boundary now replaces sensitive values in final formatted standard-log
output. HTTP requests, response data, methods, hosts, paths, status codes,
ordinary query fields and provider error codes retain their behavior.
Sanitizing after formatting also covers chained exceptions, cached exception
text and stack information. Original LogRecords and structured Uvicorn/TikTok
filter arguments remain intact.

## Changed files and strategy

1. `backend/sensitive_logging.py`: stdlib-only final formatter wrapper and
   idempotent installer for existing root and named logger handlers; covers
   aliases, case/percent-encoded key names, punctuation/quoted query values,
   reflected credential fields and bearer values.
2. `backend/server.py`: installs before provider imports; replaces the late
   basicConfig call with the same level/format policy at early bootstrap.
3. `backend/tests/test_sensitive_logging.py`: 40 offline output contracts using
   real HTTPX MockTransport, real Uvicorn AccessFormatter and isolated execution
   of the actual TikTok callback filter; verifies both privacy and normal use.
4. `.github/workflows/security-gate.yml`: compiles helper and runs output
   contracts, four Meta suites and the relevant stateless TikTok logging test.
5. This task status document.

Final-output wrapping is narrower than changing every provider request and
preserves credentials at the provider boundary. Root logger filters alone
would miss propagated records; flattening records would break existing
structured access filters.

## Ordered fresh verification

All local pytest invocations used `PYTHONDONTWRITEBYTECODE=1`,
`PYTHONPATH=backend` and `--noconftest`, avoiding the repository conftest's
runtime .env loader. No real credentials or runtime logs were used as fixtures.

1. Syntax/diff: changed Python source compiled in memory; final CI Backend
   preflight compiled backend and ran release guard/identity suites; all passed.
   `git diff --check f174e294d12e2d96f0ba03678c4e4768036bea2d HEAD`: exit0.
2. Focused trigger and alternate inputs:
   `python -B -m pytest --noconftest -q backend/tests/test_sensitive_logging.py`:
   **40 passed**, exit0. Original synthetic token/proof no longer occur in
   emitted handler output; request query values and normal responses are intact.
   Alternate cases cover OAuth aliases, encoded keys, duplicated fields,
   punctuation, quotes, reflected fields, bearer headers and exception copies.
3. Legitimate behavior / owning checks, exact accepted source:
   Security Gate run `38806988268`, backend job `116670272352`:
   - output + four Meta suites + stateless TikTok access logging:
     **57 passed, no skips**, exit0;
   - existing security contracts: **83 passed**, exit0;
   - inherited boundary regressions: **216 passed + 29 subtests**, exit0;
   - dependency policy, audit, compile and toolchain contracts passed.
   Frontend security job `116670272127`: **275 suites / 1780 tests passed**,
   dependency audit, CSP/security manifests and production build passed.
   Release readiness run `38806988380`: Backend preflight `116670272627`
   and Frontend build `116670273060` passed. The official source transition
   resolver returns the exact f174e294d baseline; no intent history or byte diff
   exists in baseline..source.

Final exact-source check summary: **28 success / 4 skipped / 0 failure /
0 pending**. Four required checks and CodeQL Python/JavaScript/aggregate passed.
An interim CodeQL neutral aggregate resolved after Python completed.
Skips are manual redeploy, Host20 reviewed-intent adapter rehearsal (no B yet),
and two unrelated Snapchat settings jobs; none is counted as a pass.

CI created candidate artifact `12006713115`, name
`release-v5-reviewed-intent-candidate`, run `38806988380`,
SHA256 `8dc0b658046cfff48698a2879abe171c0be36c36d1c7ba7c0529c62ea658a7c6`,
expires `2026-10-17T21:05:05Z`. This candidate is not an approved release
intent or evidence of production publication.

## Review and failed-attempt accounting

Independent read-only boundary investigation and one candidate review completed.
Review found partial-value delimiter/quote leakage; confirmed with synthetic
inputs and fixed, with expanded actual HTTPX pagination regressions. No other
concrete finding was reported. Parent verified the corrected final candidate.

Earlier attempts found a bearer-header gap (corrected) and missing local test
dependencies. Direct git push lacked authentication; the connected GitHub API
created verified remote commits instead. Local Python3.12 dependency differences
were superseded by exact-source Python3.11.17 CI with declared requirements.

Intermediate source `0ad7c14f136274fc57d754b355e050f089406138` was based on
older production. Merge `60409d2e89357165f924630f47ee6cb23a7221e5` preserved
new shipping content, but release readiness correctly refused its full-history
intent transition. That failed build is not acceptance evidence. The intermediate
is preserved on `codex/sensitive-log-60409d2-checkpoint`. Only the unfrozen owned
task head was rebuilt directly from current production using an expected-head
update; production history, frozen reviewed A/B and release policy were untouched.
Final runtime/test blobs equal the reviewed corrected candidate.

## Limits and next safe action

Configured standard logging in the web process is covered. Future external
logging reconfiguration must reinstall the wrapper. Historical retained logs,
arbitrary unlabelled secrets, direct prints, third-party exports and separately
configured processes were not established covered. No live Meta request was
needed; compatibility proof uses synthetic provider transports. No credentials
were rotated/deleted; no provider, account, database or business data was changed.

Implementation is complete. Before any separately authorized publication,
re-read current production, ledger and /app guard; preserve any other task's
lease. Recheck current baseline and candidate validity, freeze a new intent-only
B for the approved source, require exact-B clean-clone/Host20 package rehearsal,
then the normal one-publish/three-probe guard procedure. Never deploy this source
using the inherited shipping intent or reuse another task's lease. Paid Emergent
chat prompts remain prohibited by the user.

## Publication preparation — user requested continuation

- Immutable source A `2fbc6a96b2010f78cbb2e1aac94dbf6c4b40c74f`.
- Reviewed intent-only B `f75b9db45f48d5a37301fdb06ca6f84c1310e027`, parent A,
  tree `5692bae267a1c1e3945a3711a249b16e8ea5979f`.
- PR1374 head is B; backup `codex/sensitive-logs-reviewed-2fbc6a96`.
- Target `rg5-ddb356568a59c7bf83af43b08ce98f7a7a102b52dff07eb3db76fedcc3cb3837`.
- Official external candidate validator passed on exact A and frozen B validator
  was started; exact-B checks and Host20 clean-clone/package rehearsal pending.
- Shared guard observed inactive, /app HEAD/current production J=f174e294d.
- Disk was full. Generated Yarn cache moved intact to
  `/tmp/mezan-sensitive-yarn-cache-vwh0kzcw/yarn`, original-path symlink preserved.
  Byte total938775369 retained; /app free1175199744 bytes, tracked clean.
- Original terminal/unsaved editor and unrelated untracked files preserved.
- No lease, /app source update, merge or publish yet. Next require exact-B
  acceptance and fresh shared guard/production recheck before preserving A/B
  merge. Materialize official adapter before prepare/prepublish.
- This documentation checkpoint is outside frozen A/B and must not be deployed.

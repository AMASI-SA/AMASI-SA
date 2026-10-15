# Sensitive HTTP log redaction — 2026-10-15

- Task: `SENSITIVE-HTTP-LOG-REDACTION-20261015`.
- User authorized implementation of the separately observed runtime logging fix.
- Base: production `231f5714257220cdfe192428275c2bca62e0bd2d`.
- Current integrated production baseline: `f174e294d12e2d96f0ba03678c4e4768036bea2d`
  (PR1373 shipping change and reviewed intent; no overlap with these five files).
- Branch: `codex/redact-sensitive-http-logs-20261015`.
- State: candidate review completed and findings corrected; clean source lineage
  replaces a refused intermediate merge; required final-head CI pending.
- Production changed: **no**. No merge, release intent, lease, restart or deployment.

## Finding and boundary

Meta requests include credential query parameters. Global INFO logging emitted
HTTPX's complete request URLs. An offline MockTransport reproduction confirmed
that both synthetic token and proof appeared in the original log output while
the request itself succeeded. No real credentials or runtime logs were copied
into this task's artifacts.

The new `backend/sensitive_logging.py` wraps existing root and named logger
handler formatters before provider imports in `backend/server.py`. It sanitizes
the final rendered output, including exception chains, cached exception text and
stack information. Requests, structured access-log filter arguments, existing
formatter behavior, log levels, methods, hosts, paths, statuses, ordinary query
fields and provider diagnostic codes remain intact. Repeated installation is
idempotent. OAuth query aliases and percent-encoded key names are covered, as
are reflected credential fields and bearer values.

Changed source: `backend/sensitive_logging.py`, `backend/server.py`.
Regression: `backend/tests/test_sensitive_logging.py`.
CI: `.github/workflows/security-gate.yml` adds the new output contracts plus
nearby synthetic Meta/TikTok compatibility suites. This status is the fifth file.

## Fresh verification

Use `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=backend`, and `--noconftest`.
The repository conftest reads a runtime .env; tests here must remain offline.

- Baseline: real HTTPX 0.28.1 with MockTransport confirmed synthetic values
  present in INFO output and normal HTTP 200/JSON response (exit 0).
- Focused candidate: `python -B -m pytest --noconftest -q
  backend/tests/test_sensitive_logging.py` — **40 passed**, exit 0.
- Changed Python source syntax compiled in memory — passed, exit 0.
- `git diff --check` — passed, exit 0.
- Local interpreter: Python 3.12.14; HTTPX 0.28.1, Uvicorn 0.25.0. Other existing
  local test dependencies differ from the repository lock. Required CI must
  validate the declared Python 3.11 dependency closure before readiness.
- Earlier focused attempts exposed a bearer-header redaction gap (corrected)
  and local missing Uvicorn/provider dependencies. The focused suite now uses
  the real isolated TikTok callback filter class without loading its unrelated
  provider package. Those failed attempts are not acceptance evidence.
- Independent read-only review identified partial-value leakage at punctuation
  and quoted query delimiters. Confirmed with synthetic inputs and corrected;
  real HTTPX pagination URL tests now include literal parentheses, semicolons,
  braces, OAuth code and encoded keys. Quoted field replacements remain stable
  on repeated sanitization. Review found no other concrete issue. One review
  cycle completed; final corrected candidate is verified by the focused suite.
- First remote source checkpoint: `0ad7c14f136274fc57d754b355e050f089406138`;
  Draft PR1374. Production advanced independently during this task. Its exact
  seven-file compare contains only shipping workflow/service/tests/status and
  release intent. Intermediate merge `60409d2e89357165f924630f47ee6cb23a7221e5`
  preserved that content but release readiness correctly refused the full-history
  intent transition. It is a failed readiness attempt, not accepted release evidence.
- Intermediate exact-head Security Gate passed: job `116669059138`, run
  `38806491218`. New logging plus provider regressions: **59 passed, 11 skipped**.
  All 11 skips were unrelated TikTok database tests requiring an explicit
  disposable Mongo URI. Final CI runs only TikTok's relevant stateless callback
  logging test, along with all four Meta suites and the 40 output contracts.
- Correction: create a new source commit directly on current production
  `f174e294d12e2d96f0ba03678c4e4768036bea2d`, carrying the same runtime/test blobs
  and unchanged inherited release intent. Preserve the intermediate branch before
  moving this task's unfrozen PR head with an expected-head check. No guard or
  release policy is weakened; no reviewed A/B has been frozen for this task.

## Remaining scope and next safe action

Run required CI and the synthetic provider suites on the exact remote head, and
update this status and Issue #1006. Do not merge or publish this checkpoint.

Protection covers this web process's configured standard logging handlers.
Future external logging reconfiguration must call the installer again. This
does not retroactively alter retained logs or establish coverage for arbitrary
unlabelled secrets, direct print calls, third-party export systems, or separately
configured processes. No credential rotation, deletion, provider write, database
change or paid Emergent chat action is part of this implementation.

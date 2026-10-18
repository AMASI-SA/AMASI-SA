# WIP: review approval after hiding customer options

Status: investigation incomplete; no production-code fix and no deployment.

Base: `8d731ec92536e872fef830c2fcd995dde3046c82`.
Branch: `codex/review-hidden-option-server-20261018`.

The reported native flow hides an option, then receives an uncertain review
completion result and blocks a second approval. The source-level afternoon
fix remains present. This checkpoint does not establish the production cause.

## Verified locally

Added two real-Mongo ASGI tests in `backend/tests/test_review_attempt_closure.py`:

- first export-control write, fresh displayed approval, successful completion,
  with the hidden key retained;
- initial intake containing actual synthetic customer options, hide, fresh
  displayed approval, completion and replay of the same request: one completion
  event and the hidden key retained.

Both focused tests passed individually (exit 0) on 2026-10-18, using the existing
loopback-only replica set and isolated UUID databases. Existing fixture guards
reject provider HTTP and Salla calls. No production data was copied to fixtures.
The second test's first draft changed options after an already reserved intake;
that correctly encountered `component_plan_conflict`. The final fixture includes
options at initial intake and passes; that first draft is not a reproduction of
the reported regression.

Run from this checkout with the isolated Mongo URI already configured:

```powershell
$env:PYTHONPATH='backend;backend/tests'
& 'D:/codex-test-runtime/review-minimal-shipping/venv/Scripts/python.exe' -c "import sys,types,pathlib,unittest; p=types.ModuleType('tests'); p.__path__=[str(pathlib.Path('backend/tests').resolve())]; sys.modules['tests']=p; unittest.main(module=None,argv=['unittest','test_review_attempt_closure.ReviewAttemptClosureTests.test_first_hide_then_fresh_display_can_complete_without_recovery','test_review_attempt_closure.ReviewAttemptClosureTests.test_hide_real_customer_option_after_intake_can_complete_once','-v'])"
```

## Remaining evidence

- Production UI showed the reported order pending review, but the retained
  runtime log window did not contain the original rejection.
- The shared Emergent IDE disconnected before a shell became usable. No command
  was successfully executed there for this investigation.
- A loopback-only local read-only connection attempt timed out before accessing
  Mongo metadata. No production database reads or writes succeeded via it; the
  helper was stopped and transient connection material cleared.
- Need the original request's sanitized rejection code/stage and exact server
  workflow/attempt metadata. Do not infer non-commit from pending status alone.
- The installed emulator's fresh read of the selected order showed approval
  enabled and no additional specifications, unlike the supplied screenshot.
  No business action was executed; this does not establish that the original
  attempt recovered or that the defect is fixed.

Next: restore a trusted read-only path to those records, reproduce the observed
mechanism in an isolated fixture, then implement a focused fix. Preserve signed
approval, exact receipt binding, unknown-outcome protection and current shipping
behavior. Do not include this WIP in the separate shipping APK47 release.

Production changed: **no**. No hide/review/closure/stock/financial writes, merge,
release lease, deployment or APK build performed by this investigation.

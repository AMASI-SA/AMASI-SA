# WIP: review approval after hiding customer options

Status: investigation incomplete; no production-code fix and no deployment.

Base: `8d731ec92536e872fef830c2fcd995dde3046c82`.
Branch: `codex/review-hidden-option-server-20261018`.

The latest owner clarification is that hiding an option immediately disables
review approval and displays a message, BEFORE an approval is submitted.
Showing the option again re-enables approval in the SAME Android app. The owner
subsequently approved and assigned that order; it is now in progress. Do not
change that order or its assignments. Earlier investigation treated the symptom
as a rejected completion; that timing assumption is superseded. The exact new
message still needs confirmation. The afternoon source fix remains present.
This checkpoint does not establish the production cause.

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

Two further tests in `backend/tests/test_review_hidden_option_runtime.py` use
the actual `order_review_image_modes` router wrapper and
`AcceptanceConfigDatabase` adapter, including the native card's controls GET
after fresh approval data. Both hiding size/Arabic-note fields and hiding then
showing those fields completed once with duplicate-request protection. These
two tests passed on final test source (2 tests, 10.761s, exit 0). The first run
also discovered 16 imported closure tests; all 18 passed in 95.768s. The import
was then changed to a module alias, and discovery was asserted to contain only
the two intended new tests. This remains synthetic coverage, not a reproduction
or proof that the reported defect is repaired.

## Remaining evidence

- Production UI showed the reported order pending review, but the retained
  runtime log window did not contain the original rejection.
- A fresh Emergent IDE tab restored a usable independent shell. A read-only
  search of available preview backend logs found no original conflict for the
  reported order. Preview logs are not production rejection evidence.
- A loopback-only local read-only connection attempt timed out before accessing
  Mongo metadata. No production database reads or writes succeeded via it; the
  helper was stopped and transient connection material cleared.
- A second read-only connection from the existing Emergent backend Python
  environment to the Live database also timed out before metadata queries.
  The client was closed, interpreter exited, and transient credential/clipboard
  contents cleared. No connection material was written into source or reports.
- Need the pre-approval save/refresh response and message, and any existing
  receipt that could explain the displayed state. If completion was actually
  sent, require its sanitized rejection/receipt evidence. Never infer non-commit
  from pending status alone.
- The installed emulator's fresh read of the selected order showed approval
  enabled and no additional specifications, unlike the supplied screenshot.
  No business action was executed; this does not establish that the original
  attempt recovered or that the defect is fixed.

Next: identify the exact message immediately after the export-controls PATCH
and its follow-up detail GET, then reproduce that pre-approval mechanism in an
isolated fixture. Do not assume that `/complete` was called. Restore a trusted
read-only path to server evidence when needed. Preserve signed
approval, exact receipt binding, unknown-outcome protection and current shipping
behavior. Do not include this WIP in the separate shipping APK47 release.

Production changed: **no**. No hide/review/closure/stock/financial writes, merge,
release lease, deployment or APK build performed by this investigation.

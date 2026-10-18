# WIP: review approval after hiding customer options

Status: investigation incomplete; no production-code fix and no deployment.

Base: `8d731ec92536e872fef830c2fcd995dde3046c82`.
Branch: `codex/review-hidden-option-server-20261018`.

## Current evidence: authorized emulator reproduction

On 2026-10-18 the owner authorized hiding one named customer option and
approving one specific pending order in Android. No customer identifiers or
option values are retained in this repository. The emulator remained on
versionCode 46/versionName 1.0.31; the separate APK47 was not installed.

The owner corrected the earlier timing report: a disabled button while the
product editor is open is expected. The owner hid the target option and closed
the editor before the agent's approval action. A screenshot confirmed the
option was crossed out and the approval button was enabled. Therefore the
previous pre-approval failure theory below is superseded by this direct test.

1. First explicit confirmation at 2026-10-18T12:30:24.586Z returned
   `component_source_event_stale`, followed by the application's unresolved
   outcome notice. The agent sent no blind retry.
2. The explicit `update and verify review` action performed the existing exact
   attempt recovery. Fresh UI showed approval enabled with the option still
   hidden. This is consistent with the closure/readback contract; the raw
   server proof was not separately extracted from the non-debuggable device.
3. A new explicit confirmation after that recovery returned the same code.
   This demonstrates the initial error was not solved merely by fresh UI
   consent/recovery. It does not identify the server rejection stage.
4. Final explicit verification at 2026-10-18T12:44:42.784Z again recovered the
   button. No third approval was sent. The option remains hidden and the order
   was not reported as approved. No assignment or financial action followed.

The production View Logs modal exposed a cached 100-line window ending at
12:32:10, with no matching conflict. Closing/reopening did not refresh it.
Absence there is NOT evidence of absence in production. A Mongo viewer session
ended before a connection; subsequent attempts did not open a new viewer.
No database read succeeded through that viewer, and connection material was
cleared. Local screenshots are evidence only and are not committed because
they contain customer data.

Root cause remains unproven: the same error code can arise from token/context
validation, approval fingerprint comparison, or component source lifecycle
validation. Do not call the current source fixed or weaken any of these guards.

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

## Earlier read-only evidence (before the authorized experiment)

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
- The earlier pre-approval timing theory was unproven. The authorized test
  above now establishes a completion rejection. Pending status alone still
  cannot prove non-commit.
- The installed emulator's fresh read of the selected order showed approval
  enabled and no additional specifications, unlike the supplied screenshot.
  No business action was executed; this does not establish that the original
  attempt recovered or that the defect is fixed.

Next: obtain the sanitized production rejection stage and approved/current
input evidence for the reproduced attempt, or reproduce the responsible
mechanism with synthetic data. The generic code alone cannot distinguish the
approval and component-lifecycle branches. Preserve signed approval, exact
receipt binding, unknown-outcome protection and current shipping behavior.
Do not include this WIP in the separate shipping APK47 release.

Production source/deployment changed by this task: **no**. Production business
interaction: **yes**, only the specifically authorized order's two explicit
approval attempts and exact-attempt verification/recovery actions described
above. The owner performed the hide. No successful approval was observed;
no assignments, financial writes, release lease, merge, deployment, or APK
build were performed by this investigation. Earlier read-only checkpoints
remain historical and do not describe this newly authorized experiment.

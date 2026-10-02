# Independent attribution-fixture audit evidence

Historical child-audit scope: old B `e030b737ca50adb03f37b06dab2a5624d79474fa` and CodeQL candidate `551bd3d51b0cf33f5f34a8524495216bf56cc0de`.

The parent subsequently executed `run_final_probe.py` against the untouched old-B archive and the exact final candidate `d6c0553ae6a99a85d52b03871b7bf64024180514` (TREE `806e935c8ecc3268f98096cd2b477050d235c99e`). Both success/failure enrichment cases passed on both sources, exit0. `exact-final-probe.log.gz` retains the complete new stdout losslessly; the manifest hashes the compressed artifact. Source fixtures/assertions remain unchanged.

This directory distinguishes the previously captured child-audit output from the later parent-owned exact-final-source probe. No further process was launched by the child after execution ownership was reserved.

## Retained successful probe

- `probe.py`: exact successful child probe body, with descriptive header added.
- `run_probe.py`: reproduces the sanitized parent invocation; not executed after retention.
- `captured-probe.stdout.txt`: successful stdout captured from tool chunk `aaf8b7` (3.379 seconds, process exit0), copied from the tool result. This is captured prior output, not a claim of a new run.
- `captured-membership-and-blobs.json`: prior command result values and inspected selector locations.

Reproducer (parent-owned execution only):

```powershell
& 'C:/Users/amasi/mz2-final-integration-owner-20261001/.venv/Scripts/python.exe' -B 'D:/CodexAcceptance/mz2-b2-fixture-audit-20261002/run_probe.py'
```

The probe uses actual tenant resolution, mapping, upsert, canonical source-snapshot persistence and post-persist attribution handling. The operational transaction capability and shipping financial observer are mocked. There is no atomicity or financial-posting claim. After asyncio has created its Windows self-pipe, socket connections are denied. Environment credentials are excluded, dotenv is disabled and only synthetic identifiers are used.

Before the successful probe, two local harness setup attempts failed: an over-early socket denial blocked asyncio's Windows self-pipe; then only the original module symbol was patched while fulfillment's imported transaction alias remained real and mongomock lacked command support. Neither ran a Production call or showed an application regression. The retained successful probe corrects the probe's dependency substitutions; no repository test or source was edited.

## Original failing-test reproduction evidence

The original tests remain unchanged and failing. Existing raw evidence is retained in the security artifact store:

Root: `C:/Users/amasi/.codex/state/plugins/codex-security/scans/mz2-final-release-1240-1241-20261002/artifacts-747f72c02f8244482939bd04dcd29e8bade9777a59c1140e36b4afb07ce07dad/artifacts/`

- `baseline.log` / `baseline.xml`: original-B isolated archive, 2 failed.
- `focused.log` / `focused.xml`: current CodeQL source broad selection, 84 passed and the same 2 failed.

Original baseline executor source is also available at:
`C:/Users/amasi/AppData/Local/Temp/codex-security-artifacts-742e3d7504381c2d1bcbab5139791fc8856c05c3d3cdd9de0aa987463eb6c6a1/artifacts/check_baseline.py`.

Its command, run from an untouched `git archive e030b737ca50adb03f37b06dab2a5624d79474fa backend` extraction under sanitized environment, was:

```text
<venv-python> -B -m pytest --noconftest -p no:cacheprovider -q --tb=short --showlocals -o asyncio_mode=auto --junitxml=<baseline.xml> backend/tests/test_salla_webhook_attribution_ledger_bridge.py
```

Current focused executor `run_focused.py` (same temporary artifact directory) used:

```text
<venv-python> -B -m pytest --noconftest -p no:cacheprovider -q --tb=short -o asyncio_mode=auto --junitxml=<focused.xml> backend/tests/test_salla_current_shipping.py backend/tests/test_salla_demo_store_isolation.py backend/tests/test_salla_webhook_attribution_ledger_bridge.py backend/tests/test_salla_abandoned_carts_v1.py backend/tests/test_snapchat_capi_purchases_v3.py backend/tests/test_snapchat_capi_cancellation_v3.py
```

Both executors use an OS environment allowlist, `PYTHON_DOTENV_DISABLED=1`, no inherited production credentials, synthetic JWT and loopback-only `MONGO_URL=mongodb://127.0.0.1:1`.

Exact baseline failure details inspected:

1. `test_verified_order_webhook_refreshes_attribution_ledger`: fixture replaces all of fulfillment_v2_routes with a SimpleNamespace exposing only auto_route_instant_order. Import of persist_component_source_snapshot at webhook_order_sync.py:424 fails before attribution. Result reason=`order_webhook_persist_failed`, error=`cannot import name 'persist_component_source_snapshot' from '<unknown module name>' (unknown location)`. ledger_calls=[]; original assertion at test line102 fails.
2. `test_ledger_failure_never_blocks_salla_order_ingestion`: object() database lacks command() required by operational_owner at operational_atomic.py:438. Result reason=`order_webhook_persist_failed`, error=`'object' object has no attribute 'command'`. Original assertion at test line146 fails before the throwing attribution mock is called.

Both tests are outside the declared154-file native/operational Release Gate selection and the inspected CI selectors. They are not globally ignored or deleted. An unrestricted whole-repository test run must still report them as failures. Existing source files and test blobs are equal oldB/currentB2. Successful independent probes validate the intended enrichment boundary on both sources, without relabeling the original tests PASS.

## Runtime source trace

server.py:193-198 creates the real Motor DB; routes.py:357 passes it to dispatch after webhook verification; easy_mode_webhook.py:444 passes it to capture; capture calls sync_order_from_verified_webhook. Runtime does not supply object() or replace fulfillment_v2_routes with the test's incomplete SimpleNamespace.

webhook_order_sync.py:424-438 completes canonical snapshot persistence first. Attribution at441-459 catches failures and returns ledger_bridge_unavailable without undoing canonical ingestion. Real-Mongo regression coverage of operational ingestion includes test_g47_operational_boundary.py:336, test_real_verified_webhook_paused_unconfigured_ingests_and_replays_without_finance.

No source or test file was changed by this audit. Production accesses/mutations=0. No release commands were run.

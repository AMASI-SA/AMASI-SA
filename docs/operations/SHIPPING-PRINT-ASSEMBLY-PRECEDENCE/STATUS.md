# Assembly completion precedence for PR #1302

Candidate only; no Merge, Prepare, Prepublish, Deploy or Production test.
Production writes: 0.

Branch: `codex/shipping-print-read-only`.
Previous reviewed HEAD: `973fa9b6cdbcb00ca2da12c43fbadb3b4661fc8c`.
Production base: `128bfc1c2b4670d853a8bce33f15a667d2fdb120`.

## Contract

The completed-fulfillment refresh endpoint checks authorization, merchant/order
identity and durable local `assembly_status == "completed"`. It does not require
the current workflow stage to remain `completed`, nor inspect Salla status as an
execution gate. Missing, foreign or malformed completion proof fails before Salla.
Print confirmation, a timestamp or ready piece totals alone are not proof.

The assembly search adds a read-only `assembly_completion_confirmed` projection.
The assembly card uses that proof to open/reopen the current label regardless of
`history_only` or `print_confirmed`. Confirmation retains its existing restrictions.
The original issue guard, mark-ready, inventory, execution eligibility and delivery
transitions are unchanged. No migration or historical workflow rewrite is needed.

Provider current-label selection, identity validation, unavailable/ambiguous-label
failure and the store courier formatter remain the reviewed PR-A implementation.
There are no new issue, resync, snapshot, workflow or Salla writes in printing.

## Fresh functional evidence

- Frontend: 50 PASS / 0 SKIP / 0 FAIL (assembly card, completed orders, formatter).
- Real MongoDB 8.0.12, loopback replica set `shippingfulfillment`, PRIMARY:
  77 PASS / 0 SKIP / 0 FAIL, 77 memory cases deselected. Command:
  `python -B -m pytest --noconftest --asyncio-mode=auto -q -ra backend/tests/test_shipping_print_legacy_path.py backend/tests/test_shipping_print_boundary.py -k mongo`.
- Broader shipping/fulfillment/preparation/handoff regression: 205 PASS, one
  memory-only rollback SKIP (not a pass); its real Mongo counterpart passes above.
- Replacing the print guard in memory with the original stage guard causes all
  12 selected later-stage cases to fail, as expected. No source file was swapped.
- Read-only route tests compare every database collection before/after, require
  GET-only synthetic Salla transport and fail on issue/resync/persistence paths.
- Initial backend test invocation without asyncio auto mode failed fixture setup;
  no product result was inferred from it. Correct invocation passed.

Exact-HEAD governed CI and official shipping boundary are pending this checkpoint.
The boundary workflow now runs the current-label/assembly precedence suite on its
real Mongo replica set. Conditional skipped CI jobs must not be called passes.

## Rollback

Revert this correction commit to restore the previous PR-A assembly print gates;
this also restores the known later-stage reprint restriction. No data rollback,
backfill or Salla action is required. Do not merge or deploy without approval.

Next: inspect exact-HEAD CI, source/build-meta/reproducibility evidence and record
the final HEAD/TREE and conclusions in PR #1302 and continuity Issue #1006.

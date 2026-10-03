# Build37 operational root fix — source checkpoint, NOT a release

Base: `8a9c35fd27e256d690831fc19ce649e079cc04ca`.
Branch: `codex/build37-operational-root-fix`.
Mobile companion: `AMASI-SA/amasi-mobile`, `codex/build37-history-isolation`.

## Proven mechanisms and invariants (recorded before release)

- `_recent_session_events` wrote piece services before later price calculation
  could fail. A synthetic failing-price fixture demonstrated persisted partial
  state. Stage service updates until all draft computation succeeds; refresh
  uses a Mongo transaction with an actor-authorized open-session write to
  conflict with concurrent refresh/close. Close already supplies a transaction.
- `_supplier_product_reference_price` skipped a selected direct option binding
  with missing amount. A regression fixture demonstrated the understated price.
  Reject it explicitly; never silently charge zero for a missing option cost.
- `_supplier_live_piece_services` could drop a still-linked missing resource.
  Validate selected resource references using the same selection semantics as
  service inheritance (including size/color), before deriving or writing services.
- Invalid option snapshots could raise AttributeError, or an empty list could
  silently become empty options. Explicit 422, preserving missing/null/dict.
  Invalid list inputs are NOT evidence for the actual Production outage.

These fixes preserve live Product V2 sync, piece IDs, option costs, service
separation, grouping, owner/employee authorization, and all accounting writers,
write controls, component stale guards and delivery protections. No legacy
price fallback was introduced. Existing missing product/base/variant cost
completeness contracts are not silently upgraded to complete prices.

## Shipping and assignment

Shipping runtime remains unchanged. The stale guard predates Build37; the actual
order's stored shipping identity is unavailable. Added regression tests cover
same identity, current printed label, changed carrier, concurrent baselines,
changed AWB, and issue without a current label, alongside existing replacement
and stale webhook tests. No guessed fix to the guard.

Assignment runtime remains unchanged. Supplier management and dispatch share
`mezan_suppliers_v2`; dispatch additionally requires non-inactive status and
`service_ids.0`. Exact Production supplier fields/responses remain unavailable.
No supplier mutation or eligibility bypass was performed.

## Verification checkpoint

- Before fixes: 7 failed / 3 passed in supplier refresh safety regressions.
- Additional size-selected missing resource regression failed before alignment.
- After fixes: 11 supplier refresh safety tests pass.
- Shipping + initial supplier safety focused run: 74 passed (before added size case).
- Receiving/service/dispatch compatibility focused run: 85 passed.
- Real Mongo 8.0.12 isolated loopback replica set: 5 passed, including transaction
  rollback, repeated/concurrent refresh, empty session, actor isolation and closed session.
- Broader Track F/accounting/native invoice/history suite: pending checkpoint.
- Remote CI/security/CodeQL/G47/full checks: pending; not a release readiness claim.

## Safety and next steps

No Production requests or mutations, no deployment, rollback, APK, startup,
recovery, backfill, Opening or Activation. Historical recovery exception B
remains; historical Production financial writes are NOT PROVEN ZERO.
This task's Production financial writes are zero.

Next: run exact-head CI, resolve evidence-backed failures, review remaining
unproven actual shipping/assignment incidents. Do not prepare/publish. A future
Backend release needs a new protocol-v5 source/Intent and explicit authorization;
startup recovery identity risk remains unresolved and must not be bypassed.

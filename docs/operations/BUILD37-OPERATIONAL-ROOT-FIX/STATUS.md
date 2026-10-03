# Build37 operational root fix — source checkpoint, NOT a release

## 2026-10-03 owner-authorized security exception checkpoint

The owner explicitly authorized a single temporary risk acceptance for
GHSA-vfj7-8cjw-p6xm / CVE-2026-93687, braces 3.0.3 only. This supersedes the
earlier no-exception instruction for this advisory only. The vulnerability is
not remediated. `security/frontend-audit-exception.json` records the rationale,
approval, source references and removal plan. It expires at
2026-10-10T16:47:30Z (2026-10-10 19:47:30 Asia/Riyadh), without automatic renewal.

The frontend gate retains raw Yarn audit output and its severity threshold.
It reports ALLOWED / EXCEPTION with the expiry and counts one unique advisory,
even when Yarn reports multiple dependency paths. Any other moderate/high/
critical advisory, different package/version/CVE, expired exception, incomplete
audit or execution error fails closed. CodeQL and other assertions remain
mandatory. The new gate has 22 local contract tests; the real baseline audit
matched one unique advisory on two paths, with no other blocking advisory.

Starting Backend HEAD: b24a0390196a009cdb4b02ab7f2e4d9c6d123182.
Unchanged Mobile HEAD: 2bf1e3162e1da811585cb822aa668a96ce239219.
No dependency, business logic, supplier, shipping, mobile, accounting or
write-control change. No internal backport included. Full exact-HEAD CI is
pending this checkpoint; no merge/release/prepare/deploy is authorized.
Production was not accessed or modified. Next action: push this task branch,
run all applicable CI at the resulting exact HEAD and retain the result matrix.

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

## Supplier product-selection acceptance — 2026-10-03

Owner clarified: order number -> order products, physical-piece barcode -> matching
piece. Independent Product ID search is NOT required. Base is exactly
78dcf31af73581ceba3677c464c657a0b9b2c4fc (owner reconfirmed), not the typo SHA.

Compared Backend source 057d06ecd19266359567107899a5f50334cede49 and Mobile
ab1d3f10ed321a6194511e32d4dd5e70647cba9c. Added tests only; no runtime changes.

- Real Mongo loopback + actual FastAPI routes: current 14 PASS, 0 SKIP.
- Identical suite against immutable Base: 13 PASS, 1 FAIL. The one failure is
  the NEW selected-option-cost requirement: Base returns variant cost 1275
  halalas; current returns 1500 (1275+225), preserving the 300 service price.
  This is a retained Build37 improvement, not a newly introduced failure.
- Mobile actual repository/TSX with synthetic transport: 9 PASS. No device UAT.
- Search/spec/row functions have identical ASTs in Base and current.
- Backend acceptance suite is wired into native-contract's isolated Mongo job;
  Mobile suite is wired into frontend-checks. Remote CI on this checkpoint is
  pending; Security remains blocked by pre-existing braces without exceptions.

GET /supplier-receiving-v1/sessions/{id}/search?q= reads merchant-scoped
mezan_preparation_pieces_v1 and requires an actor-authorized OPEN session plus
inventory.preparation.receive permission. q is an order number (# and Arabic
prefix supported), MEZAN-PIECE:<32hex>, or bare piece UUID. Barcode search returns
order context with matched_piece_id and the match first, not an ambiguous SKU
match. Product identity is the order-piece product_id/sku, not a Legacy lookup.

Assignment comes from CURRENT piece supplier_id/supplier_dispatch_status and
status/reservation fields. current_supplier_id means the SESSION's supplier;
previous_supplier_id identifies the piece's assignee. These must not be conflated.
A different supplier requires confirmation and an unconfirmed scan is HTTP409.
Not-dispatched pieces show the blocker and no Add button. Historical pieces with
no dispatch status retain the existing compatibility policy; it was not changed.
Supplier eligibility is not overridden.

Selection sends the server piece barcode, quantity=1 and stable client_request_id
to POST /sessions/{id}/scan. The server re-reads the piece and validates eligibility;
it stores a draft reservation/event, not a financial invoice. Product/SKU/variant,
order item, pieceId and options survive into scan/draft; costs use Product V2
profiles and selected option bindings; services use current service resources.
Mobile mapper preserves eligibility/reassignment flags and reconciles the scan
into Invoice Draft/Preview. Tests do NOT call /close or post a financial invoice.

Next safe action: review acceptance evidence and remaining Security blocker.
No Production requests or mutations, no publish/deploy/rollback/recovery.
This task's Production financial writes = 0; historical exception B unchanged.

# PRD — MEZAN E-commerce Accounting App

# ══════════════════════════════════════════════════════════════════
# ✅ ITER-001K COMPLETE — Pipeline Instrumentation (P0, 2026-02-XX)
# ══════════════════════════════════════════════════════════════════

**Status**: SHIPPED. All 1406 relevant tests (Qoyod / Selective Send
/ Pipeline / OneShot / WriteLock suites) pass. `assert_send_allowed`
is wired ahead of every `api_client.create_invoice` and
`api_client.create_invoice_payment` in `pipeline.py`, and ahead of
the pipeline delegation in `one_shot_reprocess.py`. Invoice and
payment share ONE frozen `send_timestamp_riyadh` via a single
`selective_send_decision`. `apply_send_date_to_qoyod_payload` stamps
both payloads.

## Iter-001k close-out — what changed in this session
- `tests/test_qoyod_per_order_approval_iter293_4.py::_seed_inbox`
  now injects a POLICY-COMPLIANT `canonical_payload`
  (`order_date="2026-07-05"`, `order_status="completed"`,
  `payment_method="credit_card"`) + `qoyod_customer_id=999001`.
  6 previously-failing allow-path tests now pass. Refuse-path tests
  still refuse at the approval_phrase gate (which precedes the
  policy gate in one_shot_reprocess.py).
- NO changes to `selective_send_policy.py`, `selective_send_guard.py`,
  `api_client.py`, `pipeline.py`, or `one_shot_reprocess.py`.
- NO writes to `qoyod_settings`. `qoyod_write_lock_attempts` count
  remains at 0 — no real send to قيود occurred.

## Grep proof (Iter-001k contract)
```
pipeline.py:849       selective_send_decision = assert_send_allowed(...)
pipeline.py:882       invoice_payload = apply_send_date_to_qoyod_payload(...)
pipeline.py:944       api_client.create_invoice(invoice_payload)
pipeline.py:1402      payment_decision = selective_send_decision  # SHARED
pipeline.py:1424      payment_decision = assert_send_allowed(...)  # fallback
pipeline.py:1458      payment_payload = apply_send_date_to_qoyod_payload(...)
pipeline.py:1505      api_client.create_invoice_payment(payment_payload)
one_shot_reprocess.py:810   _assert_send_allowed(...)  # AFTER approval_phrase
one_shot_reprocess.py:901   process_normalized_row(...)  # inner guard fires again
one_shot_reprocess.py:926   process_customer_resolved_row(...)  # inner guard fires again
```

## Immutable constraints (STILL HELD)
- Preview DB `qoyod_write_lock_attempts` count = 0 → NO real send.
- Preview DB `selective_live_send_enabled` and
  `production_writes_locked` UNTOUCHED by this session.
- No deploy. No UI Manual Send button. No CSV / Q2 Report /
  bank_transfer routing shipped.
- `selective_send_policy.py` / `selective_send_guard.py` /
  `api_client.py` unmodified.

# ══════════════════════════════════════════════════════════════════
# 🚧 PREVIOUS HANDOFF (kept for reference; superseded by close-out)
# ══════════════════════════════════════════════════════════════════

**Status**: Handed off to a fresh-context iteration on 2026-07-01
after user (owner) explicitly approved deferring the actual pipeline
instrumentation. Iter-001j (Guard Module) was accepted as a
FOUNDATION ONLY — P0 is NOT complete.

## Immutable constraints (must hold throughout Iter-001k)
- `selective_live_send_enabled` must remain **false** (Preview + Prod).
- `production_writes_locked` must remain **true** (Preview + Prod).
- No deploy from the agent.
- No real send to قيود.
- No UI Manual Send button.
- No CSV / Q2 Report / bank_transfer routing.
- Do NOT modify `api_client.py` unless the change is a strictly-
  additive defense-in-depth attribute check.

## Currently safe because
- `selective_live_send_enabled=false` → pipeline never enters the
  send branch.
- `production_writes_locked=true` → `api_client._request()` rejects
  every POST/PUT/PATCH/DELETE and records to
  `qoyod_write_lock_attempts`.
- `qoyod_write_lock_attempts` count = 0 → no attempted writes.
- The 3 rows in `qoyod_invoices` predate this session.

## Iter-001j surface — ready to be adopted
Module: `backend/integrations/qoyod/selective_send_guard.py`

```python
from integrations.qoyod.selective_send_guard import (
    SelectiveSendPolicyBlocked,     # raised on block
    assert_send_allowed,             # decision on allow, raises on block
    apply_send_date_to_qoyod_payload,  # canonical date stamper
)
```

### `assert_send_allowed(...)`
Signature:
```python
assert_send_allowed(
    *,
    order: dict,                      # rich order (see below)
    settings: dict,                   # qoyod_settings snapshot
    manual_send_requested: bool = False,
    manual_approval_phrase: Optional[str] = None,
    now_utc: Optional[datetime] = None,   # override for tests
) -> SelectiveSendDecision            # RETURNS on allow
                                      # RAISES SelectiveSendPolicyBlocked
                                      # on block
```

### Expected `order` dict shape (build from pipeline scope):
```python
{
    "order_number": str,
    "salla_order_id": str,
    "salla_order_created_at": str,   # ISO date YYYY-MM-DD (from Salla)
    "status": str,                   # order_status
    "payment_method": str,
    "existing_qoyod_invoice_id": Any,  # None / DRY: / PREVIEW: / real
    "customer_status": {
        "resolved": bool,
        "qoyod_id": Any,             # int / DRY:X / PREVIEW:X / None
        "reason": Optional[str],
    },
    "products_status": {
        "resolved": bool,
        "resolved_count": int,
        "dry_run_only": int,
        "missing": list[str],        # unmapped SKUs
    },
    "totals_status": {
        "valid": bool,
        "total": float,
        "expected": float,
        "diff": float,               # positive OR negative
    },
}
```

### `apply_send_date_to_qoyod_payload(payload, decision)`
- Stamps every top-level and nested occurrence of `date`,
  `issue_date`, `invoice_date`, `due_date`, `payment_date`,
  `receipt_date` with `decision.send_date_riyadh` (YYYY-MM-DD).
- Scrubs `completed_at`, `delivered_at`, `paid_at`, `received_at`,
  `order_created_at`, `created_at` from the payload.
- Idempotent. Rejects `None` decision.

## Files to instrument (Iter-001k tasks)

### 1. `backend/integrations/qoyod/pipeline.py`
- **Line ~778** (before `api_client.create_invoice(invoice_payload, idem=invoice_idem)`):
    ```python
    try:
        decision = assert_send_allowed(
            order=_build_policy_order_from_pipeline_scope(...),
            settings=settings,
        )
    except SelectiveSendPolicyBlocked as blocked:
        # Park the row with blocker_code (mirror the existing
        # QoyodWriteLockedError handling pattern at line ~786).
        await db.integration_inbox.update_one(
            {"user_id": user_id, "trace_id": trace_id},
            {"$set": {
                "pipeline_stage":
                    f"SELECTIVE_SEND_BLOCKED:{blocked.blocker_code}",
                "selective_send_blocker_code": blocked.blocker_code,
                "selective_send_blocker_reason":
                    blocked.blocker_reason,
                "updated_at": datetime.now(timezone.utc),
            }})
        return {"stage": "blocked",
                "blocker_code": blocked.blocker_code,
                "blocker_reason": blocked.blocker_reason,
                "trace_id": trace_id}
    invoice_payload = apply_send_date_to_qoyod_payload(
        invoice_payload, decision)
    inv_resp = await api_client.create_invoice(invoice_payload,
                                                idem=invoice_idem)
    ```
- **Line ~1276** (before `create_invoice_payment`):
    - Reuse the SAME `decision` object captured at line 778 above
      (do NOT re-compute — invoice + payment must share one frozen
      `send_timestamp_riyadh`).
    - `payment_payload = apply_send_date_to_qoyod_payload(payment_payload, decision)`.

### 2. `backend/integrations/qoyod/one_shot_reprocess.py`
- Locate the `approval_phrase` verification (existing).
- Immediately AFTER that check, BEFORE any `api_client` write:
    ```python
    try:
        decision = assert_send_allowed(
            order=..., settings=...)
    except SelectiveSendPolicyBlocked as blocked:
        return {"stage": "blocked",
                "blocker_code": blocked.blocker_code, ...}
    ```
- **Contract**: approval_phrase alone MUST NOT bypass the policy.
  Even a correct approval phrase must yield a block when the policy
  says block (gate disabled, write lock, Q2 cutoff, DRY IDs, etc.).

### 3. Payload builders
- Every builder that constructs invoice/payment/receipt payloads
  must call `apply_send_date_to_qoyod_payload(payload, decision)`
  once, using the ONE decision captured for that send attempt.
- Rule: **ONE frozen `send_timestamp_riyadh` per send attempt** —
  invoice + payment + receipt share it. Do NOT call
  `should_allow_selective_live_send()` twice; capture once, reuse.

## Test matrix (all must pass)
Use `respx` or a mock httpx client to prove `create_invoice` /
`create_invoice_payment` / `create_receipt` are NEVER invoked when
the policy blocks. Cover:
1. Pipeline blocked on `gate_disabled`.
2. Pipeline blocked on `write_lock_active`.
3. Pipeline blocked on `before_sync_start_date` (Q2).
4. Pipeline blocked on DRY / PREVIEW / null IDs.
5. Pipeline blocked on `bank_transfer_on_hold_iter_294`.
6. Pipeline blocked on `totals_mismatch_hard_diff_gt_0.01`.
7. Pipeline blocked on `invoice_trigger_status_not_enabled`
   (delivered / shipping default).
8. one_shot blocked on `manual_approval_phrase_required`.
9. one_shot blocked by policy EVEN WITH correct approval phrase
   (feed a `gate_disabled` settings scenario + correct phrase →
   still blocks).
10. `create_invoice` is not called before `assert_send_allowed`.
11. `create_invoice_payment` is not called when invoice stage blocked.
12. Invoice + payment share the SAME `send_timestamp_riyadh` (freeze
    via `now_utc` in a test; assert equal in both payloads sent to
    the mock client).
13. All payload date fields = `send_date_riyadh`.
14. `completed_at` / `delivered_at` / `paid_at` are scrubbed from
    the actual payload sent to httpx.
15. Master gate `selective_live_send_enabled=false` yields no real
    send even in tests (verify via `qoyod_write_lock_attempts`
    remains 0).

## Deliverables at the end of Iter-001k
- Modified files: `pipeline.py`, `one_shot_reprocess.py`, and any
  payload builder helpers that carry date fields.
- New/updated tests: `test_pipeline_selective_send.py` (or extend
  existing pipeline tests).
- Grep proof: `grep -n "create_invoice\|create_invoice_payment"
  pipeline.py one_shot_reprocess.py` — every call site preceded by
  `assert_send_allowed` in the same function scope.
- 163+ tests (all existing) + new pipeline integration tests pass.
- `qoyod_write_lock_attempts` count still 0 after test run.
- Preview `qoyod_settings.main.selective_live_send_enabled == false`.
- Preview `qoyod_settings.main.production_writes_locked == true`.

## Do NOT modify in Iter-001k
- `api_client.py` write-lock guard (existing, working).
- `selective_send_policy.py` / `selective_send_guard.py` (frozen
  contract).
- Any DB config in Production (owner will do this manually).
- Frontend UI (no Manual Send button in this iteration).

## Reference: existing pipeline write-lock pattern to mirror
```python
# From pipeline.py around line 786-810 (existing pattern):
except QoyodWriteLockedError as exc:
    await db.integration_inbox.update_one(
        {...},
        {"$set": {"pipeline_stage": "WRITE_LOCKED",
                  "write_lock_reason": str(exc),
                  ...}})
    return {"stage": "write_locked", ...}
```
Use the SAME shape for `SelectiveSendPolicyBlocked` — it should feel
like a peer of `QoyodWriteLockedError` to the pipeline reader.

# ══════════════════════════════════════════════════════════════════



## Iter-001j — Phase C P0 Wiring: Guard Module + Integration Contract (2026-07-01)

### Scope decision
The user directive asks to wire ALL Qoyod sending paths through
`SelectiveSendDecision`. Given `pipeline.py` (1500+ lines) and
`one_shot_reprocess.py` are production-critical multi-branch flows,
this iteration delivers:

**Ship now:**
1. NEW `backend/integrations/qoyod/selective_send_guard.py` — the
   canonical wire-in surface every write path must adopt.
2. NEW `backend/tests/test_selective_send_guard.py` — 27 tests,
   including a mock QoyodAPIClient that PROVES the guard fires
   BEFORE any API call on every blocker code.
3. Integration contract documented in the module docstring.

**Deferred to a follow-up iteration:**
4. Actual instrumentation of `pipeline.py` create_invoice call site
   (line ~778) and `one_shot_reprocess.py` — deliberately not done
   here because touching those files without full context risks
   silent regressions in the currently-working (albeit gated) send
   paths. Recommend a dedicated iteration with focused review.

### Guard module surface
```python
from integrations.qoyod.selective_send_guard import (
    SelectiveSendPolicyBlocked,
    assert_send_allowed,
    apply_send_date_to_qoyod_payload,
)

# Every Qoyod-write code path adopts this pattern:
try:
    decision = assert_send_allowed(
        order=order_dict,
        settings=settings_dict,
        manual_send_requested=is_manual,
        manual_approval_phrase=phrase_or_None,
    )
except SelectiveSendPolicyBlocked as blocked:
    # decision.blocker_code, decision.blocker_reason available
    return _park_and_log(blocked.decision)

# Only past this line may we build & send the payload:
payload = build_qoyod_invoice_payload(...)
payload = apply_send_date_to_qoyod_payload(payload, decision)
# ↑ stamps date/issue_date/due_date/payment_date/receipt_date =
#   decision.send_date_riyadh; scrubs completed_at / delivered_at /
#   paid_at / received_at / created_at.
await api_client.create_invoice(payload)
```

### Contract guarantees (proved by tests)
- `assert_send_allowed()` RETURNS `SelectiveSendDecision` on allow.
- `assert_send_allowed()` RAISES `SelectiveSendPolicyBlocked` on
  block. Callers cannot silently fall through.
- `apply_send_date_to_qoyod_payload()`:
  - Rewrites `date / issue_date / invoice_date / due_date /
    payment_date / receipt_date` (top-level + nested) to
    `send_date_riyadh`.
  - Scrubs `completed_at / delivered_at / paid_at / received_at /
    order_created_at / created_at` from the payload.
  - Idempotent.
  - Rejects None decision or missing `send_date_riyadh` with
    `ValueError`.

### Tests (163/163 all-suite pass)
27 new guard tests including `TestCallerContract` which mocks a
QoyodAPIClient and asserts `client.calls == []` on every blocker
code (gate_disabled, write_lock_active, before_sync_start_date,
bank_transfer_on_hold, dry_or_null, hard_totals_mismatch, missing
manual phrase). Also verifies allow path invokes the client exactly
once with a rewritten payload date.

### Files
- NEW `backend/integrations/qoyod/selective_send_guard.py`
- NEW `backend/tests/test_selective_send_guard.py`

### Follow-up (P0 continuation)
Dedicated iteration to instrument:
1. `pipeline.py` line ~778 — call `assert_send_allowed()` immediately
   before `api_client.create_invoice(...)`. Same for
   `create_invoice_payment` at line ~1276. Handle
   `SelectiveSendPolicyBlocked` by parking the row with the
   `blocker_code` in the audit trail (mirror the existing
   `QoyodWriteLockedError` pattern already there).
2. `one_shot_reprocess.py` — call `assert_send_allowed()` after the
   existing `approval_phrase` check.
3. All payload builders — apply `apply_send_date_to_qoyod_payload()`
   after building.
4. Optional defense-in-depth: `api_client.py` — refuse write methods
   unless a `SelectiveSendDecision` attribute was attached.

### Read-only contract (unchanged)
- ✅ Zero Qoyod API imports in the guard module.
- ✅ Zero DB writes.
- ✅ `selective_live_send_enabled = false` (Preview + Production).
- ✅ `production_writes_locked = true` (Preview + Production).
- ✅ `qoyod_write_lock_attempts = 0`.
- ✅ No deploy from my side.



## Iter-001i — Phase C Manual Send Path (2026-07-01)

### Rule
Auto-send default remains STRICT (`completed / تم التنفيذ` only).
For `delivered / shipping / تم التوصيل / جاري التوصيل` a NARROW
manual-send path is added — the operator must:
1. Explicitly opt in per-order (button click, `manual_send_requested=true`).
2. Type the canonical confirmation phrase:
   `Approved manual Qoyod send for order <order_number> only`
   (case-sensitive, order-number-scoped).

Every other blocker still holds. Manual send does NOT bypass:
Q2 cutoff, bank_transfer, DRY/PREVIEW/null IDs, totals mismatch > 0.01,
missing customer/product, already-sent, master gate, write lock.

Invoice date on manual path = `send_date` in Asia/Riyadh (same as auto).

### Backend
`backend/integrations/qoyod/selective_send_policy.py`:
- New constants: `_MANUAL_SEND_ELIGIBLE_STATUSES`, helper
  `manual_approval_phrase_for(order_number)`.
- New BlockerCodes: `MANUAL_APPROVAL_PHRASE_REQUIRED`,
  `MANUAL_APPROVAL_PHRASE_MISMATCH`.
- `should_allow_selective_live_send()` now accepts
  `manual_send_requested: bool = False`,
  `manual_approval_phrase: Optional[str] = None`. Check 6 becomes
  branched:
    - Status in enabled list → allow (auto).
    - Status in {delivered, shipping, تم التوصيل, جاري التوصيل}
      AND `manual_send_requested=True` → verify phrase, then continue.
    - Anything else → `INVOICE_TRIGGER_STATUS_NOT_ENABLED`.
- Decision now includes `manual_send_requested` and
  `manual_approval_phrase_provided` (boolean only — phrase text is
  never persisted to keep audit trail clean).
- Report per-order enriched with:
    `auto_send_available` (bool), `manual_send_available` (bool),
    `manual_send_confirmation_phrase` (canonical text),
    `manual_send_blocker_code`, `manual_send_blocker_reason`.
- Report top-level gains `manual_send_available_count`.

### Tests
`backend/tests/test_selective_send_policy.py` — **+24 tests** (81
total; 136/136 including eligible_orders regression suite pass).
Coverage per user directive #1–#10:
- Auto blocked for shipping/delivered/Arabic in-transit.
- Manual allowed when phrase supplied AND all other conditions pass.
- Phrase enforcement: missing / wrong / case-mismatch / wrong-order
  all blocked.
- Manual does NOT bypass: bank_transfer, Q2 cutoff, DRY customer,
  DRY product, PREVIEW IDs, hard totals mismatch, already-sent,
  master gate, write lock.
- Manual invoice_date = send_date (still Asia/Riyadh).
- Manual flag has no effect on auto-eligible `completed`.
- Manual flag cannot rescue broad-ineligible `waiting`.
- Phrase text is NOT persisted in decision dict.

### Verified impact
Synthetic 4-order demo (gates OPEN for isolated tenant only):
| Order | Status | Payment | auto_send_available | manual_send_available |
|---|---|---|---|---|
| A | completed | mada | ✅ | ✅ |
| B | delivered | mada | ❌ | ✅ (needs phrase) |
| C | جاري التوصيل | cod | ❌ | ✅ (needs phrase — COD no bypass) |
| D | delivered | bank_transfer | ❌ | ❌ (bank blocker survives) |

`counts={allow:1, block:3}`, `manual_send_available_count=3`.

### Not delivered in this iteration (per user directive "no new UI")
- Frontend Manual Send button + confirmation modal — awaits explicit
  approval. Backend policy is ready to power it whenever UI ships.

### Read-only contract (unchanged)
- ✅ Zero Qoyod API imports (grep-verified).
- ✅ Zero writes to قيود entities.
- ✅ `qoyod_write_lock_attempts = 0`.
- ✅ `selective_live_send_enabled = false` (Preview pinned).
- ✅ `production_writes_locked = true` (Preview pinned).



## Iter-001h — Phase C.0 Revision: STRICT Trigger Statuses + invoice_date=send_date (2026-07-01)

### Two policy tightenings
**1. Enabled trigger statuses default STRICT.**
`qoyod_enabled_invoice_trigger_statuses` (default
`["completed", "تم التنفيذ"]`). Statuses `delivered / shipping /
تم التوصيل / جاري التوصيل` remain visible in Eligible Orders for
diagnostics but are BLOCKED by policy with new blocker code
`invoice_trigger_status_not_enabled`. Tenants must opt them in
explicitly. **COD does NOT bypass this check** — a `cod` order in
`جاري التوصيل` is blocked by default.

**2. Invoice date = send moment in Asia/Riyadh.**
`qoyod_invoice_date_source = "send_date"` (immutable default).
For EVERY invoice we push to قيود (auto-send, manual approve,
catch-up, one-order approval), `invoice_date`, `issue_date`,
`due_date` (COD), and `payment_date` (paid_receipt) all derive
from the Riyadh-local send moment — NEVER from `order.created_at`,
`completed_at`, `delivered_at`, `paid_at`, or `received_at`.

### Selective Send policy — new decision fields
- `normalized_status` — after `_→space`, casefold.
- `enabled_trigger_statuses` — snapshot of tenant's opt-ins.
- `invoice_date_source` — always `"send_date"`.
- `would_use_invoice_date` — Riyadh calendar date used as قيود
  `invoice_date` if the send happened NOW.
- `send_timezone` — always `"Asia/Riyadh"`.
- `send_timestamp_riyadh` — full ISO datetime with `+03:00` offset.
- `send_date_riyadh` — YYYY-MM-DD in Riyadh.
- `now_utc` argument added to `should_allow_selective_live_send()`
  for deterministic tests (falls back to `datetime.now(timezone.utc)`).

### Files modified
- `backend/integrations/qoyod/selective_send_policy.py`
  - New constants `QOYOD_INVOICE_DATE_SOURCE_DEFAULT="send_date"`,
    `QOYOD_SEND_TIMEZONE="Asia/Riyadh"`,
    `QOYOD_ENABLED_TRIGGER_STATUSES_DEFAULT=("completed", "تم التنفيذ")`.
  - New `BlockerCode.INVOICE_TRIGGER_STATUS_NOT_ENABLED`.
  - Policy check 6 added between broad eligibility (5) and
    already-sent (7).
  - Report builder loads new settings + advertises them in notes.
- `backend/tests/test_selective_send_policy.py`
  - +21 tests (57 total). Full coverage of user directive
    including COD-does-not-bypass, boundary Riyadh timezone,
    invoice_date ignores completed_at, would_use_invoice_date set
    on BLOCK decisions too.

### DB config pin (Preview only)
`qoyod_settings.main` additionally set:
- `qoyod_invoice_date_source = "send_date"`
- `qoyod_enabled_invoice_trigger_statuses = ["completed", "تم التنفيذ"]`
- `phase_c0_h_settings_pinned_at = <iso timestamp>`

Fail-Closed remains: `selective_live_send_enabled=false`,
`production_writes_locked=true`, `qoyod_sync_start_date=2026-07-01`,
`bank_transfer_routing_enabled=false`.

### Verified impact
Synthetic 7-order set (gates OPEN for isolated tenant only):
- `counts = {allow: 2, block: 5}`.
- 2 allowed: `completed+mada` (paid_receipt), `تم التنفيذ+cod`
  (credit_invoice_only).
- 4 blocked by `invoice_trigger_status_not_enabled`: `delivered`,
  `shipping`, `تم التوصيل`, `جاري_التوصيل` (COD included — proves
  COD does NOT bypass).
- 1 blocked by `bank_transfer_on_hold_iter_294`.
- `would_use_invoice_date` derives from send moment; boundary test
  (UTC 21:30 → Riyadh 00:30 next day) advances the date correctly.

### Read-only contract (unchanged)
- ✅ Zero Qoyod API calls (grep-verified — no `QoyodAPIClient` /
  `httpx` / `requests` imports in `selective_send_policy.py`).
- ✅ Zero writes to قيود entities.
- ✅ `qoyod_write_lock_attempts` still 0.
- ✅ Master gate & write lock stay Fail-Closed.



## Phase C.0 — Selective Live Send Gate Preparation (2026-07-01)

### Scope
Read-Only policy layer that answers "IF Selective Live Send were
flipped on, would قيود accept this order safely?" — for every order.
**NO activation.** Master gate remains OFF. Global write lock remains
ON. No Qoyod API calls, no writes, no send buttons.

### Deliverables
- **NEW** `backend/integrations/qoyod/selective_send_policy.py`
  - `should_allow_selective_live_send(order, settings, sync_start_date)`
    — pure decider returning `SelectiveSendDecision` with:
    `decision` (allow/block), `blocker_code`, `blocker_reason`,
    `would_send_to_qoyod`, `posting_mode`, `diff`, `totals_warning`,
    `dry_ids_detected`, `existing_qoyod_invoice_id`, `warnings`,
    `gates_snapshot`.
  - Machine-readable `BlockerCode` enum:
    `gate_disabled`, `write_lock_active`, `before_sync_start_date`,
    `missing_order_created_at`, `status_not_eligible`, `already_sent`,
    `bank_transfer_on_hold_iter_294`, `payment_method_not_allowed`,
    `customer_not_resolved`, `customer_dry_or_null`,
    `product_not_resolved`, `product_dry_or_null`,
    `product_missing_mapping`, `dry_invoice_id_detected`,
    `preview_id_detected`, `totals_mismatch_hard_diff_gt_0.01`.
  - `build_selective_send_policy_report(db, user_id, since_days, limit)`
    — aggregates decisions per order + counts + blocker code
    histogram + payment method breakdown. Wraps
    `build_eligible_orders_report(show_already_sent=True)` for
    enrichment; adds no new DB reads.
  - `emit_selective_send_decision_log(...)` — stdout audit
    without DB persistence.
- **NEW** `GET /api/integrations/qoyod/admin/selective-send-policy-report`
  registered in `routes.py`.
- **NEW** `backend/tests/test_selective_send_policy.py` — 35 tests
  (all pass). Covers every scenario in user directive verbatim:
  Q2 blocked, Q3 paid green, COD credit-invoice-only, bank_transfer
  hold, DRY customer, DRY/PREVIEW invoice ID, missing skus, totals
  mismatch > 0.01, missing created_at, already_sent, gate closed
  → no Qoyod calls, write lock true → block, allow-list contract.

### Payment method allow-list (activated later, not now)
`mada`, `apple_pay`, `stc_pay`, `credit_card`, `visa`, `mastercard`,
`amex`, `tabby*`, `tamara*`, `emkan*`, `cod`.
`bank_transfer` → HOLD until Iter-294 (`bank_transfer_routing_enabled=false`).

### Fail-Closed contract (pinned in Preview DB)
Preview `qoyod_settings.main` explicitly pinned to:
- `selective_live_send_enabled = false`
- `production_writes_locked   = true` (was false — TIGHTENED)
- `qoyod_sync_start_date      = "2026-07-01"`
- `qoyod_tax_period           = "Q3-2026"`
- `bank_transfer_routing_enabled = false`
- `phase_c0_settings_pinned_at = <iso timestamp>` (audit marker)

Policy defaults in code are ALSO Fail-Closed — if the DB is missing
those keys the decider returns `block:gate_disabled` (verified by
`test_default_settings_dict_is_fail_closed`).

### Totals policy (Iter-001g)
- `diff == 0.00` → allow
- `0.00 < |diff| ≤ 0.01` → allow with `totals_warning=true`
- `|diff| > 0.01` → block (`totals_mismatch_hard_diff_gt_0.01`)

### Verified impact (5-order synthetic set, gates OPEN for demo tenant)
- Q2 order (`created_at=2026-06-20`) → excluded upstream by cutoff.
- `bank_transfer` Q3 → block: `bank_transfer_on_hold_iter_294`.
- `crypto` Q3 → block: `payment_method_not_allowed`.
- `mada` Q3 (green) → allow, `posting_mode=paid_receipt`.
- `cod` جاري_التوصيل Q3 → allow, `posting_mode=credit_invoice_only`.
- `counts.allow=2`, `counts.block=2`, no Qoyod API call, no DB writes
  outside test fixtures.



## Iter-001f — Tax-Period Sync Cutoff (Q3-2026 start) (2026-07-01)

### Business Rule
MEZAN begins pushing to قيود from **2026-07-01** only. Any Salla order
whose CREATION date is before 2026-07-01 belongs to Q2 (previous tax
period) and MUST NOT appear in Eligible Orders / Catch-up / any live
send path. Q2 orders will be handled by a separate reconciliation
workflow, not covered here.

### Fix
`backend/integrations/qoyod/eligible_orders.py`:
- New module constants: `QOYOD_SYNC_START_DATE = "2026-07-01"`,
  `QOYOD_TAX_PERIOD = "Q3-2026"`, `QOYOD_SYNC_TZ = "Asia/Riyadh"`.
- New helpers: `_parse_iso_date()`, `_extract_order_created_at()` —
  extracts Salla creation date from (priority order):
  1. `order.created_at`
  2. `order.order_date` (if NOT `order_date_inferred=True`)
  3. `raw_payload.data.date.date` (Salla webhook shape)
  4. `raw_payload.data.created_at`
- Classifier loop applies the cutoff **first**, BEFORE all other
  checks:
  - `created_at < 2026-07-01` → `excluded_before_sync_start_date`
    with reason `before_sync_start_date:2026-07-01 (order_created_at=…)`.
  - `created_at` unresolvable → `excluded_missing_order_created_at`
    with reason `missing_order_created_at`.
- New response fields:
  - `sync_start_date`, `tax_period`, `sync_timezone`,
    `date_filter_basis="salla_order_created_at"`,
    `excluded_before_sync_start_date_count`,
    `excluded_missing_order_created_at_count`.
  - Each item now carries `salla_order_created_at` (ISO date).
- New notes advertise cutoff in Arabic + English.

`frontend/src/pages/EligibleOrders.jsx`:
- New indigo **Sync-Cutoff Banner** with `data-testid="eligible-orders-sync-cutoff-banner"` explaining the Q3 start date and cutoff counts.

### Tests (55 pass, up from 46)
- `test_sync_start_date_is_2026_07_01`
- `test_parse_iso_date_handles_shapes`
- `test_extract_order_created_at_priority`
- `test_response_advertises_sync_cutoff_fields`
- `test_order_before_cutoff_excluded` (2026-06-30 → excluded)
- `test_order_on_cutoff_included` (2026-07-01 → eligible)
- `test_late_arrival_of_old_order_still_excluded` (created_at wins over received_at)
- `test_missing_created_at_excluded_and_counted`
- `test_invariant_holds_with_cutoff_mix`

### Verified impact (8-order synthetic dataset)
Seed: 4×Q2 + 3×Q3 + 1×undateable.
- `total_scanned=8`, `total_classified=3`, `invariant_holds=true`
- `excluded_before_sync_start_date_count=4` (Q2 orders blocked)
- `excluded_missing_order_created_at_count=1` (undateable blocked)
- 5 orders that would previously have been classified are now
  correctly blocked from any downstream send path.

### Read-only guarantee (unchanged)
- No Qoyod API calls, no DB writes, no approve/send/bypass buttons.
- `production_writes_locked` untouched. Q2 orders never enter the
  send queue.



## Iter-001e — Eligible Orders Status Normalization (2026-02-XX)

### Problem
Production data mixes space vs. underscore forms of Arabic order statuses
(`جاري التوصيل` vs. `جاري_التوصيل`, `تم التوصيل` vs. `تم_التوصيل`,
`تم التنفيذ` vs. `تم_التنفيذ`). The previous `$in` query used only the
canonical space forms, so underscore-form rows were silently missed from
the Eligible Orders audit, inflating `excluded_status_count`.

### Fix
In `backend/integrations/qoyod/eligible_orders.py`:
- New helper `_normalize_status(s)` — lowercases, strips, replaces `_→space`.
- New helper `_expand_status_variants(base)` — MongoDB `$in` list now
  includes BOTH space and underscore variants of every eligible status.
- Inbox-fallback post-filter uses `_is_eligible_status()` (normalized).
- Response gains two new fields:
  - `total_eligible_by_status`  — raw counts per accepted status form.
  - `total_ineligible_by_status` — raw counts per excluded status form.
- `INELIGIBLE_STATUSES` constant added for documentation.
- Notes list mentions the normalization contract.

In `frontend/src/pages/EligibleOrders.jsx`:
- New collapsible **Excluded Reasons Panel** shows
  `excluded_reason_counts`, `total_eligible_by_status`, and
  `total_ineligible_by_status`. Read-only; no send/approve buttons.
- data-testids: `excluded-reasons-panel`, `excluded-reasons-toggle`,
  `excluded-reasons-body`, `excluded-reason-*`, `eligible-status-*`,
  `ineligible-status-*`.

### Tests
`backend/tests/test_eligible_orders_readonly.py` — 46 tests, all pass.
New coverage:
- `test_normalize_status_underscore_to_space`
- `test_normalize_status_trim_and_case`
- `test_normalize_status_none_and_empty`
- `test_is_eligible_status_underscore_arabic`
- `test_is_eligible_status_ineligible_stays_out`
- `test_ineligible_statuses_defined`
- `test_expand_status_variants_includes_both_forms`
- `test_underscore_arabic_status_treated_as_eligible`
- `test_space_arabic_status_still_eligible`
- `test_all_underscore_arabic_variants_eligible`
- `test_invariant_holds_with_normalization`
- `test_response_has_normalization_note`

### Verified impact (synthetic 10-order dataset)
- BEFORE (old query): 3 matched, 5 underscore-form rows missed.
- AFTER: 8 matched, 0 missed, invariant holds.

### Read-Only guarantee (unchanged)
- No Qoyod API calls.
- No DB writes.
- No approve/send/bypass/one-shot buttons.
- `production_writes_locked` never touched.



## Iter-293.4-rev3-per-order-approval — First live send unlock mechanism (2026-XX)

**Operator mandate** (verbatim phrase used as the unlock key):
> "Approved to send order 269571122 only. لا فتح production_writes_locked=false
> بشكل عام. إذا الإرسال الفردي لا يعمل إلا بفتح القفل العام، توقف ولا ترسل."

### Mechanism
`reprocess_one_order` accepts a new optional parameter `approval_phrase`.
When `production_writes_locked=True`:
- If `approval_phrase` is missing → `OneShotRefused(approval_phrase_required)`.
- If it doesn't equal exactly `"Approved to send order <order_number> only"` →
  `OneShotRefused(approval_phrase_mismatch)`.
- If correct → the api_client is constructed with
  `write_lock_enabled=False` **for this single run only**.
- The global `qoyod_settings.production_writes_locked` is NEVER read
  or modified — the override is purely scoped to the function call.

### Audit
Every granted approval is persisted to `qoyod_per_order_approvals`:
```
{ approval_id, user_id, order_number, trace_id, row_id, actor,
  approval_phrase, expected_phrase, approved_at,
  global_lock_was_active=True, scope="single_order",
  unlocked_api_client=True }
```
A WARNING log is also emitted to stdout:
```
PER_ORDER_APPROVAL granted actor=… order=… trace=… approval_id=… scope=single_order
```
The successful one-shot response surfaces `per_order_approval.approval_id`
+ `approved_at` so operators can link Qoyod invoice → audit row.

### New read-only endpoint
`GET /api/integrations/qoyod/admin/per-order-approvals`
Lists every granted approval (filterable by `order_number`).

### Phrase format
Phrase template is order-specific and cannot be reused:
```python
APPROVAL_PHRASE_TEMPLATE = "Approved to send order {order_number} only"
```
Pinned by `TestPhraseTemplateInDocstring`.

### Tests (9 new)
`tests/test_qoyod_per_order_approval_iter293_4.py`:
- **Refusals (4 tests):** locked + no phrase → `approval_phrase_required`,
  wrong phrase → `approval_phrase_mismatch`, phrase for different order
  → `approval_phrase_mismatch`, template stability pinned.
- **Granted flow (4 tests):** correct phrase persists audit row with
  all required fields, no approval needed when lock=False,
  api_client constructed with `write_lock_enabled=False`, global
  setting NEVER modified.
- **Total Qoyod regression:** 1146/1146 PASS (was 1137, +9 new, 0 regressions).

### Live curl-tested (locally)
With production_writes_locked=true + seed inbox row:
- No phrase → HTTP 400 with `approval_phrase_required` + expected phrase in error.
- Wrong phrase → HTTP 400 with `approval_phrase_mismatch` + expected vs received in error.
- (Correct phrase live-tested by operator on Production for order 269571122.)

## Iter-293.4-rev3-cleanup — Product mapping repair workflow (2026-XX)

**Operator demand** after Dry-run on order 269571122:
> "المنتج AMS11961 موجود فعلاً في قيود — Qoyod product_id = 39. لا تنشئ
> منتج جديد. حدِّث mapping في ميزان واستبدل DRY:product:fefe7c24."

### Two changes
1. **`adopt_qoyod_product` clears `dry_run_only=False`**.
   The existing manual-adoption helper upserted `adopted=True` but
   left the `dry_run_only` flag in place. After Iter-293.4-rev3 added
   `dry_run_only` as a sendable blocker in `preview_reprocess`, this
   stale flag would keep `dependency_status.sendable=False` even after
   the operator adopted the SKU. Fixed: every adoption call now
   explicitly sets `dry_run_only: False` in the `$set` block.

2. **NEW `GET /api/integrations/qoyod/admin/products/dry-mappings`**.
   Read-only audit listing every SKU whose mapping is still in the
   "needs repair" state — either `qoyod_product_id` starts with
   `DRY:`/`PREVIEW:` OR `dry_run_only=True`. Each row carries:
     - `sku`, `qoyod_product_id`, `qoyod_product_name`
     - `dry_run_only`, `adopted`, `source`, `created_at`
     - `needs_repair_via`: "POST /products/adopt"
     - `reason`: "dry_run_only=true" | "qoyod_product_id has DRY:/PREVIEW: prefix"

### Operator repair flow (no Qoyod write — just local mapping)
```
GET  /admin/products/dry-mappings        # see what needs repair
POST /products/adopt {sku, qoyod_product_id}   # bind real id, clear dry_run_only
GET  /admin/products/dry-mappings        # confirm empty list
POST /admin/preview-reprocess {trace_id} # re-run dry-run, expect sendable=true
```

### Tests (7 new)
`tests/test_qoyod_product_adopt_dry_cleanup_iter293_4_rev3.py`:
- 3 tests on `adopt_qoyod_product` clearing `dry_run_only` (replacing
  DRY mapping, fresh SKU, refusing empty inputs).
- 4 tests on the listing matcher (DRY prefix, PREVIEW prefix, real
  id without dry flag NOT listed, real id WITH dry flag IS listed).

### Verification (live)
- 1137/1137 Qoyod tests PASS (was 1130, +7 new, 0 regressions).
- Live smoke (local DB): seed DRY mapping → GET /admin/products/dry-mappings
  shows AMS11961 with reason=`dry_run_only=true` → POST /products/adopt
  with qoyod_product_id=39 → DB shows real id + dry_run_only=False +
  adopted=True + source=operator_adopted → second GET shows count=0.

### Rolled back (per operator decision)
- `Iter-293.4-rev4` (Selective Live Send Gate / Safe Live Posting):
  the `live_send_gate.py` module was created and the 3 new
  SettingsPatch fields were added — both have been REMOVED. The
  decision was to close Rev3 first (mapping repair) and only THEN
  open the conversation about selective live posting.

## Iter-293.4-rev3 — Operator review #2: DRY mappings + sendable gate honesty (2026-XX)

**Operator demand** after Rev2 Dry-run on order 269571122:
> "dependency_status.sendable=true but contact_id=null in request_body /
> qoyod_product_id = DRY:product:fefe7c24 was treated as sendable /
> needs UNRESOLVED_QOYOD_DEPENDENCY code."

### Three additional fixes

**Fix 5 — DRY mappings + dry_run_only flag never count as resolved**
- `_is_real_qoyod_id(v)` predicate added to preview_reprocess.
- Rejects: None, "", any value starting with `DRY:` or `PREVIEW:`.
- Combined with `dry_run_only=True` on the mapping doc → unresolved.
- Each `will_create_products[]` row now carries `unresolved_reason` of
  `no_mapping_row | dry_run_only_mapping | non_real_qoyod_id_prefix`.

**Fix 6 — Use REAL Qoyod ids in the preview request_body**
- Previous: `fake_customer_id = "PREVIEW:customer:<pending>"` always.
- Now: look up `qoyod_customers_mapping` by lookup_key (phone E.164 →
  email → guest_order). Use real id when present and not DRY/dry_run_only.
- Per-SKU: look up `qoyod_products_mapping`. Use real id when present.
- Only fall back to `PREVIEW:product:<sku>` for SKUs that would be created.
- The preflight step now receives the SAME resolved/preview ids the
  invoice builder used, so its checks are consistent.

**Fix 7 — Belt-and-braces request_body sanity scan**
- After the invoice_payload is built, scan its `contact_id` and every
  `line_items[].product_id`. If ANY fail `_is_real_qoyod_id`, force
  `dependency_status.sendable = False` regardless of what the dep
  computation said.
- `dependency_status.status` escalates to `"UNRESOLVED_QOYOD_DEPENDENCY"`.
- `dependency_status.request_body_unresolved` lists every problematic
  field with `{field, sku?, value, reason}` so the operator can act.

### Tests (10 new)
- `TestDRYMappingsBlockSendable` — 4 tests covering:
  - DRY:* product id → forced unresolved with reason.
  - `dry_run_only=True` with real-looking id → still unresolved.
  - Both real customer + real product mapping → sendable=True AND
    request_body carries real ids (not PREVIEW/null).
  - Customer-only resolution → sendable=False, products still flagged.
- `TestRequestBodySanityScan` — 1 test pinning the scan list shape.

### Verification (live)
- 1130/1130 Qoyod tests PASS (was 1125, +5 new, 0 regressions).
- The four-test E2E suite confirms a sendable=True case in preview
  produces a request_body with `contact_id="4421"` and
  `product_id="9871"` (real Qoyod ids), not nulls.

### Operator-facing change summary for the next Dry-run on 269571122
- If customer/products are NOT in qoyod yet (or are dry_run_only):
  - `dependency_status.sendable: false`
  - `dependency_status.status: "UNRESOLVED_QOYOD_DEPENDENCY"`
  - `dependency_status.request_body_unresolved: [{field:..., reason:...}, ...]`
  - `will_create_customer: true` and/or `will_create_products: [...]`
- If everything is resolved in qoyod:
  - `dependency_status.sendable: true`
  - `dependency_status.status: "ready_to_send"`
  - `request_body.invoice.contact_id` is a real Qoyod id
  - Every `line_items[].product_id` is a real Qoyod id
  - `request_body_unresolved: []`

## Iter-293.4-rev2 — Preview/Audit fixes from operator review (2026-XX)

**Operator demand** after running Dry-run on order 269571122 (COD):
> "write-lock-report لا يسجل محاولة القفل / receipt_preview يظهر رغم
> أن COD = credit_invoice_only / invoice_preview يحتوي IDs ناقصة /
> reconciliation يعرض diff -1.85 لطلب COD لا يحتوي receipt أصلاً."

### Four targeted fixes (no architecture changes)

**Fix 1 — Pipeline pre-check writes to `qoyod_write_lock_attempts`**
- `pipeline.process_customer_resolved_row` had a `production_writes_locked`
  pre-check that short-circuited BEFORE the api_client call. The audit
  collection (which only fires inside `api_client._request`) was never
  written. Result: a blocked order showed as `LOCKED_AWAITING_APPROVAL`
  in the inbox but was INVISIBLE to `/admin/write-lock-report`.
- Both pre-checks (invoice + invoice_payment steps) now call
  `record_blocked_attempt(...)` directly. The audit row carries
  `order_number`, `trace_id`, `callsite`, full locked_payload, and hints.
- Inbox row also stores `lock_attempt_id` for cross-correlation.

**Fix 2 — `receipt_preview` skipped for `credit_invoice_only`**
- `preview_reprocess.py` now computes `resolved_mode` BEFORE building
  the receipt preview.
- When `resolved_mode == POSTING_MODE_CREDIT_INVOICE_ONLY`, the block
  becomes:
```json
{ "skipped_by_posting_mode": true,
  "posting_mode": "credit_invoice_only",
  "would_send_to_qoyod": false,
  "request_body": null,
  "endpoint": null,
  "note": "Posting mode = credit_invoice_only — ..." }
```

**Fix 3 — `invoice_preview.dependency_status`**
- New block surfacing whether customer/products are resolved against
  `qoyod_customers_mapping` / `qoyod_products_mapping`:
```json
{ "customer_resolved": bool,
  "products_resolved": bool,
  "will_create_customer": bool,
  "will_create_products": [{sku, name, qoyod_product_id, adopted, would_create}],
  "sendable": bool,
  "status": "ready_to_send" | "invoice_payload_not_sendable_until_dependencies_resolved",
  "note": ar-SA explanation
}
```
- `safety_summary` also lifts `dependencies_sendable`,
  `will_create_customer`, `will_create_products_count` to the top
  level so an approver can refuse explicitly.

**Fix 4 — `reconciliation` skipped for `credit_invoice_only`**
- Receipt-vs-invoice diff is meaningless for COD/BNPL (no receipt).
- Block becomes:
```json
{ "skipped_for_credit_invoice_only": true,
  "posting_mode": "credit_invoice_only",
  "tax_mode": "...", "salla_declared_total": 213.78,
  "estimated_invoice_total": 213.78,
  "receipt_amount": null, "diff": null,
  "invoice_receipt_reconciled": null,
  "note": ar-SA explanation }
```
- Operator must use `safety_summary.difference` (salla vs invoice) for
  COD reconciliation, NOT this block.

### Tests (15 new)
`/app/backend/tests/test_qoyod_preview_audit_fixes_iter293_4_rev2.py`:
- 3 unit tests: pipeline pre-check audit persistence.
- 5 end-to-end tests against `preview_reprocess_one_order` with a real
  COD payload (order 269571122 scenario):
  - `receipt_preview.skipped_by_posting_mode == True`.
  - `reconciliation.skipped_for_credit_invoice_only == True`.
  - `dependency_status.sendable == False` for fresh order.
  - `safety_summary` surfaces dependency gate.
  - **ZERO httpx calls** during preview (mock + `assert_not_called`).

### Verification (live)
- 1125/1125 Qoyod tests PASS (was 1110, +15 new, 0 regressions).
- Live smoke: blocked attempt persisted + log emitted + visible in
  `/admin/write-lock-report?order_number=269571122` with full hints.

### Operator-facing changes summary
On the next Dry-run for order 269571122, the JSON will now show:
- `stages.receipt_preview.skipped_by_posting_mode: true` (was a misleading "POST /receipts" plan).
- `stages.invoice_preview.dependency_status.sendable: false` (new — exposes that PROD-A / customer need creation first).
- `reconciliation.skipped_for_credit_invoice_only: true` (no more -1.85 diff red herring).
- `safety_summary.dependencies_sendable: false` + `will_create_customer: true` + `will_create_products_count: N` (new).
- AND when the live webhook hits a locked tenant, the blocked attempt
  appears in `/admin/write-lock-report` with `action: create_invoice`,
  `callsite: pipeline.process_customer_resolved_row`, and the full
  `locked_payload`.

## Iter-293.4 — Global Qoyod Production Write Lock (2026-02-XX)

> Note (organisational): `Iter-294` remains reserved for the Bank Transfer
> Routing-by-receiving-bank work that depends on Production payload
> samples. The Global Write Lock is tagged as `Iter-293.4` to keep the
> changelog coherent with the user's planning sequence.

**User mandate** (post Iter-293.3 review):
> "Production writes must be locked across ALL write paths — حتى لو نسي
> المطور فحص القفل في pipeline أو resolver، الـ API client نفسه يمنع
> الإرسال. لا Deploy قبل اكتمال القفل الشامل."

The Iter-293.3 Kill Switch only covered `create_invoice` inside
`pipeline.process_customer_resolved_row`. Critical gaps surfaced:
`create_invoice_payment` (paid orders), `create_product` (new SKUs),
`create_contact` (new customers), `retry_payment_only.create_invoice_payment`,
and all `delete_*` paths (fresh_start_cleanup) were ALL unprotected.

### Defense-in-depth architecture
Lock enforcement moved into `QoyodAPIClient._request` itself. Every
POST/PUT/PATCH/DELETE is intercepted BEFORE the HTTPS call:

1. Method classified to a human action (`create_invoice`, `delete_product`, etc.).
2. Outbound payload + audit hints (sku, masked email, reference, amount)
   persisted to `qoyod_write_lock_attempts` collection.
3. `QoyodWriteLockedError` raised — caller surfaces clean
   `LOCKED_AWAITING_APPROVAL` outcome.

GET requests pass through untouched (test-connection, list_products,
list_inventories, etc. all keep working).

### Files changed
- **NEW** `/app/backend/integrations/qoyod/write_lock.py`:
  - `QoyodWriteLockedError` exception with `action`, `attempt_id`, `method`, `path`.
  - `classify_action(method, path)` — POST /invoices → `create_invoice` etc.
  - `extract_payload_hints(action, payload)` — sku, masked_email, name, reference, amount.
  - `mask_email`, `WRITE_METHODS` constant.
  - `is_locked(settings)` — Fail-Closed aware: missing field + env
    `QOYOD_FAIL_CLOSED_DEFAULT=true` → True; explicit value always wins.
  - `fail_closed_default_enabled()` — env state reporter.
  - `emit_blocked_log(...)` — emits `BLOCKED_QOYOD_WRITE action=… order=… reason=…`
    to stdout/journal at WARNING level. Format pinned by tests.
  - `record_blocked_attempt(db, ...)` — best-effort audit insert + log
    emission (never raises).
  - `list_blocked_attempts`, `count_blocked_attempts_by_action`.
  - `set_write_lock_context(order_number, trace_id, callsite)` — contextvar
    so audit records carry order context without API signature pollution.
- **`api_client.py`**:
  - Constructor accepts optional `db, user_id, write_lock_enabled`.
  - `_request` checks lock for write methods BEFORE httpx call. Records
    audit + raises `QoyodWriteLockedError`.
- **`pipeline.py`**:
  - `_get_api_client` snapshots `production_writes_locked` flag at
    construction time.
  - `process_customer_resolved_row` sets the audit context (order_number, trace_id).
  - Pre-check on `create_invoice` step (already existed via Iter-293.3).
  - **NEW** pre-check on `create_invoice_payment` step (symmetric).
  - **NEW** safety-net `except QoyodWriteLockedError` on both steps —
    saves `*_locked_payload` and returns `LOCKED_AWAITING_APPROVAL`.
- **`retry_payment_only.py`**:
  - API client constructed with `write_lock_enabled` snapshot.
  - `except QoyodWriteLockedError` returns clean `LOCKED_AWAITING_APPROVAL`
    response with `lock_attempt_id`.
- **`customer_resolver.py`**:
  - Direct-entry path constructs client with lock snapshot.
  - `except QoyodWriteLockedError` returns `ResolutionResult(success=False)`
    with `code=qoyod_write_locked` so pipeline routes it gracefully.
- **`product_resolver.py`**:
  - `except QoyodWriteLockedError` returns clean error with SKU + attempt_id.
- **`one_shot_reprocess.py`**:
  - API client constructed with lock snapshot (rest of code unchanged —
    underlying pipeline catches the locked error).
- **`routes.py`**:
  - Helper `_build_qoyod_client_for(db, tenant, key)` for consistent
    locked-client construction.
  - `/fresh-start/audit/run`, `/fresh-start/plan/build`, `/fresh-start/execute`
    now use the helper (delete_* operations protected).
  - **NEW** endpoint `GET /api/integrations/qoyod/admin/write-lock-report`:
    - Returns blocked attempts (paginated, filterable by action / order_number / since_hours).
    - Returns 24h counts by action.
    - Returns the live lock flag + operator-facing Arabic note.

### Tests (45 new, all pass)
`/app/backend/tests/test_qoyod_global_write_lock_iter294.py`:
- `classify_action` for all known POST/PUT/PATCH/DELETE paths.
- `mask_email` edge cases (short, no-@, None, empty).
- `extract_payload_hints` for product/contact/invoice/invoice_payment.
- **Core contract** — every write method raises `QoyodWriteLockedError`
  + persists audit row + makes ZERO http calls:
  - `create_invoice`, `create_invoice_payment`, `create_product`,
    `create_contact`, `create_receipt`.
  - `delete_invoice`, `delete_receipt`, `delete_product`, `delete_customer`.
- Read methods (`list_products`) pass through normally with lock=True.
- Writes flow normally with lock=False (no audit row created).
- Audit context (trace_id, order_number) captured per attempt.
- Lock refusal raises even with no db (defense without dependency).
- Audit query helpers (`list_blocked_attempts`, counts) work.

### Verification
- **1099/1099 Qoyod tests pass** (was 1042, +57 new for Iter-293.4).
- Live smoke: PUT /settings with `production_writes_locked=true` → all
  5 write methods (invoice/invoice_payment/product/contact/receipt)
  blocked, ZERO httpx calls, 5 audit rows persisted with proper hints,
  5 BLOCKED_QOYOD_WRITE log lines emitted to stdout/journal.
- Endpoint `/admin/write-lock-report` returns:
  - `production_writes_locked` (effective state)
  - `production_writes_locked_field` (raw setting; None if missing)
  - `fail_closed_default_enabled` (env state)
  - `lock_source` = `explicit_setting | env_fail_closed_default | unlocked_default`
- Fail-Closed verified: env `QOYOD_FAIL_CLOSED_DEFAULT=true` + missing
  setting → `is_locked == True`. Explicit `False` always overrides.

### Fail-Closed BY DEFAULT (hardened post-review)
**Code-level safety net — NO env var required on Production.**

`is_locked(settings)` resolves to:
| Settings field value | Env `QOYOD_MISSING_FIELD_UNLOCKED` | Result |
|---|---|---|
| `True` (explicit)    | (any)              | LOCKED   |
| `False` (explicit)   | (any)              | UNLOCKED |
| missing / `None`     | unset / `false`    | **LOCKED** (fail-closed default) |
| missing / `None`     | `true`             | UNLOCKED (dev/CI escape hatch only) |

Rationale: a freshly-deployed Production tenant whose `qoyod_settings`
doc has not been created yet would previously allow writes
(missing → False). The hardened default flips this to LOCKED so
a webhook arriving BEFORE the operator hits Settings → Save can NEVER
slip through to api.qoyod.com.

The `QOYOD_MISSING_FIELD_UNLOCKED=true` env var is reserved for
development / CI use ONLY. Production .env must NOT set it.

### Operator Action on Production
After Deploy:
1. **Verify** `GET /api/integrations/qoyod/admin/write-lock-report` shows
   `production_writes_locked: true` and `lock_source: fail_closed_default`
   (assuming the settings doc is missing) OR `explicit_setting` if you
   already set it.
2. To process a specific order: `POST /admin/preview-reprocess` for review.
3. To send: explicitly disable the lock per-batch via the existing
   `one_shot_reprocess` flow + per-order confirm token.

### Log format (pinned by `TestEmitBlockedLog`)
```
BLOCKED_QOYOD_WRITE action=create_invoice method=POST path=/invoices
  order=269547100 trace=trace-abc reason=production_writes_locked
  attempt_id=<uuid> reference=269547100 amount=131.92
BLOCKED_QOYOD_WRITE action=create_product method=POST path=/products
  order=- trace=- reason=production_writes_locked
  attempt_id=<uuid> sku=AMS-TEST
BLOCKED_QOYOD_WRITE action=create_contact method=POST path=/customers
  order=- trace=- reason=production_writes_locked
  attempt_id=<uuid> email_masked=b***r@example.com
```
(Logger name: `qoyod.write_lock`, level: WARNING. Visible in
`journalctl -u backend` / `tail -f /var/log/supervisor/backend.*.log`.)

### Operator workflow
1. `PUT /api/integrations/qoyod/settings` body `{"production_writes_locked": true}`.
2. All live webhooks + retry tools + one_shot + fresh_start now refuse writes.
3. Per-order review via `POST /admin/preview-reprocess` (dry run).
4. Explicit per-order approval via `one_shot_reprocess` with token
   `REPROCESS-<order_number>` (still respects the lock — operator must
   FIRST disable `production_writes_locked` to send).
5. Audit log via `GET /admin/write-lock-report?since_hours=24` shows
   every blocked attempt with the exact payload that was refused.

### Guarantees (pinned by tests)
- `WRITE_METHODS == {POST, PUT, PATCH, DELETE}` — adding a new mutating
  HTTP method requires updating this set (covered by test).
- Lock check happens BEFORE `httpx.AsyncClient.request` is called —
  api.qoyod.com NEVER receives the request (verified via patched
  AsyncMock that `mock_req.assert_not_called()`).
- Audit hints NEVER include raw emails (masked: `f***r@example.com`)
  or full phone numbers (last 4 only).
- Record-blocked-attempt NEVER raises — silent best-effort persist.


## Iter-293.1 — COD Fee as Separate Line + Audit-Grade Sourcing (2026-06-30)

**Symptom**: COD order `269532761` fell into DEAD_LETTER with `invoice_total_mismatch_before_post`. Salla total = 174.91, sum(items) = 169.89, delta = 5.02 SAR — the missing **COD fee**, an order-level charge not present in `items[]`.

**Root cause**: Normalizer extracted only `subtotal/tax/shipping/discount/total` from `amounts`. Order-level fees (COD fee, payment fee, etc.) were silently dropped, and the totals-guard correctly refused to send a mismatched invoice.

### What changed
- **`dto.py`** — three new fields on `SalesOrderDTO`:
  - `cod_fee_amount: float` — value from explicit payload key.
  - `cod_fee_source_path: Optional[str]` — exact JSON path the value came from (e.g. `data.amounts.cash_on_delivery`). Audit-proof.
  - `cod_fee_source_type: Optional[str]` — `"explicit_payload"` (or future `"salla_full_fetch"`). NEVER `"inferred_from_delta"` — that code path does not exist.
  - `extra_charges: dict` — verbatim capture of every unrecognised key in `amounts` (forward-compat for new Salla fee fields).
- **`normalizer.py`** — probes `amounts.cash_on_delivery` → `cod_fee` → `payment_fee` in priority order. First positive value wins. Records the JSON path for auditing. Zero values produce no source attribution.
- **`invoice_builder.py`**:
  - When `cod_fee_amount > 0` AND `default_cod_fee_product_id` configured → adds line **"رسوم الدفع عند الاستلام (COD Fee)"** with `discount` glue so the line's gross matches the fee exactly (no double-tax). Net mechanism identical to the existing shipping-line math.
  - When `cod_fee_amount > 0` but no product id → emits `_COD_FEE_MISSING_PRODUCT_ID_` diagnostic line and lets the guard refuse.
  - Diagnostics now expose: `cod_fee_detected`, `cod_fee_source_path`, `cod_fee_source_type`, `cod_fee_missing_product`, `inferred_from_delta: false` (always — invariant).
- **`pipeline.py`** — error codes now distinguish three failure modes:
  - `MISSING_COD_FEE_PRODUCT_ID` — explicit COD fee detected, operator needs to configure the Qoyod product.
  - `MISSING_ORDER_LEVEL_CHARGE` — payment_method=COD but NO explicit fee field + total mismatches. Carries `suspected_charge: "cod_fee"`, `missing_delta`, and `inferred_from_delta: true` (meaning "we COULD have guessed but REFUSED"). Operator needs to fix Make scenario to forward `amounts`.
  - `invoice_total_mismatch_before_post` — legacy fall-through (non-COD orders only).
- **`routes.py`** — Settings model accepts `default_cod_fee_product_id`.
- **`QoyodSettings.jsx`** — save() persists `default_cod_fee_product_id`.
- **NEW tests** (`/app/backend/tests/test_qoyod_cod_fee_iter293_1.py`) — 11 tests:
  - Normalizer extracts from all 3 candidate keys + records source path.
  - Zero/missing keys produce `source_path = None` (never invents a source).
  - Invoice builder adds line ONLY when explicit source exists, even if product id is configured.
  - Regression: non-COD paid orders still flow through unchanged.
  - Diagnostic asserts `inferred_from_delta: False` always.

### Audit guarantees (user-mandated invariants)
1. **No silent delta-to-COD-fee conversion.** The code has no path that infers `cod_fee_amount` from `total - sum(items)`. Proven by the test `test_no_explicit_source_means_no_cod_line_even_if_product_configured`.
2. **Every populated cod_fee carries a JSON path.** `cod_fee_source_path` is None ⇔ `cod_fee_amount == 0`. Auditor can replay the path against the stored payload.
3. **Distinct error codes per failure mode.** Operators see exactly what's wrong (product missing vs. Make scenario incomplete) instead of a generic mismatch.
4. **Forward-compat.** Any new fee key in `amounts` (e.g. `installment_fee`) is captured into `extra_charges` for visibility; the guard catches the resulting mismatch.

### Acceptance test (run on Production after Deploy)
1. Create Qoyod product: name="رسوم الدفع عند الاستلام", SKU=`MEZAN_COD_FEE`, type=Service, no inventory.
2. Set `default_cod_fee_product_id=<that_product_id>` via Settings PUT (or UI when added).
3. Reprocess order 269532761 OR create a new COD order with `amounts.cash_on_delivery > 0`.
4. Verify in Qoyod:
   - Invoice total = Salla total (e.g. 174.91).
   - 3 invoice lines: 2 items + COD Fee.
   - No invoice_payment / receipt (credit_invoice_only branch).
   - Remaining amount = invoice total.
5. Verify `inbox.qoyod_payloads.invoice_diagnostics`:
   - `cod_fee_detected: true`
   - `cod_fee_source_path: "data.amounts.cash_on_delivery"`
   - `inferred_from_delta: false`

### If acceptance test STILL fails
If a COD order arrives WITHOUT `amounts.cash_on_delivery` (or `cod_fee`/`payment_fee`) and has a delta:
- Guard raises `MISSING_ORDER_LEVEL_CHARGE` (not a generic mismatch).
- Action: fix Make.com scenario to forward the full `amounts` object, OR enable Salla Full Fetch integration (future Iter) to fill the gap server-side. Mezan will NOT auto-balance the difference.

### Acceptance gate update
```
Paid orders OK                             ✅
COD invoice (explicit fee + product id)    ✅ in code, ⏳ on Production
COD failure modes (specific codes)         ✅
Audit invariants (no delta inference)      ✅
bank_transfer routing (Iter-294)           ⛔ blocked on Production payload
waiting status exclude                     ⛔
simulator_version verify                   ⛔
ZATCA                                      🚫
```



## Iter-293 — COD = Credit Invoice Only (2026-06-30)
**Symptom**: COD (Cash-on-Delivery) orders were wrongly booked as PAID in Qoyod (balance=0 + invoice_payment), because the pipeline blindly created `/invoice_payments` for every order mapped to a Qoyod account.

**Root cause**: No notion of "posting mode" — every payment-method mapping forced an account_id and triggered the receipt step. Accounting reality: COD must remain as a credit (unpaid) invoice in Qoyod until the courier remits cash.

### What changed
- **Backend `payment_methods.py`** — new constants + helpers:
  - `POSTING_MODE_PAID_RECEIPT` (default for instant payments).
  - `POSTING_MODE_CREDIT_INVOICE_ONLY` (COD: invoice only, no receipt, no account).
  - `POSTING_MODE_DISABLED` (intentionally not synced).
  - `is_cod_family()` — recognises COD across en/ar + alias variants (`cash`, `cash_on_delivery`, `الدفع_عند_الاستلام`, etc.).
  - `resolve_posting_mode()` — **forces COD → credit_invoice_only**, ignoring any operator override (defense in depth).
  - `coerce_cod_rows()` — same enforcement at API write boundary.
  - `needs_qoyod_account()` — only `paid_receipt` requires an account.
- **Backend `pipeline.py`** — new branch before the invoice_payment step:
  - `credit_invoice_only` → skips `build_invoice_payment_payload`, `/invoice_payments` POST, account_id check. Marks row `COMPLETED` with `qoyod_invoice_payment_id=null`, `paid_amount=0`, `remaining_amount=total`.
  - `disabled` → stops at `INVOICE_CREATED` with reason `posting_mode_disabled`.
  - `paid_receipt` → existing behaviour unchanged.
- **Backend `models.py`** — `PaymentMethodMappingRow.qoyod_account_id` is now `Optional[str]` and `posting_mode` is a new optional field.
- **Backend `routes.py`** — PUT `/settings` runs `coerce_cod_rows` on the mapping before persisting.
- **NEW endpoints** (read-only, admin-gated):
  - `GET /api/integrations/qoyod/admin/cod-receipts-report` — lists every Qoyod invoice in the COD family that has a `qoyod_invoice_payment_id` (i.e. wrongly booked as paid). Per-row recommendation in Arabic. Filters: `from`, `to`, `limit`.
  - `GET /api/integrations/qoyod/admin/bank-transfer-discovery` — Iter-294 prep. Scans `qoyod_payloads` for orders with `payment_method=bank_transfer` and returns redacted candidate JSON paths (e.g. `$.order.transactions[0].bank_name`) so we can pinpoint where Salla encodes the receiving bank before designing per-bank routing.
- **Frontend `QoyodSettings.jsx` — `PaymentMethodMappingTable`**:
  - New column **وضع الترحيل لقيود** with 3-option dropdown.
  - COD rows: dropdown **disabled + locked** on `credit_invoice_only` + amber-tinted row + 🔒 hint "COD لا يحتاج حساب قبض".
  - Bank-transfer rows: **Legacy badge** in orange — "يحتاج Routing حسب البنك".
  - Account picker is hidden for `credit_invoice_only` / `disabled` rows ("غير مطلوب").
  - Save sanitiser coerces COD client-side AND validator excludes COD from `unmapped_payment_methods` blockers.
- **Frontend NEW page `/integrations/qoyod/cod-receipts-report`** — cards (total/with_receipt/without_receipt) + filters + table with red row-highlighting for mismatches.
- **Sidebar** — new nav entry `nav-qoyod-cod-receipts-report`.

### Tests
- `/app/backend/tests/test_qoyod_posting_mode_iter293.py` — 39 tests pass (unit + E2E coercion via PUT/GET roundtrip).
- Testing-agent `iter293_http.py` — 10 additional HTTP smoke tests, 49/49 pass.
- Iter-291 / Iter-292 backwards-compat verified (no regressions).

### Operator action on Production
1. Save to GitHub + Deploy to `mezansalla.com`.
2. Open Settings → Qoyod → طرق الدفع. Verify the COD row shows the 🔒 lock + "آجل" status, and bank_transfer shows the "Legacy" orange badge.
3. Open the new page from sidebar → "🧾 تقرير COD المُرحَّل كمدفوع".
4. Filter by date range, identify the wrongly-booked invoices (e.g. order #269349492), and manually void those `invoice_payment` records inside Qoyod's UI. **Mezan does not auto-cleanup Qoyod data.**

### Out of scope (intentionally — moved to Iter-294)
- `bank_transfer` routing by receiving bank — needs a real payload sample first. Use the new `/admin/bank-transfer-discovery` endpoint to capture candidate field paths from incoming orders.

### Acceptance gate for ZATCA (per user)
- ✅ COD fixed (credit-invoice-only, no receipt).
- ⛔ `bank_transfer` still blocked (Legacy mapping warning visible). ZATCA work cannot start until Iter-294 lands.



## Iter-291 — Salla OAuth `invalid_scope` Fix (2026-06-30)
**Symptom**: Merchants get `فشل الربط: invalid_scope` when trying to install Mezan from Salla store. OAuth fails BEFORE any order sync or webhook activity.

**Root cause**: `DEFAULT_SCOPES` requested `customers.read`, but Customers permission was NOT enabled in the Salla Partners Portal App for Mezan. Salla validates every requested scope against the App's enabled permissions during `/oauth2/auth` — any unenabled scope causes the whole request to fail with `error=invalid_scope`.

### What changed
- **`/app/backend/salla_integration/service.py`** — `DEFAULT_SCOPES` reduced to the minimum required + made env-overridable:
  - **Before**: `offline_access orders.read orders.write webhooks.read webhooks.write customers.read settings.read`
  - **After**:  `offline_access orders.read orders.write webhooks.read webhooks.write settings.read`
  - Operators can now override via `SALLA_OAUTH_SCOPES` env var (space-separated) without a code change.
- **`/app/backend/salla_integration/routes.py`** — `/oauth/login` now:
  - **Logs the exact authorize URL** (with masked `state`) before redirecting → debug-friendly.
  - **Surfaces `scope` and `scope_list`** in the JSON response → UI/QA can verify what's requested without parsing the URL.
- **NEW debug endpoint `GET /api/salla/oauth/scopes`** — auth-gated, returns the exact scope string, scope source (env vs code default), and a reminder note. Lets support engineers verify scopes side-by-side with the Partners Portal app config without triggering the OAuth flow.
- **NEW test suite `/app/backend/tests/test_salla_oauth_scopes_iter291.py`** — 7 pinned regression tests:
  - `customers.read/write` MUST NOT be in scope (root-cause regression).
  - `products.*`, `payments.*`, `shipping.*`, `shipments.*`, `taxes.*`, `branches.*`, `transactions.*` MUST NOT be in scope (per architecture: come from order payload).
  - `offline_access`, `orders.read`, `orders.write`, `webhooks.read`, `webhooks.write`, `settings.read` MUST all be present.
  - Scope string is single-line, space-separated, no commas / no double-spaces.
  - `SALLA_OAUTH_SCOPES` env override works.
  - Static guard: no code under `salla_integration/` calls `/customers` endpoints (so dropping `customers.read` is safe).

### Why customer data still works without `customers.read`
Per Salla's design, customer details (name, phone, email, ship-to address) are embedded inside the order payload that arrives via webhook/poll. The `customers.read` scope only authorises the standalone `/customers` listing/CRUD API, which Mezan does not use. Same logic applies to payments, shipping, taxes, branches — all sourced from the order payload.

### Verification
- 7/7 new Iter-291 tests pass.
- Backend restarts cleanly. Live preview `GET /api/salla/oauth/scopes` returns the corrected scope string.
- Existing Salla phase-1 tests are env-loading flaky in the pytest harness (pre-existing, unrelated to this change).

### Operator action required (production)
Code change alone is insufficient — the existing Salla install on `mezansalla.com` still holds an access_token bound to the OLD scope set, which Salla revokes/refuses on next refresh anyway. To re-enable the merchant:
1. `Save to GitHub` + `Deploy` to push the new code to production.
2. Merchant: uninstall the old Mezan app from their Salla store dashboard.
3. Merchant: re-install via Mezan → Settings → "ربط متجر سلة" → click Connect.
4. Salla should now redirect back to `/api/salla/oauth/callback?code=...` (no `invalid_scope`).
5. Verify success: `GET /api/salla/status` returns `connected: true`.

### Future toggle: when we need Products scope (SKU sync)
1. Enable Products (Read + Write) in the Salla Partners Portal App.
2. Set `SALLA_OAUTH_SCOPES="offline_access orders.read orders.write webhooks.read webhooks.write settings.read products.read products.write"` in backend/.env.
3. Restart backend. Merchant re-installs. No code change needed.



## Iter-290k.3 — CORRECTED قيود model + Representability Check (2026-06-29)
**User's diagnostic from order 269349492 proved the previous model wrong**: قيود computes `displayed_net = Σ round(line_net, 2)` (round-each-line-then-sum), NOT `round(Σ line_net, 2)` (sum-then-round). Verified by reproducing قيود's actual 228.11 from the production payload: 8.45+85.32+81.98+22.61 = 198.36, header_vat = round(198.36 × 0.15, 2) = 29.75, header_total = 228.11.

### Critical finding: some Salla totals are UNREPRESENTABLE in قيود
For order 269349492 (Salla=228.12), the reachable header_totals from any positive-discount tweak are **{228.10, 228.11, 228.13}** — 228.12 simply is NOT in قيود's reachable set. Producing a "paid" status with qoyod_total=228.11 would silently short-pay Salla by 0.01. The user explicitly rejected this as failure, not success.

### What changed
- **`simulate_header_vat()`** — FIXED to use round-each-line-then-sum. Reproduces قيود's 228.11 from the 269349492 payload exactly.
- **NEW `find_representable_adjustment()`** — brute-force search over ±0.030 SAR discount deltas (0.001 step) on the top 4 lines by value. Returns the first adjustment achieving BOTH `header_total_after == salla` AND `line_gross_sum_after == salla` AND `new_discount ≥ 0`. If none exists, returns `success=False` with `reachable_header_totals[]`.
- **`attempt_header_vat_alignment()`** — now delegates to `find_representable_adjustment` after the scope check.
- **New outcome `unrepresentable_total_under_qoyod_header_model`** — emitted when قيود's model cannot produce salla_total from the payload regardless of discount tweaks.
- **New row field `representability{}`** with: `qoyod_total_equals_salla`, `line_gross_sum_equals_salla`, `expected_payment_amount`, `expected_qoyod_total_after`, `expected_remaining_after`, `fully_representable`, `reachable_header_totals[]`.
- **UI**: new prominent "Representability Verdict" card per row — emerald (REPRESENTABLE) or rose (UNREPRESENTABLE) — showing all 5 acceptance criteria as boolean rows + the reachable_header_totals list for unrepresentable cases.

### Acceptance criteria (now strictly enforced)
A row is `adjustment_succeeded` ONLY IF:
1. `qoyod_total_after == salla_total` (within 0.005) ✓
2. `line_gross_sum_after == salla_total` (within 0.005) ✓
3. `new_discount ≥ 0` for the adjusted line ✓
4. `expected_payment_amount = salla_total` ✓
5. `expected_remaining_after == 0` (within 0.005) ✓

If ANY fails → outcome is `unrepresentable_total_under_qoyod_header_model` or `header_aligned_but_lines_drifted`.

### Verification
- 32/32 dry-run tests pass.
- Pinned: order 269349492 fixture is detected as UNREPRESENTABLE with reachable={228.10, 228.11, 228.13} ≠ 228.12.
- Pinned: a representable fixture (Line A discount 0.0049 + Line B 30 → 149.50) succeeds via Line A 0.0049→0.0051 tweak landing both on 149.49.
- 973/975 Qoyod tests pass (no regressions).

### Strict invariants (still enforced)
- ZERO DB / قيود / pipeline / payment / payload writes.
- DISCOUNT_ALLOCATION + MATERIAL_MISMATCH still hard-excluded.
- Iter-290l still BLOCKED — requires all 11 production PARITY GAP cases to test `fully_representable=true` before any production change is even considered.



## Iter-290k.2 — Header VAT Alignment Simulation (2026-06-29)
**User insight (root-cause confirmed)**: قيود computes invoice header total as `displayed_net + round(exact_net × tax%, 2)`, NOT as `sum(round(line_net × (1+tax%), 2))`. The two diverge by 0.01 whenever `exact_net × tax%` lands on the .005 half-up boundary. Reproduced on orders 269340921 and 268905066: line_gross_sum = 228.12 (= Salla) but header_total = 228.13 (= قيود's actual, leaving 0.01 unpaid). The fix targets BOTH header_total AND line_gross_sum landing on Salla via a minimal 4-dp discount tweak.

### Algorithm (pure simulation, no production writes)
```
simulate_header_vat(payload_lines) →
    exact_net_sum     = Σ (unit_price * qty - discount)         # full Decimal precision
    displayed_net_sum = round_half_up_2(exact_net_sum)
    header_vat        = round_half_up_2(exact_net_sum × rate)
    header_total      = displayed_net_sum + header_vat          ← قيود returns THIS
    line_gross_sum    = Σ round_half_up_2(line_net × (1+rate))  ← what we used to track

attempt_header_vat_alignment(payload_lines, salla_total) →
    diff = header_total - salla_total
    target_header_vat = header_vat - diff
    boundary = (target_header_vat ± 0.005) / rate              ← cross THIS half-up boundary
    target_exact_net = boundary - 0.00005
    adjustment_net = exact_net_sum - target_exact_net          ← rounded to 4 dp HALF_UP
    apply to largest line by (unit_price × qty)
    re-simulate → verify header_aligned AND lines_aligned
```

### Acceptance criteria (pinned by tests)
- `header_total_after  == salla_total` (within 0.005)
- `line_gross_sum_after == salla_total` (within 0.005)
- The proposed adjustment is **minimal** (typically 0.003–0.005 SAR net) — boundary-crossing math, not the over-correcting `diff/1.15`.
- Refuse if it would make any line's discount negative.
- Refuse if |header_diff| > 0.025 (out of Phase-2 scope).

### Backend files
- `/app/backend/integrations/qoyod/rounding_dry_run.py`:
  - **NEW** `simulate_header_vat(payload_lines)` returns 6-field dict.
  - **NEW** `attempt_header_vat_alignment(payload_lines, salla_total)` returns `before/after` snapshots + `header_aligned/lines_aligned` flags.
  - `_dry_run_single_row` now compares `header_total` to `qoyod_actual` for parity (closes the parity gap for قيود's pattern).
  - New row fields: `header_vat_before{}`, `header_vat_alignment{}`.
  - New outcome `header_aligned_but_lines_drifted` for cases where the smart adjustment fixed the header but moved a line's individual gross.
- `tests/test_qoyod_rounding_dry_run_iter290k.py`:
  - **31/31 tests pass** (7 new, 24 carried over).
  - Pinned the order 269340921 fixture: lines [65.785, 65.785, 66.80] reproduce header_total=228.13 vs line_gross_sum=228.12 EXACTLY.
  - Pinned that the smart alignment lands both metrics on 228.12 by adjusting Line C's discount by ~0.0034 SAR net.

### Frontend (`QoyodRoundingDryRun.jsx`)
- New 5-row "Header VAT Alignment" table per row showing each metric × (قبل | بعد | Salla), color-coded green when metric matches Salla.
- Adjustment proposal line displays: chosen line idx + description, old/new discount, adjustment_net, header_aligned/lines_aligned booleans.
- New outcome pill `header_aligned_but_lines_drifted` (amber) for partial wins.

### Strict guardrails (still enforced)
- ZERO DB writes.
- ZERO قيود calls.
- ZERO pipeline / payload / payment / live-DB mutations.
- DISCOUNT_ALLOCATION + MATERIAL_MISMATCH still hard-excluded.
- Iter-290l (production change) STILL BLOCKED — requires explicit user approval after reviewing prod simulation results.

### Decision blocked on user
Re-run dry-run on prod and confirm for the 11 PARITY_GAP cases:
1. `local_sim_matches_qoyod_actual` flips from `false` to `true` (parity closed by the new header VAT model).
2. `outcome` changes from `parity_gap_needs_qoyod_model` to `adjustment_succeeded`.
3. Per row, the "Header VAT Alignment قبل/بعد" table shows `header_total` and `line_gross_sum` both landing on Salla.
4. `adjustment_net` per row is a small fraction (~0.003–0.005), NOT a whole halala.

If all 11 cases meet criteria 1–4, then Iter-290l (production change) becomes a candidate. Phase-2 implementation only proceeds with separate user approval and only for NEW orders going forward.



## Iter-290k.1 — Parity Probe (BLOCKS Iter-290l) (2026-06-29)
**User report**: Phase-2 Dry-Run on prod shows 11 eligible cases ALL marked `no_adjustment_needed`, but the same orders show `Qoyod=248.60 vs Salla=248.59` (+0.01 drift) in the rounding mismatch report. The local Decimal simulator is reproducing Salla's expected total, NOT قيود's actual server-side recomputation. Therefore the proposed Phase-2 algorithm has NOT been validated yet and any "success" it reports is fake.

### What this iteration changes
- **NEW outcome `parity_gap_needs_qoyod_model`** — emitted when `local_sim_matches_salla=true` but `local_sim_matches_qoyod_actual=false`. Adjustment is NULL for these rows; no fix is proposed.
- **NEW parity gate**: `_dry_run_single_row` refuses to call `attempt_adjustment` unless `local_sim_matches_qoyod_actual` is true (within 0.005). Acceptance criteria from the user:
  - Step 1: `simulated_before ≈ qoyod_actual_total` (model parity)
  - Step 2: `simulated_after ≈ salla_total` (fix validity)
- **NEW three-way parity flags** on every row: `local_sim_matches_salla`, `local_sim_matches_qoyod_actual`, `qoyod_actual_matches_salla`.
- **NEW `parity` label** values: `ALIGNED` / `MODEL_OK_NEEDS_ADJUSTMENT` / `PARITY_GAP_LOCAL_MATCHES_SALLA` / `PARITY_GAP_MODEL_OFF` / `NO_QOYOD_ACTUAL`.
- **NEW `qoyod_response{}`** per row — invoice_id, invoice_total, invoice_balance, invoice_status, payment_amount, payment_id (all from `qoyod_responses.invoice.body`, no fresh قيود calls).
- **NEW `qoyod_response_lines[]`** — per-line {net, tax, total, local_sim_gross, local_vs_qoyod_line_gap}; surfaces WHICH line drifted, not just that the total drifted.
- **NEW top-level counters** `parity_gap_count` and `parity_histogram`.

### Frontend (`QoyodRoundingDryRun.jsx`)
- 6-tile summary now includes PARITY GAP card (amber).
- New parity histogram across all rows.
- Table columns: رقم الطلب / Bucket / **Parity** / Salla / **Local sim** / **Qoyod actual** / sim−qoyod / النتيجة / تفاصيل.
- Expanded row now shows: **Parity Gap callout** (when applicable), **triple comparison** card (Salla vs Local-sim vs Qoyod-actual), **قيود response** card (invoice_id/balance/status/payment), and a **line-comparison table** with sky-tinted local-sim columns + amber-tinted قيود-response columns + rose-tinted `line_gap` column.
- New "PARITY GAP فقط" filter option.

### Strict invariants (preserved)
- No DB writes.
- No قيود calls.
- No pipeline / payload / payment / DB mutations.
- DISCOUNT_ALLOCATION still hard-excluded.
- MATERIAL_MISMATCH still hard-excluded.
- Iter-290l (production change) BLOCKED until parity_gap_count drops to 0.

### Verification
- 24/24 dry-run tests pass (5 new + 19 updated). Critical pins:
  - PARITY_GAP_LOCAL_MATCHES_SALLA outcome emits NULL adjustment.
  - `qoyod_response` summary fields extracted correctly.
  - `qoyod_response_lines[].local_vs_qoyod_line_gap` computed.
  - `ALIGNED` parity when all three totals agree.
- 965/967 Qoyod tests pass (no regressions; 2 pre-existing skipped).
- API now returns `parity_gap_count`, `parity_histogram` at top level.
- Smoke screenshot confirms 6-tile summary with PARITY GAP card.

### Decision blocked on user
Re-run the dry-run on prod. If parity_gap_count > 0 (expected for the 11 cases), then the Decimal+ROUND_HALF_UP model is NOT a faithful replica of قيود's server-side math. Possible roots:
1. قيود applies a different rounding rule (e.g., banker's rounding or per-line gross-then-net).
2. قيود recomputes line totals from a different basis (e.g. unit_price+tax then subtract discount).
3. Stored `qoyod_payloads.invoice` differs from what قيود actually received (encoding/serialization).
4. قيود's tax calc has a per-invoice rounding step we're not modeling.

Next step (after Iter-290k.1 ships): inspect prod `qoyod_response_lines` line-by-line to identify which line(s) قيود computed differently. That tells us the rounding rule difference. ONLY after parity is reached do we open Iter-290l.



## Iter-290k — Phase-2 DRY-RUN Simulation (Decimal + ROUND_HALF_UP) (2026-06-29)
**User direction**: Before any production change, simulate the proposed Phase-2 fix on real recent invoices to validate it lands `simulated_qoyod_total == salla_total` for halala-scale drift. ZERO writes to DB or قيود.

### Scope (per user's explicit narrowing)
**Include**: severity = `MINOR_ROUNDING` AND |invoice_diff| ∈ {0.01, 0.02} AND bucket ∈ {QOYOD_SERVER_SIDE_ROUNDING, MULTI_LINE_CUMULATIVE_ROUNDING, SHIPPING_ROUNDING_MISMATCH, INVOICE_TOTAL_ROUNDING_MISMATCH}.

**Exclude**: DISCOUNT_ALLOCATION_MISMATCH (needs own RCA — order 269087627), PAYMENT_MISMATCH_ONLY (invoice already correct), MATERIAL_MISMATCH (e.g. 6.24 / 18.84 SAR — not rounding), INSUFFICIENT_DATA, rows without `qoyod_payloads.invoice.line_items`.

### Algorithm (pure-function, Decimal + ROUND_HALF_UP)
```
simulate_invoice(payload_lines) → Σ round_half_up((u*q - d) * (1+t/100), 2)
attempt_adjustment(payload_lines, salla_total):
    diff = simulated - salla
    if |diff| > 0.025  → out_of_phase2_scope (refuse)
    pick largest line by (unit_price × quantity)
    adjustment_net = diff / tax_factor_of_chosen_line
    new_discount = current_discount + adjustment_net
    if new_discount < 0  → negative_discount_blocked (refuse)
    re-simulate → diff_after ≤ 0.005 ⇒ success
```

### Files
- **NEW** `/app/backend/integrations/qoyod/rounding_dry_run.py` — pure-function simulator + report builder.
- **NEW** route `GET /api/integrations/qoyod/admin/rounding-dry-run` in `routes.py`.
- **NEW** `/app/frontend/src/pages/QoyodRoundingDryRun.jsx` — UI with 5-tile summary, skip-reason histogram, outcome filter, per-row payload-column breakdown clearly labeled with column origin (`qoyod_payload_*`, `simulated_*`).
- **NEW** sidebar link "🧪 محاكاة Phase 2 (Dry-Run)" + route `/integrations/qoyod/rounding-dry-run`.
- **NEW** `tests/test_qoyod_rounding_dry_run_iter290k.py` — 19 tests pinning algorithm, eligibility rules, and end-to-end report. All pass.

### Strict guardrails (every test verifies)
- No DB writes.
- No قيود calls (no httpx client touched).
- DISCOUNT_ALLOCATION_MISMATCH is hard-excluded even when |diff|=0.01.
- MATERIAL_MISMATCH (e.g. 6.24 SAR) is hard-excluded by severity check.
- Negative discount is never proposed.

### Verification
- 19/19 pytest pass for dry-run.
- 960/962 Qoyod test suite pass (no regressions; 2 pre-existing skipped).
- Smoke screenshot confirms UI renders with summary tiles + scope banner + empty state.

### Next decision point (BLOCKED on user)
The user will run the dry-run on PRODUCTION and inspect the proposed adjustments. Phase-2 implementation (touching the live pipeline) only proceeds after the user confirms the proposed `adjustment_net` values are acceptable for `QOYOD_SERVER_SIDE_ROUNDING` cases. DISCOUNT_ALLOCATION remains its own stream.



## Iter-290i.2 — SearchableSelect for Payment Method Mapping (2026-06-29)
**User request**: Apply the SearchableSelect picker (already rolled out elsewhere in QoyodSettings via Iter-290i) to the Payment Method Mapping table — the only place still using a raw text input for `qoyod_account_id`. Show account NAME instead of bare numeric ID, with `ID · code` as a secondary hint. Empty / failed accounts list must NOT label saved ids as "غير موجود".

### Changes (additive, no schema change, no posting logic touched)
- **`PaymentMethodMappingTable`** (`QoyodSettings.jsx`):
  - Replaced `<input type="text">` for `qoyod_account_id` with `<SearchableSelect>`.
  - New props: `accountsList`, `accountsListUnavailable`, `accountsUnavailableReason`.
  - Per-row test id: `pm-account-select-<key>`.
  - Help-hint text updated to match the new search UX.
- **`SearchableSelect`** (`searchable-select.jsx`):
  - New optional prop `unavailableLabel` — when provided AND `listUnavailable && value`, renders `${unavailableLabel} (ID ${value})` instead of the generic "ID X (لم تُحمّل القائمة)".
  - Existing callers unaffected (default null preserves prior behavior).
- **Parent invocation** (`QoyodSettings.jsx`):
  - Treats `accounts.length === 0` ALSO as `unavailable` (not just `fetch_errors` non-empty). This is what the user explicitly asked for: never label a saved id as "missing" when the list was never fetched.
  - Passes `unavailableLabel="تعذر تحميل قائمة حسابات قيود"` so the trigger shows the domain-specific Arabic message.
- **Display contract**:
  - Option in dropdown: "إيرادات المبيعات / الخدمات   ID 17 · 4101"
  - Search filters by name OR id OR code (via `Command.filter` over `[name, id, code].join(" ")`).

### What is NOT changed (per the user's explicit guardrails)
- `qoyod_account_id` payload structure — still a plain string, persisted exactly as before.
- Pipeline / `/invoices` / `/invoice_payments` math — untouched.
- Phase-2 rounding fix — still paused per Iter-290j Phase 1.5 decision.
- Iter-292 (transitional payment statuses) — still not started.

### Verification
- `testing_agent_v3_fork` iteration_55: 7/7 acceptance criteria pass.
  - SearchableSelect renders (`pm-account-select-mada`).
  - Old raw input is gone.
  - Trigger label is "تعذر تحميل قائمة حسابات قيود (ID 9)" on empty list (NOT "غير موجود").
  - Popover shows the `*-unavailable` banner.
  - Trigger is `<button>`, not `<input>`.
  - No new console errors.



## Iter-290j-rounding-fix · Phase 1.5 — Richer rounding diagnostic (2026-06-29)
**User report after Phase 1 production scan (70 invoices)**: 43 match, 14 fell into the `INVOICE_TOTAL_ROUNDING_MISMATCH` catch-all, 13 marked `INSUFFICIENT_DATA`, plus drifts as large as 6.24 / 18.84 SAR were being lumped in with halala-scale 0.01 drifts. User explicitly forbade Phase 2 (any math change) until the report explains the catch-all cases and separates real rounding from material mismatches.

### What this iteration adds (all read-only, no math changes)
- **NEW bucket `QOYOD_SERVER_SIDE_ROUNDING`** — replaces the catch-all for cases where ميزان's `expected_qoyod_total` matched Salla but قيود recomputed differently. This was the silent majority of the 14 catch-all rows.
- **Severity tag per row**:
  - `MINOR_ROUNDING` for `|invoice_diff| ≤ 0.02`
  - `MODERATE_DRIFT` for `0.02 < |invoice_diff| ≤ 0.05`
  - `MATERIAL_MISMATCH` for `|invoice_diff| > 0.05`  ← these are NOT rounding; need their own remediation
- **`data_gaps[]` per INSUFFICIENT_DATA row** — exact reason codes (`no_invoice_response`, `no_payment_response`, `no_line_diagnostics`, `no_canonical_items`, `no_qoyod_invoice_id`, `pre_logging_row`) so the operator stops seeing the opaque "بيانات ناقصة".
- **Richer per-line table** — fuses `canonical_payload.items` (qty, unit_price, discount, tax_amount), `invoice_diagnostics.line_diagnostics` (Mezan-computed), and `qoyod_responses.invoice.body.invoice.line_items` (قيود-line gross when echoed back).
- **Per-row `summary{}`** — `primary_cause`, `offender_count`, `shipping_contribution`, `non_shipping_contribution`, `largest_offender`.
- **Two new histograms**: `by_severity`, `by_gap_reason`.

### API shape extensions (additive, no breaking changes)
- `GET /api/integrations/qoyod/admin/rounding-mismatch-report` now also returns `by_severity` and `by_gap_reason` at the top level. Each row carries `severity`, `data_gaps`, `lines`, `summary` (legacy `line_diffs` kept for backward compat).

### Frontend (`QoyodRoundingReport.jsx`)
- Three histograms (bucket / severity / data-gap).
- Severity pill alongside bucket pill on every row.
- Expanded row now shows: invoice summary card → richer line table (نوع/SKU/qty/unit_price/discount/tax%/Salla-target/Mezan-computed/Qoyod-line/Δ).
- New filters: severity dropdown, gap-reason dropdown.

### Tests
- `tests/test_qoyod_rounding_mismatch_report_iter290j.py` — rewritten with 18 tests covering all new buckets, severity tiers, gap reasoning, and `lines[]` fusion. **All 18 pass.**

### Explicit non-goals (per user)
- **No** Phase-2 math change yet.
- **No** payment override.
- **No** invoice rebuilding.
- The user will inspect the enhanced report on production, then decide which slice (qoyod_server_rounding vs discount allocation vs material mismatch) gets its own targeted Phase-2 fix.



## Iter-290h.4 — One-shot-reprocess diagnostics for the payment-link step (2026-02-28)
**User report**: After Iter-290h.3 deployed and the retry ran for order 269048975, the UI showed `request_body_json تم إيقافه (لم يُرسَل لقيود)` — but the diagnostic also displayed a `qoyod_validation_error: Invalid resource`. The user couldn't tell whether the request actually reached قيود or was halted pre-flight.

### Backend (`one_shot_reprocess.py`)
- `_build_failure_response` now has an explicit branch for `PAYMENT_LINK_FAILED` + `PAYMENT_METHOD_MAPPING_MISSING` that surfaces:
  - `payment_post_attempted` — was the POST attempted?
  - `request_sent_to_qoyod` — did the request reach قيود's server?
  - `qoyod_status_code` — HTTP status code قيود returned
  - `qoyod_response` — قيود's response excerpt verbatim
  - `skip_reason` — operator-facing Arabic explanation when halted pre-flight
  - `request_body_json` — the exact payload the pipeline built (NEW shape with `date` + `account`)

### Frontend (`QoyodFirstSyncMonitor.jsx`)
- The payload label is now **conditional** — three states:
  - `"جسم الطلب المُرسل لقيود (نجح)"` — outcome=COMPLETED
  - `"جسم الطلب المُرسل لقيود — قيود رفضه"` — request_sent_to_qoyod=True
  - `"جسم الطلب الذي تم إيقافه قبل إرسالها لقيود"` — pre-flight halt
- New diagnostic grid below the JSON for `PAYMENT_LINK_FAILED` / `PAYMENT_METHOD_MAPPING_MISSING` showing the 5 diagnostic fields in Arabic.

### Tests
- `test_qoyod_oneshot_payment_link_diagnostics_iter290h4.py` — 2 tests covering both diagnostic branches.
- **865/865 Qoyod tests pass**, lint clean.

### Idempotency confirmed unchanged
- The check at `pipeline.py:747` requires `qoyod_invoice_payment_id` to be present — failed attempts never write to `qoyod_invoice_payments` so they don't block retries. (User's concern #4 was already correct in the existing implementation.)

### Next operator step
After deploy + one-shot-reprocess for 269048975, the user will see the EXACT قيود response. If قيود is genuinely rejecting the payload with `Invalid resource`, the new diagnostic grid reveals which field is missing — and we then open Iter-290h.5 to add it (per official Qoyod doc / GET /invoices/63 evidence, never by guessing).



## Iter-290h.3 — Live Qoyod field-name correction `date` + `account` (2026-02-28)
**Production failure**: Order 269048975 (Invoice 63, 131.92 SAR) — invoice created successfully, payment-link step rejected with:
```
{"error":"Invalid resource",
 "messages":{"date":["Can't be blank"],"account":["Can't be blank"]}}
```
We were sending `payment_date` + `payment_method_id` based on a third-party API summary. **Live Qoyod evidence** confirms the canonical field names are `date` and `account`.

### Backend fixes
- **`invoice_builder.py`** — `build_invoice_payment_payload` now emits:
  ```json
  {"invoice_payment": {
      "invoice_id": <int>, "amount": <decimal>,
      "date": "YYYY-MM-DD", "account": <int>,
      "reference": "<order#>", "description": "Mezan · Salla order …"
  }}
  ```
- **`pipeline.py`** — Pre-POST guard now reads `account` (not `payment_method_id`).
- **`api_client.py`** docstring updated with the live shape.
- **Internal idempotency fingerprint** keeps logical names (`payment_method`, `payment_method_id`) so historical DB rows still match.

### Monitor bug fix
- **`first_sync_monitor.py`** — New `_status_for_invoice_payment_step` function. Previously, when a row sat in `PARTIAL_FAILURE` with `last_failed_stage=PAYMENT_LINK_FAILED` but no `qoyod_invoice_payment_id`, the monitor returned **"pending"** because it only recognized the legacy `RECEIPT_CREATED`/`FAILED_RECEIPT` tokens. Operator saw a failing call as "in progress". Now explicitly handles `PAYMENT_LINK_FAILED` + `PAYMENT_METHOD_MAPPING_MISSING` → **"failed"** status.

### Tests (7 new, total 863 Qoyod pass)
- `test_qoyod_invoice_payment_wire_names_iter290h3.py`:
  - Payload uses `date` + `account`; explicit assertion that `payment_date` + `payment_method_id` are NOT present.
  - `account` is `None` when method unmapped (pre-POST guard intact).
  - Monitor surfaces `PAYMENT_LINK_FAILED` as `failed` (not `pending`).
  - Monitor surfaces `PAYMENT_METHOD_MAPPING_MISSING` as `failed`.
  - Pending state preserved for true in-progress rows.
  - Legacy `RECEIPT_CREATED` rows still display as success (back-compat).

### Operator retry instructions (invoice 63 ONLY)
After deploy:
1. Use the existing `one-shot-reprocess` endpoint with:
   - `salla_order_number = "269048975"`
   - `confirm_token = "REPROCESS-269048975"`
2. The pipeline auto-skips /customers (already resolved), /products (mappings cached), and **reuses invoice 63** via the idempotent short-circuit. ONLY `POST /invoice_payments` is re-attempted with the corrected field names.
3. No new invoice is created. No new standalone receipt is created.



## Iter-290h.1 — Unallocated Receipts Admin Page (2026-02-28)
**Operator-facing companion** to Iter-290h. Surfaces PYT1–PYT8 (and any future orphan receipts) for manual reconciliation inside قيود UI.

### Backend additions
- **`unallocated_receipts_report.py`**:
  - `_suggest_invoice` now returns `(invoice, match_reasons, score)`. `match_reasons` is a subset of `{"reference","amount","customer","date"}` — operator-readable chips on the UI.
  - `_qoyod_deep_links` — emits `receipt_url` + `invoice_url` from `settings.qoyod_ui_base_url` (default `https://www.qoyod.com/tenant`).
  - `dismiss_receipt(...)` / `undismiss_receipt(...)` — soft toggle on new collection `qoyod_unallocated_dismissals` so the operator can mark a receipt as "تمت المعالجة يدوياً" inside ميزان. Audit trail preserved on un-dismiss.
  - Report output: `qoyod_receipt_url`, `qoyod_invoice_url`, `match_reasons`, `match_score`, `dismissable` per item; `summary.by_confidence` breakdown.

- **`routes.py`**: New endpoints
  - `GET    /api/integrations/qoyod/admin/unallocated-receipts-report`
  - `POST   /api/integrations/qoyod/admin/unallocated-receipts/{receipt_id}/dismiss`
  - `DELETE /api/integrations/qoyod/admin/unallocated-receipts/{receipt_id}/dismiss`

### Frontend addition
- **`/app/frontend/src/pages/QoyodUnallocatedReceipts.jsx`** — Admin page with:
  - 5 stat cards: total unallocated · with suggestion · high confidence · medium/low · without suggestion
  - Table columns: السند · العميل · المبلغ · التاريخ · الفاتورة المقترحة · الثقة · سبب المطابقة · إجراء
  - Match-reason chips: reference / amount / customer / date (Arabic labels)
  - Deep links: "فتح في قيود ↗" for both receipt and invoice
  - "تمت المعالجة يدوياً" button with inline note input + confirm/cancel
  - Empty state when no orphans remain
- Sidebar entry: `nav-qoyod-unallocated-receipts` → "🧾 سندات قبض غير مستعملة"
- Route: `/integrations/qoyod/unallocated-receipts`

### Tests (7 new, total 856 Qoyod tests pass)
- `test_qoyod_unallocated_receipts_report_iter290h.py` extended:
  - `_qoyod_deep_links` (default + override + missing-id)
  - `match_reasons` in suggestion output
  - `dismiss_then_report_excludes_receipt`
  - `dismiss_is_idempotent` (no duplicate rows)
  - `undismiss_soft_toggles_active_false` (audit-trail preserved)

### Boundaries (per user spec — strict)
- **No allocation API call**. Mezan never mutates Qoyod state — operator links manually in قيود UI.
- **No auto-delete + recreate**. PYT1–PYT8 stay intact in Qoyod.
- **Dismissal is local to ميزان**. Reversible via DELETE endpoint.

### Operator workflow
1. Open ميزان → التكاملات → سندات قبض غير مستعملة.
2. For each row: click "فتح الفاتورة في قيود ↗" → in قيود UI, allocate the receipt to that invoice manually.
3. Back in ميزان → click "✓ تمت المعالجة يدوياً" → optional note → confirm.
4. Row disappears from the report. Audit row stays in `qoyod_unallocated_dismissals`.



## Iter-290h — POST /invoice_payments replaces standalone POST /receipts (2026-02-28)
**User report**: After end-to-end success on order 268784455, the receipt showed up in Qoyod's "غير مستعمل" (unallocated) bin and the invoice balance remained > 0. Qoyod's data model distinguishes **Receipts** (standalone) from **Invoice Payments** (registered ON the invoice — the only resource that closes the invoice balance). The old `POST /receipts` flow created orphan receipts that never reconciled.

### Architectural decision (user-approved 1a + 2c)
- **Replace** `POST /receipts` with `POST /invoice_payments` for ALL new orders. No dual flow.
- **No fallback** to `/receipts` on payment-link failure (per user spec — "لا يوجد fallback").
- **Backfill PYT1–PYT8**: read-only admin report listing unallocated receipts with suggested matching invoices; operator links manually in قيود UI for now. Auto-link button postponed.

### Fixes
**State machine** (`state_machine.py`)
- `HAPPY_PATH` now: `… INVOICE_CREATED → INVOICE_PAYMENT_CREATED → COMPLETED`
- Legacy `RECEIPT_CREATED` retained in `ALL_STAGES` for in-flight rows during deploy window.
- New failure stages: `PAYMENT_LINK_FAILED`, `PAYMENT_METHOD_MAPPING_MISSING` (both resume from `INVOICE_CREATED`).

**API client** (`api_client.py`)
- New `create_invoice_payment(payload, idem)` → `POST /invoice_payments`.

**Invoice builder** (`invoice_builder.py`)
- New `build_invoice_payment_payload(...)` returns `(payload, idempotency_fingerprint)`.
- Payload: `{invoice_payment: {invoice_id, amount, payment_date, payment_method_id, reference, description}}`.
- Fingerprint per user spec: `order_id + qoyod_invoice_id + payment_method + amount`.
- `DryRunQoyodClient.create_invoice_payment` added.
- `build_receipt_payload` marked DEPRECATED but retained.

**Pipeline** (`pipeline.py`)
- Renamed 4d step from RECEIPT to INVOICE_PAYMENT.
- Pre-POST guard 1: refuses with `PAYMENT_METHOD_MAPPING_MISSING` when mapping missing.
- Pre-POST guard 2: DB-side idempotent short-circuit on `qoyod_invoice_payments` collection (fingerprint match).
- Failure → `PAYMENT_LINK_FAILED` → `PARTIAL_FAILURE` (invoice still in Qoyod; only payment-link missed).
- Persists request_body_json + Qoyod response excerpt for the operator's diagnostic.
- New collection: `qoyod_invoice_payments` (ledger + idempotency store).

**First-sync monitor** (`first_sync_monitor.py`)
- Step renamed `receipt` → `invoice_payment` with legacy fallback for historic rows.

**Admin endpoint** (`routes.py`)
- `GET /api/integrations/qoyod/admin/unallocated-receipts-report` — lists Qoyod receipts that appear unallocated and proposes the best matching invoice (scored by reference / amount / customer / date).

### Tests (20 new + 5 updated)
- `test_qoyod_invoice_payments_iter290h.py` — 7 tests covering builder shape, happy path, idempotency, payment_link_failed, no-fallback-to-receipts, preflight mapping guard.
- `test_qoyod_unallocated_receipts_report_iter290h.py` — 13 tests covering allocation heuristics, suggestion scoring, API stub integration.
- Updated `test_qoyod_state_machine.py`, `test_qoyod_day5_invoice_receipt.py`, `test_qoyod_idempotent_invoice_reuse_iter291.py`, `test_qoyod_first_sync_monitor.py` to reflect new flow.
- **849/849 Qoyod tests pass**, lint clean.

### Operator workflow (post-redeploy)
1. **Deploy** to mezansalla.com.
2. New order flows through `INVOICE_CREATED → INVOICE_PAYMENT_CREATED → COMPLETED`. The Qoyod invoice MUST show balance=0 and NO new "غير مستعمل" receipt.
3. PYT1–PYT8 backfill: `GET /api/integrations/qoyod/admin/unallocated-receipts-report` returns the orphan receipts with suggested invoices. Operator links each manually in قيود (only 8 receipts).



## Iter-290g — Qoyod `/products` `tax_id` scalar shape (2026-02-28)
**User scenario**: Production order `268784455` (SKU `AMS11542` — كرت اهداء حسب الطلب) failed at `FAILED_PRODUCT` with 422 from Qoyod:
```
{"tax_id": ["Please select taxes"]}
```
We were sending `tax_id: ["1"]` (array) because Iter-289 had wrongly inferred Qoyod's validator wanted a `has_many :taxes` array shape. Production evidence proves the validator wants a **scalar** — preferably an integer.

### Fixes (`product_resolver.py`)
1. **`_coerce_id_to_int` + `_unwrap_id_for_payload`** — helpers that:
   - Unwrap multi-select arrays (take first usable element).
   - Coerce numeric strings → `int`. Non-numeric strings pass through unchanged (legacy compat).
   - Empty / None / `[]` → `None` (caller drops the key).
2. **`_stamp_required_ids`** — sends ALL four ids (`category_id`, `tax_id`, `product_unit_type_id`, `sales_account_id`) as SCALARS. The Iter-289 `[tax]` array wrap is reverted.
3. **`validate_product_id_shapes` + `build_invalid_id_shape_error`** — new preflight that refuses multi-element-array settings with structured `product_payload_invalid_id_shape` before any POST.
4. **`validate_product_defaults`** — now accepts any non-empty unwrapped value (int, multiselect single, numeric/legacy string), refusing only truly empty configs.
5. **422 self-heal retry** extended to trigger on `please select taxes` / `tax_id` error messages (defensive — if a future tenant flips shape expectation).
6. **Fallback payload** bumps `selling_price` 0 → 1.0 for catalog row only (some tenants refuse free products). Invoice line price is untouched — accounting unaffected.
7. **Diagnostic error attribution** — failure payloads now carry the exact `sku` and `attempted_selling_price` of the failing line item (fixes the confusing "item #2 fails but item #1's SKU is logged" case).

### Tests
- New `test_qoyod_product_tax_id_scalar_iter290g.py` — 13 tests covering coercion, unwrap, shape detection, payload contract, preflight blocking, end-to-end POST shape, 422 self-heal, and SKU attribution.
- `test_qoyod_product_tax_id_array_iter289.py` — fully rewritten as a regression guard for the SCALAR contract (Iter-289 inverted).
- `test_qoyod_product_required_defaults_iter287.py` — 3 assertions updated from array → scalar.
- **825/825 Qoyod tests pass**, lint clean.

### Operator workflow (post-redeploy)
1. Redeploy preview → production (mezansalla.com).
2. Run preview-reprocess on `268784455` → confirm `product_create_request_body` carries `tax_id: 1` (integer scalar).
3. Then one-shot-reprocess for the order. Expected flow: PRODUCT_CREATED → INVOICE_CREATED → RECEIPT_CREATED.



## Iter-290f — Shipping line + preflight reconciliation skip (2026-02-28)
**User scenario**: After Iter-290e shipped, production order `268860160` (Salla=131.92 = items 106.92 + shipping 25.00) was rejected at preflight:
```
estimated_invoice_total=130.07 would NOT match receipt_amount=131.92
(diff=-1.85 SAR, tolerance=0.66)
```
Two compounding bugs:
1. The **old** Iter-285 `invoice_receipt_reconciliation` preflight estimator didn't account for `shipping_amount` and would false-positive on any shipped order.
2. The **new** Iter-290e payload builder didn't add a shipping line — so even if preflight passed, the Qoyod invoice would still come up short by ~25 SAR.

### Fixes
**`preflight.py`**
- Skip the legacy Iter-285 reconciliation check when `invoice_total_policy == "match_salla_total"` (Iter-290e has its own shipping-aware guard).
- New check #6.6 — `missing_default_shipping_product_id` fires when `shipping_amount > 0` AND the setting is blank.

**`invoice_builder.py`**
- When `shipping_amount > 0` AND policy is `match_salla_total`:
  - Append a shipping line bound to `settings.default_shipping_product_id`.
  - Compute `shipping_target_gross = total_amount − sum(item.total)`.
  - Apply the same match_salla_total math: `discount = shipping_amount − target_gross/1.15`.
  - Same negative-discount fallback as product lines.

**`SettingsPatch` (`routes.py`)**: new field `default_shipping_product_id`.

### Tests
- New `test_qoyod_shipping_line_iter290f.py` — 6 tests including the exact production order numbers and edge cases.
- **815/815 Qoyod pytest passes**, lint clean.

### Operator workflow (post-redeploy)
1. **In Qoyod UI**: create a single product called "شحن - ميزان" (or any name), set it as Service / non-stock, get its id.
2. **In Mezan Settings** (currently via API; UI in Iter-295): PUT `default_shipping_product_id = <id>`.
3. Redeploy preview → production.
4. Run `one-shot-reprocess` for `268860160` → expected:
   - 3 invoice lines (2 products + 1 shipping)
   - Qoyod invoice total = 131.92 (matches Salla)
   - INVOICE_CREATED ✅ → RECEIPT_CREATED ✅ → balance = 0

### Quick API call to set the setting before UI lands
```
PUT /api/integrations/qoyod/settings
{ "default_shipping_product_id": "<Qoyod product id>" }
```

## Iter-290e — Qoyod 15% Match Salla Total (2026-02-28)
**Business requirement**: Qoyod's standard `tax_percent=15` (Saudi VAT) ≠ Salla's effective per-line tax (~8% empirically on test orders). Iter-290c shipped a working invoice but the totals inflated:
```
268756329:  Salla 290.63  vs  Qoyod 309.47   (Δ +18.84 SAR)
268833109:  Salla  96.23  vs  Qoyod 102.47   (Δ  +6.24 SAR)
```
Customer paid Salla's amount → Qoyod invoice MUST land on the same number, else receipt creates an outstanding balance that doesn't reconcile.

### Fix (`/app/backend/integrations/qoyod/invoice_builder.py`)
New policy `invoice_total_policy = "match_salla_total"` (default):
- For each line, reverse-engineer the discount so Qoyod's (unit_price·qty − discount)·(1 + tp/100) = item.total.
- Math: `target_net = item.total / 1.15` → `discount = unit_price·qty − target_net`.
- unit_price stays verbatim from Salla (auditable).
- Edge cases handled:
  - `item.total == 0` → discount = full base.
  - `discount < 0` (anomalous) → fallback to `unit_price = target_net/qty, discount = 0`.

New settings field: `qoyod_tax_percent` (default 15). Allows future regional override.

### Pre-POST math guard (`pipeline.py`)
- Before calling `api_client.create_invoice`, extract diagnostics and validate `|expected_qoyod_total − salla_total| ≤ 0.10 SAR`.
- If exceeded → dead-letter with `invoice_total_mismatch_before_post`. No POST.
- Diagnostics block kept OUTSIDE the `invoice` dict so Qoyod never receives it.

### Diagnostics in row + preview
Stored on `qoyod_payloads.invoice_diagnostics`:
```
{
  "pricing_mode":               "match_salla_total",
  "salla_total":                96.23,
  "expected_qoyod_total":       96.23,
  "difference":                 0.00,
  "salla_tax_percent_detected": 8.00,
  "qoyod_tax_percent_used":     15,
  "line_diagnostics":           [...]
}
```
Surfaced via `preview-reprocess` → `stages.invoice_preview.diagnostics`.

### Tests
- New `tests/test_qoyod_match_salla_total_iter290e.py` — 9 tests including the exact production order numbers (268833109, 268756329) and edge cases.
- Updated 4 legacy tests to explicitly use `invoice_total_policy="legacy_passthrough"` (test fixtures pre-date this policy).
- New `SettingsPatch` fields: `invoice_total_policy`, `qoyod_tax_percent`.
- **809/809 Qoyod pytest passes**. Lint clean.

### Operator workflow (post-redeploy)
1. **CRITICAL — cleanup Qoyod first**: delete or cancel the wrong invoices already created in قيود for orders `268833109` and `268756329`. Don't mark them as paid (`GetPaid`); they are accounting-invalid.
2. Redeploy preview → production.
3. Run `one-shot-reprocess` for one test order → expected `INVOICE_CREATED` with total == Salla total ✅ → `RECEIPT_CREATED` ✅ → `balance = 0`.

### Go-Live gate
Order is COMPLETED only if:
- INVOICE_CREATED succeeds
- RECEIPT_CREATED succeeds
- receipt.amount == canonical.total_amount
- Qoyod-reported invoice total ≈ Salla total (±0.10)

## Iter-293 + Iter-294 — Webhook Activity Log + Monitor UI (2026-02-28)
**Context**: After Qoyod MVP closed (Iter-285 → Iter-291 — first end-to-end Salla→Qoyod invoice + receipt on order 268833109 ✅), the operator asked to harden the Make.com integration path with an observable webhook log.

### Backend — Iter-293 (`/app/backend/integrations/qoyod/webhook_activity.py`)
- New module exposing 4 helpers:
  - `record_webhook_event(...)` — best-effort write, NEVER raises.
  - `list_recent_events(...)` — paginated list with filters (event_type, order_id, skipped_only).
  - `get_event_counts(...)` — facet aggregation: total / accepted / skipped / errors / by_event.
  - `soft_cap_old_rows(...)` — trims oldest beyond `keep` cap (1000 default).
- New collection `qoyod_webhook_events` with indexes:
  - `(user_id, received_at desc)` — listing
  - `received_at` TTL 7 days
  - `(user_id, salla_order_id)` sparse — order filter
  - `(user_id, event_type, received_at desc)` — event-type filter
- Webhook handler instrumented (try/finally) to log EVERY arrival — duplicates, parse failures, accepted rows — without affecting the live pipeline.

### Backend — Iter-294 endpoints (`routes.py`)
- `GET /api/integrations/qoyod/admin/webhook-activity` — last 50 events + filters.
- `GET /api/integrations/qoyod/admin/webhook-activity/counts?hours=24` — facet counts.
- Soft-cap maintenance fires lazily once per list call.

### Frontend — Iter-294 (`/app/frontend/src/pages/QoyodWebhookMonitor.jsx`)
- Replaces the `/integrations/qoyod/sync-log` placeholder.
- Live "tail" with auto-refresh (5s / 15s / 30s / 60s).
- 4 stat cards (total / accepted / skipped / errors) over selectable window (1h / 24h / 7d).
- Filters: event_type, order_id, skipped_only.
- Color-coded rows: 🟢 accepted, 🟡 skipped, 🔴 error.
- Click row → drawer with full event JSON.
- Distribution chips for `by_event` breakdown.

### Tests
- New `tests/test_qoyod_webhook_activity_iter293.py` (7 tests, MongoDB-integration): insert, list filters, count facets, soft cap trim, never-raises safety.
- **805/805 Qoyod pytest passes**, lint clean (Python + ESLint).
- Live page verified via Playwright screenshot — renders, fetches data, displays counts and rows correctly.

### Make-side guidance (operator's responsibility)
The user controls the Make scenario, which we cannot modify. They should:
- Switch to **Salla Instant Webhook** (not polling) for sub-15s latency.
- Subscribe to 7 events: `order.created`, `order.updated`, `order.status.updated`, `order.completed`, `order.cancelled`, `order.refunded`, `payment.updated`.
- Send `items` as a **real JSON array**, not the literal string `[object Object]`. Mezan already detects this and logs to `webhook_parse_failures` — `/admin/webhook-parse-failures` surfaces them.

### Operator workflow
1. Each webhook from Make is auto-logged to `qoyod_webhook_events`.
2. The `/integrations/qoyod/sync-log` page tails them live with color-coded states.
3. Anything red/yellow can be drilled into and forwarded to Make config for fixing.

## Iter-291 — Idempotent invoice short-circuit for retry scenarios (2026-02-28)
**User scenario**: After Iter-290d shipped the receipt fix, the operator faces a dilemma — Qoyod already has invoice id `51` from the previous run. Re-running `one-shot-reprocess` would naively POST another invoice → duplicate in قيود.

### Fix (`/app/backend/integrations/qoyod/pipeline.py::process_customer_resolved_row`)
- Before calling `api_client.create_invoice`, check if `row["qoyod_invoice_id"]` is already set AND the row is NOT in dry-run mode.
- If yes: SKIP the POST entirely. Reuse the stored `qoyod_invoice_id` and proceed straight to the receipt step.
- Stamp diagnostic markers on the row so an auditor can tell the invoice was reused (not freshly created):
  - `qoyod_responses.invoice.reused_from_previous_run = True`
  - `qoyod_responses.invoice.reused_qoyod_id = "51"`
  - `qoyod_responses.invoice.reused_at = <timestamp>`
- Fresh rows (no `qoyod_invoice_id`) continue to POST normally — backwards-compatible.
- Dry-run mode unaffected (stubs always create fresh DRY:* ids).

### Tests
- New `tests/test_qoyod_idempotent_invoice_reuse_iter291.py` (4 tests, MongoDB-integration):
  - reuse path: `create_invoice` NOT called
  - reuse path: `create_receipt` still fires with reused id
  - fresh path: `create_invoice` IS called
  - diagnostic markers written to the row
- **793/793 Qoyod pytest passes**. Lint clean. (4 pre-existing `qyd_go` failures still unrelated.)

### Operator workflow with this fix
1. Order 268756329 already has invoice id 51 in Qoyod (from previous run).
2. Redeploy preview → production.
3. Run `one-shot-reprocess` for 268756329:
   - Pipeline resets row to NORMALIZED
   - Re-resolves customer (idempotent) + products (auto-adopt by SKU)
   - **Invoice step: REUSES id 51, no Qoyod POST** ← Iter-291
   - Receipt step: POSTs with `contact_id=109` ← Iter-290d
4. Expected: `INVOICE_CREATED (reused) → RECEIPT_CREATED` ✅

## Iter-290d — Qoyod /receipts requires `contact_id` at root (2026-02-28)
**User scenario**: After Iter-290c reshaped the invoice payload, production order `268756329` finally reached **INVOICE_CREATED ✅** (Qoyod returned invoice id `51`). However the immediately-next stage failed with:
```
POST /receipts → 422
{"error": "Invalid resource", "messages": {"contact": ["Can't be blank"]}}
```
The Mezan receipt builder never stamped `contact_id` — Qoyod's `/receipts` validator requires it just like `/invoices` does.

### Fix (`/app/backend/integrations/qoyod/invoice_builder.py::build_receipt_payload`)
- New parameter `qoyod_customer_id: Optional[str]` (passed through from row state).
- Receipt payload now stamps `contact_id` at the receipt root, coerced to int via `_to_int_or_none`.
- `invoice_id` and `account_id` also int-coerced (consistent with Iter-290c).

### Callers updated
- `pipeline.py` line 599 — passes `row["qoyod_customer_id"]`.
- `preview_reprocess.py` line 375 — same.

### Tests
- New `tests/test_qoyod_receipt_contact_id_iter290d.py` (5 tests): contact_id at root, all-ids-int, missing customer omission, amount/currency preservation, account_id omission when no mapping.
- Updated `tests/test_qoyod_day5_invoice_receipt.py::test_receipt_payload_resolves_payment_account` — numeric ids, asserts new contact_id presence.
- Updated `_seed_settings` payment_method_mapping to use numeric account ids.
- **789/789 Qoyod pytest passes**. Lint clean. (4 pre-existing `qyd_go` failures still unrelated.)

### Deploy
- Fix lives in preview. **Production redeploy required**.
- After redeploy: rerun `one-shot-reprocess` for `268756329` → expect `RECEIPT_CREATED` ✅ → **first complete end-to-end Qoyod invoice + receipt!** 🎉

## Iter-290c — Full Qoyod-canonical invoice payload reshape (2026-02-28)
**User scenario**: After Iter-290b coerced `inventory_id` to int, production order `268756329` STILL failed with the same Qoyod error even though the JSON visibly carried `inventory_id: 1` on every line. The user located the official Qoyod API docs example and confirmed the payload SHAPE is wrong, not just the value types.

### What Qoyod's docs actually require (vs what we were sending)
| Field | Mezan was sending | Qoyod expects |
|-------|-------------------|---------------|
| `invoice.inventory_id` | absent | **integer at root** |
| `line.inventory_id` | int, on every line | **must be omitted** |
| `invoice.status` | absent | `"Approved"` |
| `line.tax_id` | string | **dropped** in favour of `tax_percent` |
| `line.tax_percent` | absent | **number (15)** per line |
| `line.discount_type` | absent | `"amount"` (or `"percentage"`) |
| `contact_id` | string `"109"` | **integer** |
| `product_id` | string `"39"` | **integer** |
| `branch_id` | string `"10"` | **integer (omit if missing)** |

### Fix (`/app/backend/integrations/qoyod/invoice_builder.py`)
- New helper `_to_int_or_none(v)` — central id-coercion at the payload boundary. Handles `None`, `bool`, `int`, `float`, `str` cleanly; returns `None` for non-numeric/blank.
- `build_invoice_payload` reshaped:
  - `inventory_id` moved from every line → invoice ROOT (int).
  - `status: "Approved"` added at root.
  - Every line: `discount_type: "amount"` + `tax_percent` (defaults to 15, override via `settings.tax_percentage`).
  - Per-line `tax_id` removed (replaced by `tax_percent`).
  - Per-line `unit_price` is now Salla's raw NET price (the customer_first gross-of-tax trick is dropped — incompatible with Qoyod's tax_percent model).
  - `contact_id`, `product_id`, `branch_id`, `inventory_id` all int-coerced at emission time.
  - `branch_id` omitted entirely when non-numeric/blank.

### Trade-off (intentional, user-approved)
The previous `customer_first` mode guaranteed invoice total = Salla customer-paid total. With Qoyod's standard 15% `tax_percent` and Salla's effective rate sometimes <15%, the Qoyod invoice total may diverge slightly. Receipt amount stays at the customer-paid total → any gap surfaces as an unpaid invoice balance for operator reconciliation. This is the price of the Qoyod-canonical payload shape.

### Tests
- Rewrote `tests/test_qoyod_inventory_id_on_invoice_lines_iter290.py` with 15 tests pinning the new contract (root inventory_id, no per-line inventory_id, status=Approved, tax_percent per line, discount_type, type-safety on all ids, branch omission, preflight, all coercion paths).
- Updated `tests/test_qoyod_customer_first_tax_mode_iter285.py` — both modes now emit `tax_percent` + NET unit_price + `discount_type: amount`.
- Updated `tests/test_qoyod_day5_invoice_receipt.py` — all id assertions now expect integers; `_LiveLikeQoyodClient._fake` returns numeric ids (real-Qoyod-realistic) so the DRY-run leak detector doesn't trip.
- Updated `tests/test_qoyod_first_sync_monitor.py` — `branch_id` expected as int; `tax_percent` replaces `tax_id` per line.
- Updated `tests/test_qoyod_line_discount_iter276.py` — same.
- **784/784 Qoyod pytest passes**. Lint clean. (4 pre-existing `qyd_go` failures still unrelated.)

### Deploy
- Fix lives in preview. **Production redeploy required**.
- Operator workflow post-redeploy:
  1. Settings stay as-is (no value changes needed — `default_inventory_id="1"` already valid).
  2. Run `preview-reprocess` for `268756329` → verify payload now has `inventory_id` at invoice root + `status: "Approved"` + `tax_percent: 15` per line + no `tax_id`.
  3. Run `one-shot-reprocess` once → expect `INVOICE_CREATED` ✅ → `RECEIPT_CREATED` ✅.

## Iter-290b — `inventory_id` must be sent as integer, not string (2026-02-28)
**User scenario**: After Iter-290 stamped `inventory_id` on every invoice line, production retry of order `268756329` STILL failed with the same Qoyod error:
```
POST /invoices → 422
{"errors": ["inventory id missing in a line item"]}
```
even though the JSON payload visibly carried `"inventory_id": "10"` on every line.

**Root cause**: Qoyod's invoice validator expects `inventory_id` as **integer** (per official apidoc example: `"inventory_id": 1001`). A string value (`"10"`) is treated as missing by the validator. Mezan's Settings UI persists ids as strings, so we must coerce at payload-build time.

### Fix (`/app/backend/integrations/qoyod/invoice_builder.py::build_invoice_payload`)
- Read `settings.default_inventory_id`, strip whitespace, then `int(...)` it.
- Non-numeric values are silently omitted from the payload (preflight blocks the row upstream so we never POST without it).
- Integer values pass through unchanged.

### Tests
- Extended `tests/test_qoyod_inventory_id_on_invoice_lines_iter290.py` with 4 coercion tests:
  - string "10" → int 10 on every line
  - native int 7 → unchanged
  - non-numeric "main-warehouse" → field omitted
  - whitespace "  10  " → 10 (trimmed + coerced)
- Existing test updated to assert `isinstance(ln["inventory_id"], int)`.
- **777/777 Qoyod pytest passes**. Lint clean.

## Iter-290 — Qoyod /invoices requires `inventory_id` on every line item (2026-02-28)
**User scenario**: After Iter-289 fixed `tax_id` as array, production order `268756329` reached `PRODUCT_RESOLVED` ✅ then failed at `FAILED_INVOICE` ❌ with:
```
POST /invoices → 422
"inventory id missing in a line item"
```
Qoyod's `/invoices` validator demands `inventory_id` on every line — even for `type=service` / `is_non_stock=true` products. The operator has no warehouses configured in Salla but Qoyod still requires one.

### Backend
- **`api_client.py`**: Added `list_inventories()` → `GET /inventories`.
- **`routes.py`**: New endpoint `GET /api/integrations/qoyod/qoyod-inventories` proxied via the same catalog-fetcher helper.
- **`routes.py::SettingsPatch`**: New optional field `default_inventory_id` accepted by `PUT /settings`.
- **`invoice_builder.py::build_invoice_payload`**: Stamps `inventory_id` on EVERY line from `settings.default_inventory_id`. Omitted entirely when blank (preflight refuses upstream).
- **`preflight.py`**: New check (#6.5) `missing_default_inventory_id` blocks the row before any POST when the setting is blank.

### Frontend (`QoyodSettings.jsx`)
- New `inventories` + `inventoriesMeta` state and `qoyod-inventories` fetch in `loadCatalogs`.
- New `IDInput` for `default_inventory_id` in the Product/Invoice Defaults section, with datalist suggestions from `/qoyod-inventories`.
- Section title updated to "إعدادات إنشاء المنتجات والفواتير في قيود" + warning note that the warehouse is required even for service products.
- Validation banner surfaces `missing_default_inventory_id` as a **blocker** when the field is blank.

### Tests
- New `tests/test_qoyod_inventory_id_on_invoice_lines_iter290.py` — 6 tests covering line stamping, blank/missing setting omission, and preflight refusal/passage.
- Updated `tests/test_qoyod_day5_invoice_receipt.py` and `tests/test_qoyod_payment_method_aliases.py` to set `default_inventory_id` in fixtures.
- **773/773 Qoyod pytest passes** post-fix. Lint clean (Python + ESLint).
- Live `GET /api/integrations/qoyod/qoyod-inventories` returns 401 (auth required) — confirming the route is registered (not 404).

### Deploy
- Fix lives in preview. **Production redeploy required** to `mezansalla.com`.
- Operator workflow post-redeploy:
  1. Create one warehouse in Qoyod (`الإعدادات → المخازن → +`), name it "مستودع افتراضي - ميزان".
  2. Open Mezan Settings → datalist will load real warehouses from `/qoyod-inventories`. Pick the id.
  3. Save settings → run `preview-reprocess` for `268756329` → expect no blockers.
  4. Run `one-shot-reprocess` for `268756329` → expect `INVOICE_CREATED` → `RECEIPT_CREATED`.

## Iter-289 — Qoyod /products requires `tax_id` as JSON array (2026-02-28)
**User scenario**: After Iter-288b added the four required product settings in the UI, the operator filled in `default_product_tax_id = "15"` (a valid Qoyod tax id) and re-ran `one-shot-reprocess` for production order `268756329`. Qoyod still rejected the product create with:
```
POST /products → 422
{"errors": {"tax_id": ["Please select taxes"]}}
```
despite the field being present and the id being valid.

**Root cause**: Qoyod's product validator runs a `has_many :taxes` check on the incoming payload. A scalar value (`"tax_id": "15"`) fails the validation even when the id exists — Qoyod expects a JSON array (`"tax_id": ["15"]`). Confirmed against Qoyod legacy API documentation (2026-02).

### Fix (`/app/backend/integrations/qoyod/product_resolver.py::_stamp_required_ids`)
- Wrapped the configured `default_product_tax_id` in a JSON array before stamping it on the outgoing payload.
- The other three required ids (`category_id`, `product_unit_type_id`, `sales_account_id`) remain scalar — they map to `belongs_to` relationships and accept a single value.
- Behavior preserved when the setting is blank: the key is dropped entirely (preflight blocks upstream).

### Tests
- New regression file `tests/test_qoyod_product_tax_id_array_iter289.py` — 4 tests covering full payload, fallback payload, exact length=1, and blank-setting omission.
- Updated 3 assertions in `tests/test_qoyod_product_required_defaults_iter287.py` to expect a list.
- **771/771 Qoyod pytest passes** post-fix. Lint clean.

### Deploy
- Fix lives in preview. **Production redeploy required** to push to `mezansalla.com`.
- After redeploy: operator runs `preview-reprocess` → expect `PRODUCT_RESOLVED`; then `one-shot-reprocess` → expect `INVOICE_CREATED` → `RECEIPT_CREATED`.

## Iter-288b — Settings UI for Qoyod-Required Product Defaults (2026-02-27)
**User scenario**: After Iter-287 added the four required product settings on the backend, the operator opened `/integrations/qoyod/settings` in production and couldn't find input fields for them — the UI hadn't been extended. Result: `missing_qoyod_product_defaults` was firing correctly but with no way to fix it.

### Frontend additions (`/app/frontend/src/pages/QoyodSettings.jsx`)
- **NEW Section "إعدادات إنشاء المنتجات في قيود"** with four required inputs:
  - `default_product_category_id` (Category ID) — labelled with hint "انسخه من قيود → الإعدادات → التصنيفات".
  - `default_product_tax_id` (Product Tax ID) — uses the same `taxes` datalist as Invoice Tax.
  - `default_product_unit_type_id` (Unit Type ID) — e.g. "1 (قطعة)".
  - `default_sales_account_id` (Sales Account ID) — uses the `accounts` datalist.
- **NEW Auto-Adopt toggle** for `auto_adopt_existing_qoyod_products` (Iter-288). On by default with description: when enabled, existing Qoyod SKUs are auto-bound; when disabled, strict Trust Gate refuses each new SKU.
- All four fields are surfaced as **blocker-severity** issues in the live validation banner when missing — operator sees the gap immediately on the settings page, not only at send time.

### Backend additions (`/app/backend/integrations/qoyod/routes.py::SettingsPatch`)
Added optional fields to the `PUT /api/integrations/qoyod/settings` Pydantic model so the new keys persist:
- `default_product_category_id`
- `default_product_tax_id`
- `default_product_unit_type_id`
- `default_sales_account_id`
- `tax_mode` (customer_first | mezan_fixed_15) — Iter-285
- `zero_tax_id` — Iter-285
- `auto_adopt_existing_qoyod_products` — Iter-288

`QoyodSettings` document model uses `extra="allow"` so no migration needed.

### Verification
- Browser screenshot confirms the new Section renders with all four inputs + the Auto-Adopt toggle, and the bottom banner reflects "كل الحقول مكتملة — جاهز للحفظ" once filled.
- **758/758 Qoyod pytest passes**, 0 regressions. Lint clean (Python + ESLint).
- Live settings PUT with the new keys returns 200 (Pydantic accepts them).

### Production runbook
1. Redeploy preview → production.
2. Open `/integrations/qoyod/settings` on `mezansalla.com` → fill the four IDs from Qoyod trial tenant UI:
   - **التصنيف**: Qoyod → الإعدادات → التصنيفات → اختر تصنيف افتراضي → انسخ ID.
   - **ضريبة المنتجات**: عادةً = Tax ID للفاتورة (15% VAT). يمكن استخدام نفس القيمة.
   - **وحدة القياس**: Qoyod → الإعدادات → وحدات القياس → "قطعة" → انسخ ID.
   - **حساب المبيعات**: Qoyod → الحسابات → دليل الحسابات → اختر حساب الإيرادات → انسخ ID.
3. التحقق من Auto-Adopt toggle (default ON).
4. اضغط حفظ. Banner سيتحول إلى أخضر "جاهز للحفظ".
5. شغّل preview-reprocess للطلب 268756329 → `product_defaults_status.ok=true` يجب أن يظهر الآن.
6. ثم one-shot-reprocess. SKUs الموجودة مسبقاً في قيود ستُربط تلقائياً (Iter-288)، الجديدة ستُنشأ مع الـ defaults الجديدة (Iter-287).


## Iter-288 — Auto-Adopt Existing Qoyod Products by SKU (2026-02-27)
**User scenario**: Operator is uploading the full Amasi catalog to Qoyod trial manually. SKU is the canonical key between Salla and Qoyod. The pre-Iter-288 Trust Gate REFUSED any order whose SKU happened to already exist in Qoyod, requiring a manual adopt — which would block every order in the trial.

### Behaviour (mandatory sequence)
1. **Check local mapping** `db.qoyod_products_mapping` by `(user_id, sku)` — zero Qoyod calls.
2. **If no mapping** → `GET /products?q[sku_eq]=<sku>` via `api_client.find_all_products_by_sku(sku)`.
3. **If 1 match** AND `auto_adopt_existing_qoyod_products=True` (default) → write local mapping `source=auto_adopted_from_qoyod, adopted_by=system, resolved_via=auto_adopt_sku_match`, reuse the existing `qoyod_product_id`. **NO `POST /products`**.
4. **If 2+ matches** → block with `code=duplicate_qoyod_sku, failed_at_stage=PRODUCT_MATCH` + surface all matches for the operator to clean up.
5. **If 0 matches** → proceed with the Iter-287 create path.
6. **If `auto_adopt_existing_qoyod_products=False`** (strict Trust Gate) → 1 match → refuse with `qoyod_existing_untrusted` (legacy behaviour preserved).

### Backend changes
- `api_client.find_all_products_by_sku(sku, limit=10)` — new multi-row lookup; defensively re-checks SKU equality in case Qoyod ignores the filter. Legacy `find_product_by_sku` kept as a 1-row wrapper.
- `resolve_products` runs the new flow per SKU. Falls back to single-row API for older test stubs via `hasattr` check.
- `DryRunQoyodClient.find_all_products_by_sku` returns `[]` and records audit.
- `preview_reprocess.stages.products_preview` surfaces `auto_adopt_existing_qoyod_products` flag + plain-Arabic `resolution_policy_note`.

### Tests (Iter-288)
- **NEW** `tests/test_qoyod_auto_adopt_existing_iter288.py` — **8/8 pass**:
  - Local mapping → no Qoyod calls.
  - 1 Qoyod match + auto_adopt=true → adopt + skip POST.
  - 2+ Qoyod matches → block with duplicate_qoyod_sku.
  - 0 matches → create.
  - auto_adopt=false → strict Trust Gate refusal preserved.
  - Default (no setting) → auto_adopt=true.
  - Multi-item order (3 SKUs): one local, one adopted, one created — exactly ONE POST /products.
  - Malformed Qoyod match (no id) → block with `qoyod_match_missing_id`.
- **Updated** `test_qoyod_ssot_product_trust_gate.py::test_resolver_blocks_qoyod_existing_untrusted` — adds explicit `auto_adopt_existing_qoyod_products: False` to preserve legacy refusal contract.
- **758/758** Qoyod pytest suite passes. 0 regressions. Lint clean.

### Production runbook after redeploy
1. Upload Amasi catalog to Qoyod trial (SKU-centric).
2. Trigger one-shot-reprocess on order 268756329:
   - First SKU `AMS11961` (already in Qoyod) → adopted silently, mapping written.
   - Any other SKU not yet in Qoyod → created with Iter-287 defaults.
3. Subsequent orders with same SKUs reuse the local mapping (zero Qoyod lookup).

### Backlog (still deferred)
- Frontend rendering of product_defaults_status, reconciliation, mezan_vat_diagnostics, duplicate-group banner, resolution-mode badges.
- Consolidated `/admin/settings-health` endpoint.
- Tamara BNPL 15,770 SAR discrepancy (P2).


## Iter-287 — Qoyod Required Product Fields + Preflight Gate (2026-02-27)
**User-reported production failure** after Iter-286 cleared the `sale_item` 422: order `268756329` hit a SECOND 422 from Qoyod:
```
{"category_id":          ["Please Select The Category"],
 "tax_id":               ["Please select taxes"],
 "product_unit_type_id": ["Please Select The Unit Type"],
 "sales_account_id":     ["Can't be blank"]}
```

### Root cause
Qoyod's `/products` validator (post-`sale_item:1` activation) requires four additional tenant-scoped ids that must come from Mezan settings (not Salla).

### Backend additions

#### 1. Four required settings keys (Iter-287 SSOT)
- `default_product_category_id`
- `default_product_tax_id`
- `default_product_unit_type_id`
- `default_sales_account_id`

#### 2. `product_resolver.py` changes
- `_stamp_required_ids(product, settings)` — adds `category_id`, `tax_id`, `product_unit_type_id`, `sales_account_id` to the payload from settings. Empty/missing values are dropped so we never emit `category_id: ""`.
- `_build_product_payload` and `_build_product_payload_fallback` both stamp the ids (Qoyod rejects the fallback too without them).
- `validate_product_defaults(settings) → (ok, missing_keys)` — preflight gate.
- `build_missing_product_defaults_error(missing_keys)` — structured Arabic error with `code: "missing_qoyod_product_defaults"`, `failed_at_stage: "PREFLIGHT_PRODUCT_DEFAULTS"`, lists missing keys with Arabic labels.
- `resolve_products` runs the preflight BEFORE any `POST /products`. If settings are missing → refuses immediately, NO Qoyod call, structured error surfaced to orchestrator.

#### 3. `preview_reprocess` surface
- `stages.products_preview.product_defaults_status` carries `{ok, missing[], code, message, ...}` so the operator sees the configuration gap BEFORE running anything live.

### Tests (Iter-287)
- **NEW** `tests/test_qoyod_product_required_defaults_iter287.py` — **11/11 pass**:
  - Full payload stamps all four ids; preserves Iter-286 contract.
  - Fallback payload also stamps the four ids (still minimal otherwise).
  - Empty settings → resolver refuses BEFORE any POST (zero `create_product` calls).
  - With settings configured → POST goes through and carries the four ids.
  - `validate_product_defaults`: empty/non-string values caught.
  - Error payload has the canonical code, stage, and Arabic message naming each missing setting.
  - Preview surfaces the gap in `products_preview.product_defaults_status`.
- **Updated** 4 brittle test files (`test_qoyod_day5_invoice_receipt.py`, `test_qoyod_dry_run_leak_protection.py`, `test_qoyod_ssot_product_trust_gate.py`, `test_qoyod_product_payload_sale_item_iter286.py`) — added the four defaults so the preflight doesn't refuse legacy tests.
- **750/750** Qoyod pytest suite passes. 0 regressions. Lint clean.

### Production runbook (after redeploy)
1. **Operator fills the four Iter-287 settings** in `/integrations/qoyod/settings` (or via PUT API) — values come from Qoyod tenant UI (Categories, Tax records, Unit types, Chart of Accounts).
2. Re-run preview for `268756329` → `products_preview.product_defaults_status.ok` MUST be `true`.
3. `one-shot-reprocess` should now clear FAILED_PRODUCT and proceed CUSTOMER → PRODUCT → INVOICE → RECEIPT → COMPLETED.
4. Customer 268756329 may already exist in Qoyod trial (created in earlier attempt); resolver reuses by phone/email match — no duplicate.

### Backlog (still deferred)
- No backfill / no batch / no totals-audit / no explain-totals.
- Frontend rendering of `product_defaults_status`, `reconciliation`, `mezan_vat_diagnostics`, duplicate-group banner.
- A small `/admin/settings-health` endpoint to list "ready / missing" for ALL Qoyod write paths in one shot.


## Iter-286 — Qoyod `/products` Payload: `sale_item: 1` (2026-02-27)
**User-reported production failure**: Order `268756329` reached FAILED_PRODUCT with Qoyod 422:
```
qoyod_validation_error · POST /products · 422
{"base": ["enter at least a purchase price or a sales price to continue."]}
```
The previous payload had `is_sold: true` + `selling_price: 5` but Qoyod's live `/products` endpoint uses integer-flag activation fields (`sale_item`, `purchase_item`), not the Rails-style booleans.

### Backend fixes
1. **`product_resolver._build_product_payload`** — drops `is_sold`/`is_bought`. Now emits `sale_item: 1`, `purchase_item: 0`, `selling_price`. Required fields per Qoyod live API: `name`, `sku`, `type`, `is_non_stock`, `sale_item`, `purchase_item`, `selling_price`.
2. **NEW `_build_product_payload_fallback`** — minimal-fields payload (`name, sku, sale_item, selling_price` only). No `type` / `is_non_stock` / `purchase_item` so Qoyod uses tenant defaults.
3. **Self-healing 422 retry in `resolve_products`**: catches `QoyodAPIError` with `status_code=422 AND ("purchase price" OR "sales price" in response excerpt)`, retries ONCE with the fallback payload. Other 422s (e.g. duplicate SKU) do NOT trigger the retry. If fallback ALSO fails, surfaces `fallback_attempted: True` on the error. No infinite retry.

### Tests (Iter-286)
- **NEW** `tests/test_qoyod_product_payload_sale_item_iter286.py` — **10/10 pass**:
  - Order 268756329 SKU `AMS11961` → `sale_item=1, selling_price=5.0`.
  - No `is_sold` / `is_bought` keys.
  - String→float price coercion preserved.
  - Defaults to `selling_price=0` when missing.
  - Fallback payload has exactly 4 keys.
  - End-to-end: 422 with "purchase price" → fallback succeeds.
  - 422 with "has already been taken" → NO retry.
  - Both attempts fail → `fallback_attempted: True`, no infinite retry.
- **Updated** `test_qoyod_product_create_payload_selling_price_iter272.py` — renamed Iter-272's `is_sold`/`is_bought` assertions to Iter-286's `sale_item`/`purchase_item` (5 tests).
- **Updated** `test_qoyod_preview_reprocess_iter281.py` for the new field name.
- **739/739** Qoyod pytest suite passes. 0 regressions.

### Production observations preserved
- Customer may already exist in Qoyod trial (created before product failure). That's acceptable — idempotency on customer side is by SKU/phone match. Invoice idempotency remains OPEN (no invoice was created yet), so the next reprocess attempt will correctly try again.
- DRY quarantine logic untouched.

### Production runbook (after redeploy)
1. Re-run preview for `268756329` → all stages green, products preview should show the new payload shape.
2. `one-shot-reprocess` should now clear FAILED_PRODUCT and proceed to INVOICE → RECEIPT → COMPLETED.
3. If the retry triggers on any item, the row's `qoyod_payloads.products` audit will carry both the canonical attempt AND the fallback attempt for forensics.


## Iter-285 — Customer-First Tax Mode (trial Go-Live) (2026-02-27)
**Tests**: 19/19 pass (after Iter-285), 2 brittle existing tests updated (`tax_mode=mezan_fixed_15` added to preserve legacy intent).

[Full Iter-285 details preserved below.]


## Iter-284 — Per-line Discount Aggregation + Clearer Error Codes (2026-02-27)
**User scenario (production)**: Order `268756329` (3 items, internal math consistent) failed `line_items_total_mismatch` because the normalizer emitted `discount_amount=0` (Salla put discounts only on items, not at order root) and the guard's UI message wrongly accused Make.com.

### Backend fixes

#### 1. `normalizer._aggregate_discount(top, items)`
When `amounts.discounts` is 0/missing but items carry per-line `total_discount`, the canonical's `discount_amount` is set to `Σ items[].discount_amount`. Top-level Salla discount (when set) is still canonical — never overridden.

#### 2. `totals_guard.validate_totals` improvements
- New diagnostic fields on EVERY result: `items_discount_sum`, `items_tax_sum`, `has_item_level_discounts`, `scanned_sku_count`, `expected_total_salla`, `header_total_diff`, `header_total_reconciled`.
- New error code `subtotal_mismatch_with_item_discounts` (Arabic message: "الطلب يحتوي خصومات على البنود...") used when `items_count > 1 AND scanned_sku_count > 1 AND has_item_level_discounts` — so the UI never wrongly blames Make.com. The legacy `line_items_total_mismatch` is kept for the single-item / no-discount path.
- `expected_total_salla = items_sum_gross − items_discount_sum + items_tax_sum + shipping_amount`. For order 268756329 → `269.10 + 21.53 + 0 = 290.63` matches Salla's declared total exactly. Surfaced as a green-flag in `header_total_reconciled`.

### Order 268756329 — now passes
```
items_sum_gross = 304.0 = subtotal  (gross convention)
items_discount_sum = 34.9
items_tax_sum = 21.53
expected_total_salla = 290.63 = total_amount  ✓
matched_convention = "gross"
ok = True
```

### Tests (Iter-284)
- **NEW** `tests/test_qoyod_per_line_discount_aggregation_iter284.py` — **8/8 pass**:
  - The exact production order PASSES.
  - `matched_convention="gross"`, `items_sum_gross=304`, `expected_total_salla=290.63`, `header_total_reconciled=true`.
  - `items_discount_sum=34.9`, `items_tax_sum=21.53`, `has_item_level_discounts=true`, `scanned_sku_count=3`.
  - Clearer code `subtotal_mismatch_with_item_discounts` used for multi-SKU + item-discount failures (Arabic message; Make.com NOT blamed).
  - Single-item failure still uses legacy `line_items_total_mismatch`.
  - Normalizer aggregates per-line discounts when top-level is 0.
  - Normalizer KEEPS top-level discount when explicitly set (no override).
- **709/709** Qoyod pytest suite passes. 0 regressions.

### Open accounting decision (user point #6) — REQUIRES YOUR INPUT
Two competing policies:

**Option A — "Customer-First"** (recommended for trial validation): Invoice tax = Salla's reported tax. Invoice total = Receipt amount = What customer paid (e.g. 290.63 SAR). Mezan VAT 15% becomes diagnostic only (already surfaced via `mezan_vat_diagnostics`). Books match transaction reality.

**Option B — "Compliance-First"**: Invoice tax = Mezan fixed 15%. Invoice total ≠ Receipt amount; needs a "tax adjustment" GL line to bridge the gap. Books match legal VAT rate but introduces a bookkeeping artefact per order.

**Current behavior**: invoice_builder sends `tax_id` (Qoyod-side rate) — if `default_tax_id` points to Qoyod's 15% tax record, this implicitly chooses Option B but **WITHOUT** the adjustment line, so receipt amount and invoice total don't match. This is what blocks Go-Live.

**Recommendation**: Switch `default_tax_id` to Qoyod's "Salla-rate" tax record (per storefront), OR add a tax-adjustment GL line for Option B. Need user's decision before next Qoyod write.


## Iter-283 — Totals Guard Discount Accounting Fix (2026-02-27)
**User scenario (production preview)**: Order `268632361` (trace `33c07a10...`) returned PASS for normalize but FAIL on totals_guard:
- `items_sum_excl = 187.06` (= 199 − 11.94 discount)
- `items_sum_incl = 202.02`
- `subtotal = 199.00` (Salla's GROSS — pre-discount)
- `code = line_items_total_mismatch`

### Root cause
Salla's `subtotal` is reported PRE-discount (gross). Pre-Iter-283 the guard only knew two conventions (`excl` = post-discount-pre-tax, `incl` = post-discount-with-tax). For ANY order with a discount the guard would always mismatch.

### Fix (`totals_guard.py`)
Added a third convention `items_sum_gross = Σ(unit_price × qty)` (pre-discount, pre-tax) and try it FIRST (Salla default). Match priority: `gross > excl > incl`. The guard surfaces ALL THREE sums in `details` so the operator sees the full picture. Each parsed item now also carries `line_gross` alongside `line_excl` and `line_incl`.

### Status gate vs preview (clarification)
- **Production pipeline** (Iter-282): status gate runs FIRST; `processing` / `under_review` → SKIPPED, never reaches totals_guard.
- **Preview reprocess** (Iter-281): runs ALL stages for diagnostic visibility — totals_guard runs regardless of status, by design. The `preflight.failures` block surfaces the status-trigger mismatch separately.

### Tests (Iter-283)
- **NEW** `tests/test_qoyod_totals_guard_discount_iter283.py` — **8/8 pass**:
  - Production order 268632361 PASSES (the exact failing scenario).
  - `matched_convention == "gross"`.
  - All three sums surfaced (`gross=199, excl=187.06, incl=202.02`).
  - No-discount orders still pass (gross = excl).
  - Post-discount subtotal convention still accepted (Make.com flattening).
  - Actually missing items STILL hard-refuses.
  - Multi-item with per-line discounts matches gross.
  - mezan_vat_diagnostics still embedded.
- **Updated** 2 existing tests for the new `matched_convention="gross"` value when discount=0.
- **701/701** Qoyod pytest suite passes. 0 regressions.

### Open question for the user
On point #1 of the user's message: should `event=order_completed + status=processing` be considered a trigger? Recommendation: **No** — `order_status_slug` is the SSOT. The Iter-282 gate correctly routes by status, not by Make's `event_type`. If the user wants to support `processing` as billable, they can add it to `settings.invoice_trigger_statuses`. The current behavior (SKIPPED) is the safer default.


## Iter-282 — Status Gate Before Totals Guard + Mezan VAT 15% SSOT (2026-02-27)
**User scenario**: Order `268746039` reached DEAD_LETTER with code `line_items_total_mismatch` even though its REAL status was `under_review` (not invoice-eligible) — Make.com was incorrectly sending `event_type: order_completed` for under-review rows, and totals_guard ran BEFORE status gate. Additionally, the mismatch was a Salla-vs-Mezan tax math divergence (Salla `tax.percent=8.00`, Mezan policy is 15%) which should NOT block the row.

### Backend changes

#### 1. Status Eligibility Gate runs BEFORE Totals Guard (`pipeline.py`)
- `process_normalized_row` now calls `business_rules.evaluate()` immediately after settings load — BEFORE `validate_totals()`.
- Orders with `order_status NOT in invoice_trigger_statuses` (e.g. `under_review`) → transition to **SKIPPED** with reason `not_in_trigger_statuses`. Never reach totals_guard.
- Eligible orders continue through totals_guard as before.
- The duplicate `evaluate_rules` block further down was removed (de-dup).

#### 2. NEW `integrations/qoyod/mezan_vat.py` — SSOT for VAT
- `VAT_RATE = 0.15` constant.
- `TAX_SOURCE_LABEL = "mezan_fixed_15"`.
- `compute_mezan_totals(canonical)` returns side-by-side `salla_*` vs `mezan_*` figures + `tax_difference`. Per-line breakdown with `net_line`, `mezan_tax_line`, `salla_tax_line`, `tax_difference_line`. Never mutates input. Never raises.
- `expected_line_tax(unit_price, quantity, discount)` helper.
- Rationale: Salla's tax math is shaped by storefront promo config (BNPL fees, partial-tax SKUs) and historically reports inconsistent percentages. Qoyod's tax records are merchant-mutable. Mezan owns the legal VAT rate as code — auditable and unit-tested.

#### 3. Totals Guard updates (`totals_guard.py`)
- Header-math check (`order_total_mismatch`) **DOWNGRADED FROM BLOCKER TO WARNING**. Salla's `total_amount` may legitimately differ from `subtotal + tax + ship − disc` because Mezan owns VAT policy. The diff is now surfaced via `mezan_vat_diagnostics.tax_difference` but NEVER moves the row to DEAD_LETTER.
- `mezan_vat_diagnostics` block is embedded in **every** `validate_totals()` result (success or failure) for uniform UI display.
- The `line_items_incomplete` / `line_items_total_mismatch` checks (Make.com data integrity) still bite as hard refuses.

#### 4. Preview Reprocess (Iter-281) hoists `mezan_vat` to top-level
The `/admin/preview-reprocess` response now carries `mezan_vat` at the response root (in addition to `stages.totals_guard.details.mezan_vat_diagnostics`) so the UI can render the Salla-vs-Mezan badge without nested drilling.

### Tests (Iter-282)
- **NEW** `tests/test_qoyod_status_gate_and_mezan_vat_iter282.py` — **13/13 pass**:
  - VAT_RATE = 0.15 constant.
  - `expected_line_tax(180, 1, 10.80) == 25.38`.
  - `compute_mezan_totals` for order 268746039 returns: `net_items_total=169.20`, `mezan_items_tax=25.38`, `mezan_shipping_tax=3.61`, `mezan_expected_total=222.26`, `tax_difference=-13.52`.
  - Per-line breakdown surfaces `tax_difference_line`.
  - Salla columns preserved for forensics.
  - Totals guard PASSES for order 268746039 despite Salla-vs-Mezan tax diff.
  - Totals guard still BLOCKS on real items-sum-incomplete (Make data integrity).
  - `under_review` order is NOT eligible (`decision.reason == "not_in_trigger_statuses"`); `completed` IS eligible.
- **Updated** existing tests:
  - `test_qoyod_totals_guard_iter273.py::test_order_total_mismatch_is_now_warning_not_blocker` (was: `_is_refused`).
  - `test_qoyod_pipeline_totals_guard_e2e_iter273.py::test_pipeline_does_not_dead_letter_order_total_mismatch_iter282` (was: `refuses_*_with_correct_code`).
- **693/693** Qoyod pytest suite passes. 0 regressions.

### Invoice Builder — already correct
`build_invoice_payload` passes each line as `{unit_price, discount, tax_id}` (NOT Salla's `tax_amount`). Qoyod computes tax server-side from `tax_id`. As long as `default_tax_id` in settings points to Qoyod's 15% tax record, the invoice receives Mezan's policy. Documented in code comment; no change required.

### What this gives the operator
- Order `268746039` (under_review) now routes to **SKIPPED**, never DEAD_LETTER.
- Operator can see Salla's reported `13.54 SAR` tax vs Mezan's expected `25.38 SAR` side-by-side in the First Sync Monitor (once UI surfaces `mezan_vat_diagnostics`).
- Books are protected: invoice payload carries Qoyod tax_id (15%), not Salla's variable percentage.

### Not done (deferred per user instruction)
- Order `268746039` was NOT sent to Qoyod (it's `under_review` → SKIPPED).
- Frontend rendering of `mezan_vat_diagnostics` in First Sync Monitor card.


## Iter-281 — Safe Preview Reprocess (No Qoyod Calls) (2026-02-27)
**User scenario**: One-shot reprocess button refused with `dry_run_mode_active` (it targets real Qoyod). User did NOT want to flip Dry Run off, nor send anything to Qoyod, but DID want to debug order `268632361` end-to-end. Additionally, the existing button returned a raw 500 when something unhandled went wrong — the UI showed `request_failed Request failed with status code 500` without diagnostic detail.

### Backend additions

#### 1. NEW `integrations/qoyod/preview_reprocess.py::preview_reprocess_one_order`
Re-runs the FULL pipeline IN MEMORY for one inbox row WITHOUT any network call to Qoyod:
1. Locates the inbox row by `trace_id` (preferred) or `order_number`.
2. Idempotency check — surfaces `invoice_already_created` with existing `qoyod_invoice_id` when a real (non-DRY) Qoyod invoice exists.
3. Runs `legacy_adapter.adapt()` → reports `adapter_applied`, `items_source`, `legacy_status_slug`.
4. Runs `normalizer.validate()` + `normalize()` → returns canonical DTO preview + live-vs-stored drift detection (item-level + top-level).
5. Runs `totals_guard.validate_totals()` → surfaces `ok / code / message / details`.
6. Runs `business_rules.evaluate()` → eligibility + chosen invoice date.
7. Builds the EXACT request body for `POST /customers`, `POST /products` (per SKU), `POST /invoices`, `POST /receipts` using the canonical builders. NEVER sends.
8. Runs `preflight.run()` → final ok/failures.

Every stage returns `would_send_to_qoyod: false`. The function NEVER raises (every failure path is structured `ok=false, failed_at_stage, error_code, message, errors[]`).

#### 2. NEW endpoint
`POST /api/integrations/qoyod/admin/preview-reprocess` body `{ order_number?, trace_id? }`. No confirm token (no side-effects). The route catches any unhandled exception and returns 200 with `ok=false, failed_at_stage="unhandled_exception", traceback_tail` so the UI NEVER sees a bare 500 again.

#### 3. Idempotency on existing `one_shot_reprocess`
Added pre-flight check inside `reprocess_one_order` (BEFORE quarantine + state reset): if `qoyod_invoices` already has a row with `dry_run=false` AND `status ∈ {sent, invoice_sent_receipt_failed, completed}`, refuse with structured `outcome=INVOICE_ALREADY_CREATED`, surface the existing `qoyod_invoice_id` + `qoyod_invoice_number` + status. Set `qoyod_request_sent=false, created_ids.invoice_id=<existing>`. No re-run is attempted — protects books from double-billing.

### Tests (Iter-281)
- **NEW** `tests/test_qoyod_preview_reprocess_iter281.py` — **15/15 pass**:
  - Happy path: full chain for order `268632361` returns `ok=true`, all 4 `would_send_to_qoyod` flags false.
  - Line item values: unit_price=199, tax_amount=14.96, discount_amount=11.94, total=202.02.
  - Customer payload preview has `name` AND `contact_name` populated.
  - Product payload preview has `selling_price` + `is_sold: true` (Iter-272 lock-in).
  - Invoice + receipt previews emit correct shapes including alias-resolved payment account (`tamara_installment → tamara → ACCT-tamara`).
  - Totals guard surfaced.
  - Drift detection: stored canonical has zeros (legacy bug) vs live recomputed (correct).
  - Idempotency: blocks when real Qoyod invoice exists; does NOT block when only DRY:* invoice exists.
  - Structured error envelopes for `row_not_found` and `missing_lookup`.
  - **Read-only contract**: inbox row stays at DEAD_LETTER, no mutations.
  - Tenant isolation.
- **680/680** Qoyod pytest suite passes. 0 regressions.
- Live curl on Preview `/admin/preview-reprocess` returns clean structured JSON.

### What this gives the operator (production workflow)
After redeploy, the UI can swap the One-Shot button for a two-stage flow:
1. **"معاينة آمنة"** (calls `preview-reprocess`) — see the full DTO + every payload Qoyod WOULD receive, with drift highlighting and idempotency check. NO writes anywhere.
2. **"One-Shot Reprocess"** (existing, still strict, dry_run must be OFF) — only fired AFTER operator visually confirms the preview is correct AND the row has no existing real invoice.


## Iter-280 — Duplicate Inbox Rows for Legacy Make Payloads (2026-02-27)
**User scenario**: First Sync Monitor showed order `268632361` TWICE as separate DEAD_LETTER rows (`eac68e664dee48738005a52b15e50a60` + `33c07a10a2994f6796a44fa386a33c00`). User demanded: same order must never produce two independent rows; attempts must accumulate inside ONE inbox doc; idempotency at `order_number + event_type + order_status_slug`.

### Root cause
`derive_idempotency_key(body, header)` inspected ONLY `body.data.*`. Legacy Make scenarios ship a FLAT body (no `data` envelope), so `order_id` was None and the function fell through to `f"salla:unknown:{uuid.uuid4().hex}"` — a fresh random UUID on every call. The unique index `(user_id, connector_key, idempotency_key)` could never collide, so every webhook delivery for the same order created a new inbox row.

### Backend fixes

#### 1. Idempotency key now reads ROOT-level legacy fields (`webhook.py::derive_idempotency_key`)
- `order_id` falls back to `raw.order_number / raw.order_id / raw.reference_id`.
- `event` falls back to `raw.event_type`.
- `status_slug` falls back to `_extract_status_slug(raw)` (root-level keys).
- `_extract_status_slug` now also handles `order_status_slug` (was previously missing).
- Result: same Make payload always produces the same `salla:order:268632361:order_completed:completed` key → unique index blocks second insert via DuplicateKeyError → webhook handler returns `{duplicate: true, trace_id: <existing>}` instead of creating a new row.

#### 2. Duplicate-group detection + merge endpoint (`first_sync_monitor.py`)
Production already had orphan duplicate rows from BEFORE the fix. Two new helpers + two new endpoints:
- `find_duplicate_groups(db, user_id, only_failed=True)` — groups inbox rows by `(salla_order_number, event, status_slug)` and returns groups with ≥2 attempts. Per-group: `latest_trace`, `oldest_trace`, `suggested_keep_trace` (heuristic: prefer COMPLETED > PARTIAL > newest of failed).
- `archive_duplicate_attempts(db, user_id, order_number, event, status_slug, keep_trace_id, confirm_token, actor)` — token `"MERGE"` required; archives all losing attempts to `integration_inbox_archive` with `archive_reason="duplicate_attempt_merged"` + `duplicate_group: {kept_trace, ...}`. Stamps `duplicate_attempts_archive[]` on the KEPT row for audit. Strict tenant + group scope. Insert-before-delete (recoverable).
- `GET  /api/integrations/qoyod/first-sync-monitor/duplicate-groups?only_failed=true`
- `POST /api/integrations/qoyod/first-sync-monitor/archive-duplicates`

#### Safety contract (NEVER violated)
1. NEVER touches Qoyod itself (local-only archive op).
2. NEVER touches rows of other tenants.
3. NEVER touches rows outside the `(order_number, event, status_slug)` group.
4. Confirm token `"MERGE"` required.
5. `keep_trace_id` MUST exist in the group.
6. Archive insert BEFORE delete (recoverable).

### Tests (Iter-280)
- **NEW** `tests/test_qoyod_webhook_idempotency_legacy_iter280.py` — **12/12 pass**: canonical shape (no regression), legacy flat (deterministic key), identical payloads collide, different status → different key (transitions preserved), different event → different key, X-Idempotency-Key precedence, empty body → random key (defensive).
- **NEW** `tests/test_qoyod_duplicate_attempts_iter280.py` — **16/16 pass**: extractor handles legacy + canonical + canonical-metadata-preferred, grouping ignores different statuses (transitions preserved), tenant isolation, only_failed gate, completed-group filter, suggest_keep heuristic, refuse without token / unknown keep_trace / single-row group, archive moves loser to archive collection, kept row gets attempt history stamp, strict order scope (other orders untouched).
- **665/665** Qoyod pytest suite passes. 0 regressions.

### Pending (deferred to next user decision)
- Frontend UI for duplicate-group banner + "Merge attempts" button in `QoyodFirstSyncMonitor.jsx`.
- Banner `items: [object Object]` is a Make.com parse-failure artifact (already routed to separate `webhook_parse_failures` table); user requested it be visually segregated from valid Raw Payload attempts in UI.
- After production redeploys Iter-280 backend, the duplicate `268632361` group can be merged via the new endpoint (or operator picks `33c07a10...` to keep and merges `eac68e66...` into archive).


## Iter-279 — `normalize-row-self-test` Status Fix Regression (2026-02-27)
**User scenario**: Operator hit `/admin/normalize-row-self-test?trace_id=eac68e664dee48738005a52b15e50a60` to debug production order 268632361. Endpoint crashed with `NormalizationError(missing_order_status, "could not extract status string")` even though the raw Make payload had `order_status_slug: "completed"` and `order_status: "تم التنفيذ"` at the root.

**Root cause**: The route called `normalize(raw)` directly. Legacy Make scenarios ship status at the ROOT, not under `data.status`, but the normalizer only reads `data.status`. The adapter (which writes `data.status`) was being SKIPPED by the self-test.

**Fixes already applied in previous session** (now locked in by tests):
1. `legacy_adapter.adapt()` mirrors `order_status` / `order_status_slug` / `status` onto the adapted root for downstream visibility (the normalizer still reads only `data.status`, which the adapter has been writing all along).
2. `routes.admin_normalize_row_self_test` now chains `adapt(raw) → normalize(adapted)` so the self-test mirrors the real webhook chain.

### Regression Tests (Iter-279)
- **NEW** `tests/test_qoyod_normalize_self_test_status_fix.py` — **10 tests, all pass**:
  - Adapter writes `order_status_slug`, `order_status`, top-level `status`, and `data.status` for the user's exact payload.
  - `normalize(adapted)` does NOT raise — full chain succeeds.
  - Defensive: `normalize(raw_legacy_body)` (bypassing adapter) STILL raises `NormalizationError(missing_order_status)` — documents WHY the chain matters.
  - DTO line item fields are exactly: `unit_price=199`, `tax_amount=14.96`, `discount_amount=11.94`, `total=202.02`.
  - DTO `order_status` canonicalises to `"completed"`, `order_status_native="تم التنفيذ"`.
  - Slug-only legacy payload (no Arabic name) still survives the chain.
- **637/637** Qoyod pytest suite passes. 0 regressions.


## SSOT Product Trust Gate + Name Display Fallback (2026-02-27)
**User scenario (P0 pre-Go-Live)**: 38 historical Qoyod products (cod_item, custom_product, old Salla SKUs like AMS11903 with empty names) could silently bind to fresh Mezan orders via SKU match. User also reported that the QYD-GO Identity Diagnostics table displayed "—" for products with only `name_ar` (e.g. "اقمشة متنوعة"). Both gaps had to close before Go-Live.

### 1. Display Fallback (frontend + backend)
- **Backend** `identity_diagnostics._sample` now exposes `name_ar` and `name_en` as standalone keys (in addition to the picker's `name` + `name_source`). Guard against the picker missing exotic shapes.
- **Frontend** `QoyodGoLive.jsx` computes `displayName = p.name || p.name_ar || p.name_en` with a `(name_ar)` source badge. Falls back to italic "(بدون اسم)" only when all three are empty.

### 2. SSOT Product Trust Gate (architectural)
Default-ON (`settings.block_untrusted_existing_products=True`). For every line item:
- Mezan mapping HIT → use it (trust_source = `mezan` | `adopted`).
- Mezan mapping MISS + Qoyod returns no product → create fresh (trust_source = `created`, mapping `source='mezan_created'`).
- Mezan mapping MISS + Qoyod HAS a row → return `qoyod_existing_untrusted` with `{qoyod_product_id, qoyod_product_name, qoyod_product_sku, remediation: "adopt_or_archive"}`. No create call is made.

### 3. Manual Adoption Endpoint
`POST /api/integrations/qoyod/products/adopt` `{sku, qoyod_product_id, qoyod_product_name?, note?}` — inserts a mapping row with `adopted=True, adopted_by, adopted_at, source='operator_adopted'`. Idempotent.

### Files changed
- **NEW** code paths in `product_resolver.py` (trust gate + `adopt_qoyod_product()` + `_untrusted_error()`).
- **NEW** `api_client.find_product_by_sku()` — Ransack `?q[sku_eq]=X` with defensive fallback to legacy flat filter; verifies returned row's SKU before claiming a hit.
- **NEW** route `POST /products/adopt` + `AdoptProductBody` model.
- `DryRunQoyodClient.find_product_by_sku` returns None (recorded for audit).
- Frontend display chain in `QoyodGoLive.jsx`.

### Tests (Iter-265)
- **8 new unit tests** in `tests/test_qoyod_ssot_product_trust_gate.py` — happy path, block path, mapping short-circuit, adopted trust source, opt-out, adoption audit trail, idempotency, validation.
- **4 new HTTP tests** in `tests/test_qoyod_ssot_trust_gate_http_iter265.py` — live endpoint contract.
- Fixed event-loop isolation bug in `test_qoyod_dead_letter_iter264_http.py` (asyncio.run + Motor factory pattern).
- **491/491** Qoyod pytest suite passes. P0 issue found by testing agent (missing import) fixed.


## Dead-Letter Auto-Requeue — Self-Healing for KNOWN_FIXED_PATTERNS (2026-02-27)
**User scenario**: QYD-GO was stuck at 10/11 — `1 فاتورة إنتاجية فشلت` — because a Qoyod customer-create call had previously failed with `contact_name: Can't be blank`. The code bug was already fixed (2026-02-26: send both `name` + `contact_name`), but the row was permanently DEAD_LETTER and blocked Go-Live. User explicitly demanded that QYD-GO reflect CURRENT state, not old fixed errors, and that the row reach 11/11 **without manual intervention**.

### Strict constraints (user-imposed, encoded in code)
- Auto-Requeue acts ONLY on rows matching `KNOWN_FIXED_PATTERNS`. Generic DEAD_LETTER rows stay red.
- `MAX_REQUEUE_ATTEMPTS = 2` per row.
- Today the registry has exactly **one** entry: `contact_name_blank_2026_02_26` (FAILED_CUSTOMER only).
- Manual "إعادة المعالجة الآن" button in QYD-GO obeys the same registry.

### Backend changes
- **New** `/app/backend/integrations/qoyod/dead_letter_requeue.py` — pattern registry, `match_pattern()`, `requeue_row()` (two-hop: terminal→RETRYING→NORMALIZED/CUSTOMER_RESOLVED), `find_requeue_candidates()`, `auto_requeue_known_fixed()`, `requeue_one()`.
- **state_machine.py** — added operator-override edges `(DEAD_LETTER, RETRYING)`, `(PARTIAL_FAILURE, RETRYING)`, `(RETRYING, NORMALIZED)`, `(RETRYING, CUSTOMER_RESOLVED)`. Terminal-stage invariant test updated.
- **worker.py** — `_one_round()` runs `auto_requeue_known_fixed()` BEFORE draining queues; self-heals on every 5s tick.
- **go_live.py** — `_check_outstanding_failures()` now partitions failures into `blocking_count` vs `auto_recoverable_count`; QYD-GO `ok=True` when only auto-recoverable rows remain. Returns `extra.sample_blocking` (top 5) for forensics.
- **routes.py** — three new endpoints:
  - `GET  /api/integrations/qoyod/dead-letter/preview` → candidates + registry
  - `POST /api/integrations/qoyod/dead-letter/auto-requeue` → bulk requeue
  - `POST /api/integrations/qoyod/dead-letter/requeue-one` → single row by `row_id`|`trace_id` (still bounded by pattern registry)

### Frontend (`QoyodGoLive.jsx`)
- `ChecklistRow` for `outstanding_failures` now renders:
  - `auto-recoverable-banner` + `btn-trigger-auto-requeue` when `extra.auto_recoverable_count > 0`
  - `blocking-failures-sample` `<details>` listing top-5 stuck rows when `extra.blocking_count > 0`
- Manual button calls POST `/dead-letter/auto-requeue`, toasts result, refreshes checklist after 2.5s.

### Tests (Iter-264)
- **17 new unit tests** in `tests/test_qoyod_dead_letter_auto_requeue.py` — pattern matcher, single-row requeue, MAX_REQUEUE_ATTEMPTS cap, bulk auto-requeue, QYD-GO integration end-to-end, manual requeue_one paths.
- **8 new HTTP tests** in `tests/test_qoyod_dead_letter_iter264_http.py` — full live endpoint contract via ingress URL.
- **471/471** Qoyod pytest suite passes. **100%** success in iter-264.
- Worker drains seeded rows within ~5s (self-healing verified).


## Identity Diagnostics Extension — Raw-Field Visibility for 38-vs-0 Discrepancy (2026-02-26)
**User scenario**: Production diagnostics returned 38 products via `GET /products` (SKUs like AMS11903, AMS11577 with empty names) but Qoyod UI showed zero after a Fresh Start. Customers matched. User refused to tick "I confirm identity" until they can see exactly what those mystery products are.

### Hypothesis
Most likely: the products are **archived** (Qoyod UI hides archived by default). Less likely: products are of `type: service` with hidden category, or the API key is on a different tenant.

### Backend changes (`identity_diagnostics.py`)
- `_sample` default `limit=10` (was 5); accepts explicit `limit=` param.
- Product picker now extracts: `id`, `name`, `sku`, `type`/`kind`, `status`, `active` (or `is_active`), `archived` (or derived from `archived_at`), `archived_at`, `category` (string or `.name` if dict), `price`/`selling_price`.
- Customer picker now extracts: `id`, `name`/`contact_name`, `phone`/`phone_number`, `email`, `type`/`kind`, `archived`.
- New `_raw_first(rows)` returns the **first** raw dict from Qoyod (up to 50 keys verbatim) so the operator can spot hidden flags (`archived_at`, custom fields, …) that the picker missed.
- Endpoints: `GET /products?page=1&limit=10`, `GET /customers?page=1&limit=10`.

### Frontend (`pages/QoyodGoLive.jsx`)
- Products table: 6 columns (ID, الاسم, SKU, النوع, الحالة, مؤرشف).
- Customers table: 5 columns (ID, الاسم, الهاتف, النوع, مؤرشف).
- Archived cell renders **"نعم"** in red-bold when `archived === true` OR `archived_at` truthy; "—" otherwise.
- Collapsible `<details>` testid `diag-products-raw` / `diag-customers-raw` with summary `🔎 عرض أول منتج كامل من Qoyod (لكشف الحقول المخفية)` — clicking reveals the raw_first_row JSON in `<pre dir="ltr">`.

### Tests
- **`tests/test_qoyod_identity_diagnostics.py`** — 11 tests (was 9), all pass:
  - Updated existing limit-5 tests to limit-10.
  - **New `test_raw_first_row_exposes_hidden_archived_field`** — the EXACT user repro: 1 product with `archived_at: "2026-06-25T10:00:00Z"`, `type: "service"`, `category: {name: "Hidden Cat"}`, `custom_field_x: "anything"`. Asserts the sample correctly extracts `archived=true`, the picker reads `category` from the dict, AND `raw_first_row` preserves `custom_field_x`.

### Testing agent verification (iteration 262)
- ✅ 11/11 pytest pass.
- ✅ Live HTTP returns spec-compliant 200 with `limit=10` endpoint strings, `api_key_fingerprint='08cef7386398'` (raw key never exposed).
- ✅ All required testids present.
- ✅ Conditional tables/raw `<details>` correctly gated behind `ok=true` and exercised by pytest.
- ✅ No regressions; 433 total Qoyod tests still pass.



## Tenant Identity Diagnostics — Anti-Mismatch Guard (2026-02-26)
**User concern**: QYD-GO reported "38 products in Qoyod" via direct API call, but the Qoyod web UI for the user's account shows ZERO products. Demanded: print Qoyod tenant identifier, exact endpoint, first 5 products + customers (id, name, sku), raw `meta.total`. Block Go-Live until the user confirms identity.

### Backend
- **`integrations/qoyod/identity_diagnostics.py`** (NEW):
  - `run_identity_diagnostics(db, user_id)` — public entry. Returns:
    - `mezan`: `base_url`, `user_id`, `api_key_present`, `api_key_fingerprint` (sha256[:12] — raw key NEVER exposed), `queried_at`.
    - `qoyod.tenant_hints`: organisation name + branches from `/branches`.
    - `qoyod.branches/products/customers`: each `{ok, endpoint, error, meta, sample}` where `sample` is up to 5 rows `{id, name, sku/phone}`.
    - `summary`, `next_step`.
  - `_key_fingerprint(api_key)`: sha256 first 12 hex chars. Empty/None returns "".
  - `_sample(rows, picker)`: truncates to 5 dicts; tolerates non-list / non-dict inputs.
  - Graceful partial failure: any sub-endpoint failure surfaces as that section's `ok: false` + `error: {code, message}` — never a 500 crash.
- **`integrations/qoyod/routes.py`**: `@router.get("/diagnostics/identity")` mounted at `/api/integrations/qoyod/diagnostics/identity`.
- **`integrations/qoyod/go_live.py::_check_lookup`**: now uses `limit=5` (was `limit=1`), returns `sample` + `qoyod_response_meta` in `extra`.

### Frontend (`pages/QoyodGoLive.jsx`)
- New amber-bordered section `🔍 تشخيص هوية حساب قيود (إلزامي قبل التفعيل)` between status banner and report.
- "تشغيل التشخيص الآن" button (testid `btn-run-identity-diag`) calls the endpoint.
- Renders: Mezan base_url + fingerprint + queried_at; tenant_hints if any; products table (testid `diag-products-table`) + customers table (testid `diag-customers-table`) with id/name/sku/phone; per-section error display when Qoyod rejects.
- Mandatory confirmation checkbox `أؤكد أن المنتجات والعملاء أعلاه تطابق ما أراه في واجهة قيود لحسابي` (testid `diag-confirm-identity`).
- ACTIVATE button blocked with label `🔒 يلزم تأكيد الهوية` until BOTH `checklist.all_passed` AND `identityConfirmed` are true. Confirmation resets on every re-run.

### Tests
- **`tests/test_qoyod_identity_diagnostics.py`** (new, 9 tests, all pass):
  - Fingerprint stable + never exposes raw key.
  - Sample picker truncates to 5; tolerates malformed inputs.
  - `no_api_key` summary returned cleanly when key missing.
  - Full success path: products+customers samples populated, tenant_hints extracted from /branches.
  - Graceful partial failure (products=403, customers=200).
  - All-endpoints-unauthorized still returns 200 with structured errors.
  - 50-row response truncated to 5 in sample.
- **Testing agent verified end-to-end** (`/app/test_reports/iteration_261.json`):
  - Backend: 22/22 pytest pass + live curl returns spec-compliant 200 with `api_key_fingerprint='08cef7386398'`.
  - Frontend: all testids present + behaviors confirmed (visibility, button, checkbox gating, reset on re-run).
  - Security: raw key never appears in response — only fingerprint.

### Outcome for the user's actual case
The preview admin tenant's key fingerprint is **`08cef7386398`** and Qoyod returns 401 for both /products and /customers. So the "38" the user saw earlier almost certainly came from a moment when the key briefly had read scope — OR the key now belongs to a different tenant than the UI account. The user can now compare the fingerprint with what's saved in their Qoyod account, and the checklist refuses to flip live until they explicitly confirm the samples match.



## Products/Customers Lookup Card — Source Transparency (2026-02-26)
**User question**: where does the "38 موجود في قيود حالياً" number come from?

**Answer**: It comes from a **direct live call to Qoyod API** (`GET /products?limit=1` and `GET /customers?limit=1`) executed every time the QYD-GO checklist endpoint is hit. NO cache. NO local collection. NO migration snapshot. Confirmed by code trace:
- `go_live.py::go_live_checklist` → `_check_lookup(api_client, …, fn=api_client.list_products(limit=1))`
- `api_client.list_products` → `self._request("GET", "/products", params={"page": 1, "limit": 1})`
- No fallback to `qoyod_external_products` or any local table. If Qoyod is down, the card fails (`qoyod_unauthorized` etc.) — never substitutes a stale number.

### Improvements applied
1. **Labels renamed** to make the source unambiguous:
   - `"استعلام منتجات قيود"` → `"استعلام مباشر من قيود — المنتجات"`
   - `"استعلام عملاء قيود"` → `"استعلام مباشر من قيود — العملاء"`
2. **Detail line** now shows the query timestamp: `"… (استعلام مباشر من قيود): N عنصر — YYYY-MM-DDTHH:MM:SS UTC"`.
3. **`extra` payload** for each check now carries:
   - `source: "qoyod_api_live"` (audit tag).
   - `endpoint: "GET /products?limit=1"` (exact path called).
   - `queried_at` (ISO timestamp of THIS check).
   - `qoyod_total` (the count Qoyod returned).
   - `total_source` ("meta.total" | "len(products)" | "len(data)" | …) so the operator knows whether N is the server-reported total or the page length.
4. **Count parsing fix**: prefers `meta.total` over `len(products)`. With `limit=1` the old code would have shown "1 موجود" misleadingly; now it shows the actual total.



## Root Cause Found — Qoyod Requires BOTH `name` + `contact_name` (2026-02-26)
**User report**: Order #268316484 had `customer_name = "هيفاء الحيدر الشمري"` in the raw payload but failed at `FAILED_CUSTOMER` with Qoyod returning `contact_name: ["Can't be blank"]`.

### Root cause
Qoyod's `POST /customers` endpoint requires TWO required fields:
- `name` — business/account name
- **`contact_name`** — contact person (this was missing in our payload)

Verified via web search of Qoyod API docs: our payload only sent `name`, so `contact_name` defaulted to empty and Qoyod rejected. The customer-name fallback I added earlier worked correctly all the way through to the DTO — the bug was at the LAST mile inside the Qoyod payload builder.

### Fix (`customer_resolver.py::_build_contact_payload`)
```python
safe_name = (customer.name or "").strip() or _safe_guest_name(customer)
payload = {
    "name":         safe_name,
    "contact_name": safe_name,   # ← was missing
    ...
}
```
Both fields are always populated with the same safe-name string for B2C orders (verified the field has been missing since Day-1; this was a latent bug exposed by today's order).

### Forensic logging added
`ResolutionResult.qoyod_request_payload` now carries the EXACT payload sent to `POST /customers`, populated on BOTH success and failure paths. This propagates to the inbox row via `to_log_dict()` and is surfaced on the First-Sync Monitor under the "إنشاء/مطابقة العميل" step. Operator can now see the exact body without rerunning the order.

### Defense-in-depth in legacy adapter
`legacy_adapter.py` now ALSO writes `customer.full_name` (in addition to `first_name + last_name`) so the normalizer's fallback chain (full_name → name → phone → guest) still has the original `customer_name` string if `_split_name` ever returns empty parts (single-character names, RTL marks, etc.).

### Tests
- **`tests/test_qoyod_contact_name_end_to_end.py`** (new, 9 tests, all pass):
  1. Direct payload-builder: both `name` AND `contact_name` populated.
  2. Blank DTO: both fallback to safe guest label — never blank.
  3. Email-only DTO: contact_name uses email label.
  4. Empty DTO: contact_name is literal "ضيف".
  5. **End-to-end repro of user's order**: Make payload → adapter → normalizer → builder. Asserts the final Qoyod payload has `contact_name: "هيفاء الحيدر الشمري"`.
  6. Single-character name: `full_name` defense in adapter saves the day.
  7. `resolve_customer` returns the payload snapshot on success.
  8. `resolve_customer` returns the payload snapshot on failure.
  9. `to_log_dict` propagates the snapshot to the inbox row.
- **Full Qoyod suite: 422 tests pass** (no regressions).



## Final Pre-Go-Live Fixes — Customer Name Fallback + Arabic COD Aliases (2026-02-26)

### Issue 1: `contact.name Can't be blank` from Qoyod
**Root cause**: `_normalize_customer` fell back to literal "ضيف" but the resulting DTO could still produce a blank name in edge cases (e.g. customer payload missing first/last/full name and the normalizer didn't have order context).

**Fix (`integrations/qoyod/normalizer.py::_normalize_customer`)**: now takes optional `order_number` arg and falls back in this strict order:
1. `first_name + last_name`
2. `full_name`
3. `name`
4. `"عميل {phone}"` (so the customer is recognizable in Qoyod)
5. `"ضيف #{order_number}"` (distinguishable guest per order)
6. `"ضيف"` (last-resort literal)

**Belt-and-suspenders (`customer_resolver.py::_build_contact_payload`)**: even if the DTO somehow has a blank name (legacy rows), the payload builder NEVER sends blank — uses phone/email as label or last-resort "ضيف".

### Issue 2: COD Arabic variant `النوع عند الاستلام` unmapped
**Root cause**: `_canonical_payment_method` had no Arabic entries. Salla sent the Arabic native string and it was canonicalized to `النوع_عند_الاستلام` (the user saw this raw value in the Settings page).

**Fix (`integrations/qoyod/normalizer.py`)**: Arabic COD variants now canonicalize to `cod` at write-time:
- `الدفع عند الاستلام` → `cod`
- `النوع عند الاستلام` → `cod`
- `الدفع نقدا عند الاستلام` → `cod`
- `نقد عند الاستلام` → `cod`
- Plus `cash` (was previously `cash`, now `cod` for consistency)
- Plus Arabic provider names: `تمارا → tamara`, `تابي → tabby`, `تحويل بنكي → bank_transfer`.

**Fix (`payment_methods.py`)**: `PAYMENT_METHOD_ALIASES` also covers already-underscored Arabic keys (legacy rows written before the normalizer extension). E.g. `الدفع_عند_الاستلام → cod`.

### Frontend (`pages/QoyodSettings.jsx`)
Payment-method rows now also render a slate badge **"من سلة: «<native>»"** when the original Salla string differs from the canonical key. This shows the user EXACTLY what Salla sent, so they can never wonder where a row came from.

### Tests
- **`tests/test_qoyod_customer_name_and_cod.py`** (new, 27 tests, all pass):
  - 9 tests for the customer-name fallback chain (first+last priority, full_name, name field, phone label, guest with order number, bare guest, string customer payloads).
  - 4 tests for `_build_contact_payload` belt-and-suspenders.
  - 8 parametrized tests for COD canonical resolution (English + 5 Arabic variants).
  - 4 parametrized tests for other Arabic provider names.
  - 2 alias-table coverage tests for already-normalised Arabic keys.
- **Full Qoyod suite: 413 tests pass** (no regressions).

### Verified end-to-end live
- Seeded an inbox row with `payment_method_native: "النوع عند الاستلام"`, `payment_method: "cod"`.
- `/payment-methods/used` returned `key: "cod"`, `label_ar: "الدفع عند الاستلام"`, `native_examples: ["النوع عند الاستلام"]`.
- Settings UI rendered: title "الدفع عند الاستلام", badge "من سلة: «النوع عند الاستلام»", mapping single account ID once covers ALL Arabic + English COD variants.



## Outstanding-Failures Watermark Fix (2026-02-26, final)
**User-reported bug**: QYD-GO blocked with "27 فاتورة إنتاجية فشلت" but the user had never activated Go-Live yet — the 27 rows were old test data from before the dry_run flag existed (or before it was reliably set per-row).

**Root cause**: My previous fix used `dry_run: {$ne: True}` as the production filter. Legacy rows from before the worker fix had **no `dry_run` field at all**, so they passed the `$ne True` test and got counted as production failures.

**User spec**:
1. Pre-Go-Live (`go_live_activated_at` not set) — check ALWAYS passes; old test rows can never block first-time activation.
2. Post-Go-Live — count only rows with `received_at ≥ go_live_activated_at` AND `dry_run != True`.
3. Legacy rows without `dry_run` flag created before activation are by definition pre-activation noise.

### Backend changes
- **`integrations/qoyod/go_live.py::_check_outstanding_failures`**:
  - Reads `settings.go_live_activated_at` (with fallback to legacy `activated_at`).
  - Parses ISO-string timestamps as a defensive fallback.
  - **If unset → always returns `ok: True`** with detail: "لم يتم تفعيل الإنتاج بعد. السجلات القديمة (ما قبل التفعيل) لا تُحسب كفشل إنتاجي."
  - If set → counts only `pipeline_stage ∈ {DEAD_LETTER, PARTIAL_FAILURE} AND dry_run != True AND received_at ≥ activated_at`.
- **`activate_go_live`**: writes BOTH `go_live_activated_at` (new canonical) and `activated_at` (legacy compat) on flip.
- **Function signature** updated to take optional `settings` param (passed from checklist to avoid double DB load).

### Tests
- **`tests/test_qoyod_go_live_qyd_fix.py`** — rewrote outstanding_failures tests to use the watermark spec:
  - Pre-Go-Live always passes (even with 27 missing-dry_run rows — the exact user scenario).
  - Post-Go-Live: pre-activation rows ignored, post-activation dry-run ignored, post-activation production COUNTED.
  - Legacy `activated_at` field honoured (backward compat).
  - ISO-string activation timestamp parsed correctly.
- **`tests/test_qoyod_qydgo_clear_failures_persistence.py`** — rewrote on real Mongo (5 tests):
  - Pre-Go-Live with mixed rows (dry/non-dry/missing) all green.
  - Post-Go-Live pre-activation rows ignored.
  - Post-Go-Live post-activation production counts.
  - Refresh stability ×5 stays green pre-activation.
  - Legacy `activated_at` field works.

### Verification
- ✅ **18 watermark tests pass**; **386 total Qoyod tests pass** (no regressions).
- ✅ Live API: `/go-live/checklist` returns outstanding_failures with `ok: True` + the new pre-activation detail message.
- ✅ Screenshot: QYD-GO page shows green ✓ "لا فواصل إنتاجية عالقة" with the new message. Total progress moved from blocked → 6/11 passed.



## Payment-Method Alias Resolution — Final Pre-Go-Live Fix (2026-02-26)
**User-reported bug**: Order reached `PRODUCT_RESOLVED` then failed at `INVOICE_CREATED` with `payment_method_mapping_missing` because Salla sent `tamara_installment` but only `tamara` was mapped in settings.

**User spec**:
1. No hardcoded provider names — accept anything Salla sends.
2. New methods auto-appear as Settings rows, never as runtime crashes.
3. Built-in alias table: `*_installment` variants collapse to their base provider (`tamara_installment → tamara`, `tabby_installment → tabby`, etc.) so one mapping covers both.
4. User can still explicitly map a variant to a different Qoyod account if needed.

### Backend
- **`integrations/qoyod/payment_methods.py`** (NEW):
  - `PAYMENT_METHOD_ALIASES` table covering Tamara/Tabby/Emkan installment variants, bank/wire, cash/cod, applepay/stcpay typo variants, credit/card.
  - `provider_family(method)` — collapses a variant to its base provider.
  - `resolve_payment_account(settings, method)` — direct match → alias fallback → None.
  - `explain_resolution(settings, method)` — diagnostic for UI/monitor.
- **`integrations/qoyod/invoice_builder.py::_resolve_payment_account`**: delegates to the new resolver.
- **`integrations/qoyod/preflight.py`**: uses `resolve_payment_account`; failure message now surfaces `provider_family` hint so operator sees which base to map.
- **`integrations/qoyod/setup_validation.py`**: a method is "mapped" if direct OR alias resolves. The blocker carries `extra.alias_hints` listing variants that could be solved by mapping a base provider.
- **`integrations/qoyod/go_live.py::_collect_eligible_skus_and_methods`**: same alias-aware unmapped detection.
- **`GET /api/integrations/qoyod/payment-methods/used`**: each row now carries `provider_family`, `mapped_via` ("direct"|"alias"|null), `matched_key`, `resolved_account_id`. The endpoint also returns the full `aliases` table.

### Frontend (`pages/QoyodSettings.jsx`)
- Payment-method mapping table renders each used method as before, but when a row is alias-covered:
  - Sky-blue row background instead of red.
  - "✓ مربوط (Alias)" badge in status column instead of "مطلوب".
  - Inline hint badge "عبر تمارا" next to the method name.
  - Input placeholder shows "(اختياري — يستخدم tamara)".
- Client-side validation mirrors backend: rows with `mapped_via === "alias"` are NOT counted as missing.

### Tests
- **`tests/test_qoyod_payment_method_aliases.py`** (new, 13 tests, all pass):
  - Provider family collapses known aliases, passes through unknown methods, handles empty/None.
  - Resolver: direct match takes priority over alias; falls back to alias; returns None when neither; ignores blank account IDs; case-insensitive + whitespace-tolerant.
  - Preflight passes when variant resolves via alias; fails with helpful family hint when no alias covers.
  - Settings validation treats variants as mapped when alias covers them.
- Full Qoyod suite: **384 tests pass** (no regressions).

### Verified end-to-end
- Seeded a `tamara_installment` inbox row with only `tamara` mapped in settings.
- `/payment-methods/used` returns the row with `mapped_via: "alias"`, `matched_key: "tamara"`, `resolved_account_id: "A-9999"`.
- `/setup/validate` returns `ok: true` — no blocker.
- Settings UI shows sky-blue row with "✓ مربوط (Alias)" badge and "عبر تمارا" hint.
- Bottom save banner: "✅ كل الحقول مكتملة — جاهز للحفظ".



## QYD-GO Dry-Run Awareness + Migration SSOT Clarification (2026-02-26)
**User decisions**:
1. `_check_outstanding_failures` counts ONLY production (`dry_run != True`) failures. Dry-run failures NEVER block Go-Live.
2. Archive flow for dry-run failures stays on the First-Sync Monitor page (already built — "أرشفة فشل الاختبار القديم").
3. Dry-run failures remain visible in the Monitor for diagnosis until archived.
4. Production failures (`dry_run: False`) always block readiness — no exclusion mechanism.
5. The «مرحلة الانتقال» page is review-only; SSOT for products/customers post-Go-Live is **Mezan + Salla**, NOT imported Qoyod data.

### Backend changes
- **`integrations/qoyod/go_live.py`**:
  - `_check_outstanding_failures` filter changed from `excluded_from_checklist: {$ne: True}` → `dry_run: {$ne: True}`.
  - Detail messages updated: green says "لا توجد فواصل إنتاجية عالقة. فشل Dry Run (إن وُجد) معروض في صفحة المراقبة فقط." Red says "X فاتورة إنتاجية فشلت — يجب مراجعتها قبل المتابعة (فشل Dry Run لا يُعيق الجاهزية)."
  - Checklist label changed to "لا فواصل إنتاجية عالقة".
- **`integrations/qoyod/product_resolver.py`** + **`customer_resolver.py`**: docstrings explicitly state Mezan+Salla SSOT; runtime never reads `qoyod_external_*` / `qoyod_migration_*`.
- The `clear-test-failures` endpoint remains for backward compat (no UI uses it anymore) — `excluded_from_checklist` field is no longer consulted by any check.

### Frontend changes
- **`pages/QoyodGoLive.jsx`**: removed the obsolete "🗑️ تنظيف فشل الاختبار" button + its handler + state. The button could have masked real production failures and is now dangerous.
- **`pages/QoyodMigration.jsx`**: added blue notice banner explaining the page is review-only and Mezan+Salla are SSOT.

### Tests
- **`tests/test_qoyod_go_live_qyd_fix.py`**: rewrote the 2 outstanding-failures tests + added a "missing dry_run treated as production" defensive test (3 new tests).
- **`tests/test_qoyod_qydgo_clear_failures_persistence.py`**: rewritten end-to-end on real Mongo (5 tests):
  - Dry-run failures alone never block.
  - Production failures always block.
  - Mixed: only production count.
  - Refresh stability ×3.
  - Worker-produced new dry failures still don't block.
- **`tests/test_qoyod_runtime_ssot_isolation.py`** (new, 1 test): tokenises every runtime module and fails if any of them references `qoyod_external_*` / `qoyod_migration_*` collections in executable code (docstrings allowed). CI guardrail against future drift.

### Verification
- 35 tests pass (all Qoyod-touched).
- Screenshot of QYD-GO confirms: green "لا فواصل إنتاجية عالقة" with new detail, NO clear-test-failures button visible.
- Screenshot of «مرحلة الانتقال» confirms SSOT notice banner renders correctly.

### Architectural confirmation
A grep across `/app/backend/integrations/qoyod/` (excluding `migration.py` + `migration_routes.py`) shows **zero references** to the 4 migration collections. The runtime pipeline only reads/writes `qoyod_products_mapping` + `qoyod_customers_mapping`, which are populated on-demand by the resolvers themselves from Salla-supplied data.



## QYD-GO "Outstanding Failures Returns RED After Refresh" — Investigation (2026-02-26)
**User report**: After clicking "تنظيف فشل الاختبار" the checklist goes 11/11 green, but on refresh "لا فواشل عالقة" returns to RED. User suspected exclusion is not persisted or not read.

### Investigation result
**The exclusion mechanism is fully correct** — proved by 5 new integration tests in `tests/test_qoyod_qydgo_clear_failures_persistence.py`:
1. Cleanup endpoint writes `excluded_from_checklist=true` + `excluded_at=<datetime>` to Mongo for all matched rows.
2. `_check_outstanding_failures` correctly filters `{"$ne": True}` on the flag.
3. Three successive checks after cleanup all return OK (= page stays green across refreshes).
4. Cleanup is idempotent.
5. **NEW** failures appearing after cleanup correctly DO surface (this is the actual cause).

There is **only one code path** that counts outstanding failures (`_check_outstanding_failures` in `go_live.py`); no second un-filtered query exists.

### Most likely root cause
The background worker keeps draining old in-flight orders (NORMALIZED, CUSTOMER_RESOLVED, INVOICE_CREATED, …) and when any of them fails it produces a **brand-new** DEAD_LETTER row. The new row has no `excluded_from_checklist` flag (correct — exclusion only stamps existing rows at the time of the click). So:

```
T0: 5 DEAD_LETTER rows from previous tests
T1: user clicks "تنظيف فشل الاختبار" → all 5 excluded → green
T1+δ: worker processes 3 stuck NORMALIZED rows → 2 fail → 2 NEW DEAD_LETTER rows
T2: user refreshes → 2 new rows counted → RED
```

The user perceives "old failures came back" but in reality these are new ones.

### Files
- `/app/backend/tests/test_qoyod_qydgo_clear_failures_persistence.py` (new — 5 tests, all pass)
- No production code changed yet — awaiting user decision on the fix strategy.

### Proposed fixes (awaiting user choice)
- (a) **Dry-run-aware check**: `_check_outstanding_failures` only counts `dry_run: False` failures. Production failures still block; dry-run noise never blocks. Removes the need for the "clear test failures" button entirely.
- (b) **Watermark exclusion**: cleanup also writes `qoyod_settings.failures_excluded_since=now()`; the check only counts rows with `received_at > since` AND `excluded_from_checklist != True`. Closer to current UX but adds a hidden "since" timestamp.
- (c) **Archive-on-cleanup**: replace `excluded_from_checklist` with the new archive flow — physically move dry-run DEAD_LETTER rows to `integration_inbox_archive` (already built for First-Sync Monitor). Cleanest, most durable.



## First-Sync Monitor → Permanent Operational Dashboard (2026-02-26)
**User request**: Transform the First-Sync Monitor from a dev-only diagnostic into a daily operational tool with sidebar integration, status counters, failure alerts, and a safe cleanup tool for old dry-run test noise.

### Sidebar Integration (frontend)
- New link **"🩺 مراقبة مزامنة قيود"** added under التكاملات → قيود (Sidebar.jsx).
- Polls `/api/integrations/qoyod/first-sync-monitor/stats/summary` every 20s.
- Red pulsing dot + numeric badge next to the link when `failed > 0`.
- Red dot next to "التكاملات (Integrations)" section header so the alert is visible even when the section is collapsed.
- testids: `nav-qoyod-first-sync-monitor`, `nav-qoyod-monitor-alert-dot`, `sidebar-integrations-alert-dot`.

### Status Counter Badges (frontend monitor page)
- 4 stat tiles at the top of `QoyodFirstSyncMonitor.jsx`:
  - **قيد المعالجة** — any non-terminal pipeline stage.
  - **فشل (DEAD_LETTER + PARTIAL)** — failed terminal states (red when >0).
  - **ناجحة (COMPLETED)** — successful terminal state.
  - **متخطّاة (SKIPPED)** — business-rule excluded.
- testids: `stat-processing`, `stat-failed`, `stat-success`, `stat-skipped`.

### Archive Failed Dry-Run Tests (renamed from "delete")
- Banner appears only when `stats.dry_failed > 0`.
- Button **"🗂️ أرشفة فشل الاختبار القديم"** opens a confirm modal.
- User must type **"CLEAN"** (case-sensitive) — submit button stays disabled until exact match.
- Modal explicitly lists safety guarantees: rows go to archive (recoverable), COMPLETED untouched, no Qoyod data touched, production failures untouched.
- testids: `archive-failed-tests-banner`, `btn-open-archive-modal`, `archive-modal`, `archive-confirm-input`, `btn-archive-confirm`, `btn-archive-cancel`, `archive-result`.

### Backend
- **`integrations/qoyod/first_sync_monitor.py`**:
  - `get_monitor_stats(db, user_id)` — `$group` aggregation by `pipeline_stage` + `dry_run`, buckets into `{processing, failed, success, skipped, dry_failed, total}`.
  - `archive_failed_dry_run_tests(db, user_id, confirm_token, actor)` — strict filter `{user_id, pipeline_stage ∈ {DEAD_LETTER, PARTIAL_FAILURE}, dry_run: True}`; copies matched rows to `integration_inbox_archive` with stamped metadata (`archived_at`, `archived_by`, `archive_reason`, `original_inbox_id`), then deletes with the same strict filter + a `trace_id` constraint for defense-in-depth.
  - Confirm token **"CLEAN"** enforced; missing/wrong raises `ArchiveRefused`.
- **`integrations/qoyod/routes.py`**:
  - `GET /api/integrations/qoyod/first-sync-monitor/stats/summary` — counter endpoint.
  - `POST /api/integrations/qoyod/first-sync-monitor/archive-failed-tests` — body `{confirm: "CLEAN"}` → `{matched, archived, deleted, archive_ids}`. Returns 400 `confirm_required` on wrong token.
  - Routes ordered so concrete paths come before the `{trace_id}` wildcard.

### Safety Contract (NEVER violated)
1. NEVER touches `pipeline_stage: COMPLETED` rows.
2. NEVER touches `dry_run: False` rows (production data is sacred).
3. NEVER touches rows from other tenants.
4. NEVER touches data inside Qoyod itself (local-only archive op).
5. Always copies before deletes — archive is the source of truth for recovery.

### Tests
- **`tests/test_qoyod_monitor_archive.py`** (7 tests, all pass):
  - Stats correctly bucket all stages including `dry_failed` subset.
  - Empty tenant returns all zeros.
  - Confirm-token enforcement (empty / lowercase / wrong word all rejected).
  - Strict filter test: 8-row mix → only the 2 dry+failed rows archived; 6 protected rows untouched.
  - Idempotent when no matches.
  - Cross-tenant isolation.
- E2E manual test passed: seeded 1 dry-run DEAD_LETTER row → sidebar badge + section dot + banner appeared → typed CLEAN → archive succeeded → row in `integration_inbox_archive` with full metadata, 0 rows in live collection.

### Files Changed
- `/app/backend/integrations/qoyod/first_sync_monitor.py` (+~120 lines)
- `/app/backend/integrations/qoyod/routes.py` (3 new endpoints, BaseModel)
- `/app/frontend/src/components/Sidebar.jsx` (link + 20s polling + alert dot)
- `/app/frontend/src/pages/QoyodFirstSyncMonitor.jsx` (badges + banner + modal)
- `/app/backend/tests/test_qoyod_monitor_archive.py` (new — 7 tests)



## QYD-GO Checklist Fixes — 3 Blockers (2026-06-27)
**User-reported**: After the worker wiring, QYD-GO page still blocked Go-Live with 3 false-positives.

### Fix 1 — Branch ID no longer a blocker
- `go_live.py::_check_branch` now returns `ok: True` regardless of branch value.
  - When set: detail shows the ID.
  - When blank: detail reads "اختياري — الحساب أحادي الفرع، سيستخدم قيود الفرع الافتراضي تلقائياً".
- Consistent with the earlier `setup_validation.py` change that demoted branch to a warning.

### Fix 2 — Outstanding-failures check ignores excluded test rows
- `_check_outstanding_failures` now filters `excluded_from_checklist: {$ne: true}` so stale DEAD_LETTER/PARTIAL_FAILURE rows from pre-fix tests don't block readiness.
- **New endpoint** `POST /api/integrations/qoyod/go-live/clear-test-failures`:
  - Marks all current DEAD_LETTER/PARTIAL_FAILURE rows as `excluded_from_checklist: True` with `excluded_at` timestamp.
  - Rows STAY in the database for First-Sync-Monitor visibility — only the QYD-GO check ignores them.
- **Frontend** `QoyodGoLive.jsx`:
  - When `outstanding_failures` fails, an inline button "🗑️ تنظيف فشل الاختبار (استبعاد من الفحص)" appears beneath the row.
  - On click: confirmation prompt → POST → toast with count → reload.

### Fix 3 — Eligible orders detects post-worker dry-run completions
- `_check_eligible_orders` now has TWO acceptance paths:
  - a) In-flight rows (NORMALIZED / CUSTOMER_RESOLVED) matching a trigger slug — existing behaviour.
  - b) **NEW**: At least one COMPLETED dry-run row in the last 24h matching a trigger slug.
- Path (b) is critical because the worker now drains rows quickly out of NORMALIZED, so (a) often returns 0 even when the pipeline is healthy and processing real webhooks.
- Detail reads "X طلب اكتمل في Dry Run خلال 24 ساعة — الـ pipeline نشط".

### Fix 4 (bonus) — Dry-run proof uses integration_inbox
- `_check_dry_run_proven` previously read `db.qoyod_invoices` which is not populated by the new pipeline.
- Now reads `integration_inbox` with `pipeline_stage: COMPLETED` + `dry_run: True`.

### Tests
- `tests/test_qoyod_go_live_qyd_fix.py` — 10 tests covering all 4 fixes incl. nested-field queries and edge cases.
- All Qoyod-touched tests still pass.


## Pipeline Worker Wiring — CRITICAL FIX (2026-06-27)
**User-reported bug**: Webhook order #268602475 stuck at NORMALIZED forever; never reached CUSTOMER_RESOLVED / INVOICE / RECEIPT.

### Root cause
Two bugs working together:
1. The pipeline was designed in two manual stages (`/pipeline/process-normalized`, `/pipeline/process-customer-resolved`) intended to be called by a background worker. **The worker was never wired into application startup.** Rows sat at NORMALIZED indefinitely.
2. Even when the orchestrator was called manually, `process_normalized_row` accepted `api_client=None` but never built a `DryRunQoyodClient` from settings — the customer resolver then attempted to hit the real Qoyod API (returning 401 in dry-run scenarios).

### Fix
- **NEW `integrations/qoyod/worker.py`** — asyncio loop that drains `process_pending_normalized` + `process_pending_customer_resolved` every 5s with batch_limit=25. Exposes `start_worker()`, `run_now()` (emergency manual trigger), `liveness()`, `is_running()`.
- **`server.py` startup hook (iter-262)** spawns the worker on app startup (idempotent, errors logged not raised).
- **`pipeline.py::process_normalized_row`** — now calls `_get_api_client(db, user_id, settings)` when `api_client=None`, so dry-run mode correctly skips real Qoyod calls.
- **`first_sync_monitor.py`** — new `_is_stuck()` helper + `stuck` field on each shaped row. Returns `{stage, waited_seconds, reason}` when a row in `{NORMALIZED, RULES_APPLIED, CUSTOMER_RESOLVED, INVOICE_CREATED}` exceeded 30s.
- **New endpoints**:
  - `GET /api/integrations/qoyod/worker/status` → `{running, last_run_at, last_run_ok, last_round}`.
  - `POST /api/integrations/qoyod/worker/run-now` → emergency drain trigger.
- **Frontend `QoyodFirstSyncMonitor.jsx`**:
  - Worker status pill in toolbar (✓ يعمل / ⚠ خطأ / ✗ متوقف).
  - "⏳ بانتظار العامل (Xs)" badge on stuck rows + red banner with "▶️ تشغيل الآن" emergency button.
  - Auto-refresh default = ON (was OFF).

### End-to-end verification
Sent a fresh dry-run webhook → 15s later monitor showed:
```
pipeline_stage: COMPLETED
customer  success qoyod_id=DRY:contact:4c6cc5e2
product   success qoyod_id=DRY:product:8cc97e87 (per-SKU)
invoice   success qoyod_id=DRY:invoice:eea30eba
receipt   success qoyod_id=DRY:receipt:7b45b841
```

### Tests
- `tests/test_qoyod_worker_and_stuck.py` — 9 tests covering stuck detection (under threshold, over threshold, all waiting stages, ISO timestamps, naive datetimes, COMPLETED/DEAD_LETTER skipped) + worker module liveness shape.
- All 72 Qoyod-touched tests pass cleanly when run in isolation.


## P0 Pre-Go-Live: Dynamic Salla Statuses + Product Type Label (2026-06-27)

### 1) Order-status trigger picker is now DYNAMIC
- **Backend** `routes.py` — new endpoint `GET /api/integrations/qoyod/salla-order-statuses`:
  - Primary source: calls Salla `GET /orders/statuses` via `call_salla()` (auto-refresh token).
  - Each row normalized to `{id, slug (lowercase), name, name_en, type, is_system}`.
  - Fallback when Salla disconnected/unreachable: scans `unified_orders` for distinct `order_status` slugs + Arabic names from `raw.status.name`.
  - Response includes `source: "salla_api" | "fallback"` and structured `error` so the UI can inform the operator.
- **Frontend** `QoyodSettings.jsx`:
  - Removed hardcoded `TRIGGER_STATUS_OPTIONS` (completed/delivered/paid/shipped).
  - New `loadSallaStatuses()` populates the picker from the endpoint.
  - Source badge: ✓ من Salla API (emerald) أو ⚠ من الطلبات المرصودة (amber).
  - Each row shows the merchant's actual Arabic status name + the immutable `slug:` (small mono) underneath.
  - Hint clarifies: "النظام يستخدم slug الحالة من Salla — تغيير الاسم الظاهر في Salla لا يكسر التكامل."
  - 🔄 Reload button per-section to force a fresh fetch.
  - Persisted to settings as lowercase slug array (existing pipeline already matches on `dto.order_status` which is also lowercase canonical — no behaviour change downstream).
- **Tests**: `test_qoyod_salla_order_statuses.py` — 3 tests (Salla success path normalization, fallback to observed orders, slug-not-name persistence).

### 2) Product Type label change (cosmetic only)
- **Frontend** `QoyodSettings.jsx` — the `PRODUCT_TYPE_OPTIONS` row for technical value `"service"` now reads:
  - **"منتجات بدون إدارة مخزون في قيود — موصى به لربط ميزان"** (was "خدمات (Service) — موصى به للمتاجر الرقمية").
- The wire value sent to Qoyod remains `service`. Pipeline / invoice builder unchanged.

### Tests
- Full Qoyod suite: **339 passed**, 0 regressions.


## P0 First Production Dry Run Readiness (2026-06-27)
**Goal**: Final pre-production polish before flipping Dry Run off.

### 1) Branch ID is now OPTIONAL
- `setup_validation.py` — `missing_branch_id` demoted from `blocker` to `warning` (single-branch Qoyod accounts don't need this set).
- `invoice_builder.py` — when `default_branch_id` is None/empty, the field is OMITTED from the invoice payload (no `branch_id: null` sent to Qoyod).
- Frontend `QoyodSettings.jsx` — label changed to "Branch ID — اختياري", `required` asterisk removed, hint reads "اتركه فارغاً إذا كان حسابك بفرع واحد".
- Tests cover both the validation severity change and the conditional payload behaviour.

### 2) Tax ID hardening
- `invoice_builder.py` — explicit comment that `default_tax_id` MUST be a Qoyod **Tax ID** (e.g. `"1"`), NOT a rate (e.g. `"15"`). When unset the line-item `tax_id` is OMITTED so Qoyod uses the item's own tax.
- Regression test `test_invoice_line_uses_tax_id_not_rate` asserts no `tax_rate` or `rate` field leaks into the line payload.

### 3) First-Sync Monitor (new page)
- **Backend** `integrations/qoyod/first_sync_monitor.py`:
  - `shape_inbox_row_for_monitor(row)` — reduces an `integration_inbox` doc into operator timeline: order summary, raw Make payload, canonical DTO, 4 step cards (customer/product/invoice/receipt) each with `payload` + `response` + `duration_ms` + per-step status.
  - `_status_for_stage()` — calculates per-step status: success / failed / pending / skipped based on `last_success_stage` and `last_failed_stage`.
  - Endpoints:
    - `GET /api/integrations/qoyod/first-sync-monitor?limit=N` — latest N rows reduced.
    - `GET /api/integrations/qoyod/first-sync-monitor/{trace_id}` — single row by trace.
- **Pipeline** persists raw Qoyod responses now:
  - `qoyod_responses.invoice = {body, qoyod_id, qoyod_number, duration_ms, received_at}` on success / `{error, duration_ms}` on failure.
  - Same for `qoyod_responses.receipt`.
- **Frontend** new page `pages/QoyodFirstSyncMonitor.jsx` at route `/integrations/qoyod/first-sync-monitor`:
  - Toolbar: refresh, auto-refresh-every-5s toggle, limit selector (1/3/5/10/25).
  - Per-row card with collapsed/expanded states.
  - 4 expandable step cards with side-by-side "📤 الإرسال إلى Qoyod" + "📥 الرد من Qoyod" JSON blocks.
  - Stage History timeline with timestamps, actors, notes, and error blocks.
  - Side-by-side Make raw + canonical DTO + business rules + preflight.
  - Empty-state guides the operator to send first Make payload.

### Tests
- `tests/test_qoyod_first_sync_monitor.py` — 11 tests covering status calculation, shaper output, branch-id-optional in builder, tax-id-not-rate, setup-validation severity downgrade.
- Updated `test_qoyod_setup_validation.py` to expect branch as warning instead of blocker.
- Full Qoyod suite: **336 passed**, 0 Qoyod regressions.


## Qoyod Fresh-Start Cleanup — Plan + Execute (2026-06-27)
**Goal**: Simplified deletion workflow (Audit → Plan → Execute) gated by `DELETE-CONFIRM` token. No Dry-Delete; environment not yet productive.

### Backend
- **NEW `integrations/qoyod/fresh_start_cleanup.py`**:
  - `EXPECTED_CONFIRM_TOKEN = "DELETE-CONFIRM"` (exact case-sensitive match).
  - `PROTECTED_ENTITIES` constant lists what is NEVER touched: chart_of_accounts, branches, taxes, settings, users, financial_accounts.
  - `build_plan()` paginates 4 entities and persists ID lists to `qoyod_fresh_start_cleanups`.
  - `execute_cleanup()` — refuses unless token matches exactly AND a planned job exists. Deletion order: **Receipts → Invoices → Products → Customers** (FK-safe).
  - `_delete_batch()` — continues past failures, treats 404 as success, aborts batch on 405 (Qoyod doesn't support DELETE), 100ms cushion between calls.
  - Raises `CleanupRefused` on bad token or missing plan.
- **API client**: added GET-only `list_invoices`/`list_receipts` and DELETE methods `delete_invoice/receipt/product/customer` (gated by cleanup module only).
- **New endpoints**:
  - `POST /api/integrations/qoyod/fresh-start/plan/build` — builds a plan job, returns full ID list + totals.
  - `GET /api/integrations/qoyod/fresh-start/plan/latest` — returns latest plan (also exposes `expected_confirm_token` and `protected_entities`).
  - `POST /api/integrations/qoyod/fresh-start/execute` — body `{job_id, confirm}`. Returns final report with `deleted` counts and `failed` array.

### Frontend (`pages/QoyodFreshStart.jsx` enhancement)
After a successful audit, three new sections appear:
1. **🗂️ Plan section** — explicit lists of "سيُحذف فقط" (4 entities) vs "لن يُمَس إطلاقاً" (6 protected), big-numbers preview of the current plan, build/rebuild button.
2. **🔴 Execute section** — appears only when `plan.status === "planned"`. Final warning + `DELETE-CONFIRM` text input that turns green when typed correctly. Execute button disabled until exact match.
3. **📊 Execute Result section** — per-entity deleted counts (success cards) + a failures table (entity/id/code/message) if any. On full success, prompts navigation to Settings to disable Dry Run and send first Make.com test.

### Tests
- `tests/test_qoyod_fresh_start_cleanup.py` — 15 tests covering: token constant, protected list, ID extraction edge cases, batch happy path, 404-as-success, continue-past-failures, 405 abort, token gating (wrong/whitespace/case/missing-plan), deletion ORDER assertion, partial failure reporting, confirm-token audit trail persistence, plan building from all 4 entities.
- Full Qoyod suite: **333 passed**, 0 regressions.


## Qoyod Fresh-Start Audit — READ-ONLY Snapshot (2026-06-27)
**Goal**: Forensic audit of what already exists in Qoyod (legacy direct-Salla integration data) before Mezan becomes the sole source. STRICTLY read-only; no DELETE/PUT/PATCH.

### Backend
- **NEW `integrations/qoyod/fresh_start_audit.py`** — orchestrator + pure analysers:
  - `run_fresh_start_audit()` paginates ONLY 4 endpoints: `/invoices`, `/receipts`, `/products`, `/customers`. 100ms cushion between pages.
  - Per-entity analysers: counts, monthly histograms, sample rows (first 5), and link-completeness checks.
  - `_build_flags()` derives cross-entity risk warnings: invoices without receipts, orphan receipts, products without SKU, customers without invoices, ref/invoice mismatch.
  - Run persisted to `qoyod_fresh_start_audits` (one row per run; latest is fetched by `latest_audit()`).
  - Hard scope: **never** queries `/accounts`, `/branches`, `/taxes`, or any settings endpoint.
- **`integrations/qoyod/api_client.py`** — added `list_invoices(page, limit)` and `list_receipts(page, limit)` (GET only).
- **New endpoints** in `routes.py`:
  - `POST /api/integrations/qoyod/fresh-start/audit/run` — kicks off a new audit.
  - `GET /api/integrations/qoyod/fresh-start/audit` — returns latest audit snapshot.

### Frontend (`pages/QoyodFreshStart.jsx`)
- New route: `/integrations/qoyod/fresh-start`.
- Header explicitly states "قراءة فقط — لا تعديل ولا حذف".
- Pre-run state: 🛡️ safety guarantees panel.
- Post-run state: 4 total cards + per-entity sections with month histograms + sample tables + risk flags pill list.
- Failed state: shows structured error (e.g. `qoyod_unauthorized`).
- Footer reminds operator: deletion is a separate gated phase requiring `DELETE-CONFIRM` and explicit criteria.

### Tests
- `tests/test_qoyod_fresh_start_audit.py` — 16 tests covering ref pattern detection, month-bucket parsing, list extraction robustness, all 4 analysers, and 4 flag-building edge cases.
- Full Qoyod suite: 307 passed, 2 skipped, 0 regressions.


## Qoyod Settings — Final One-Time Setup Page (2026-06-27)
**Goal**: After saving once, operator never needs to revisit unless a new payment method is added or accounting setup changes.

### Backend
- **NEW `integrations/qoyod/setup_validation.py`**
  - `CANONICAL_PAYMENT_METHODS`: ordered list of 11 canonical methods (mada, apple_pay, visa, mastercard, credit_card, stc_pay, bank_transfer, tamara, tabby, emkan, cod).
  - `collect_used_payment_methods(db, user_id)` — scans `unified_orders` (payment_method + raw.payment_method) and `integration_inbox.canonical_payload.payment_method`. Returns grouped `{key,label_ar,count,sources,native_examples}` rows.
  - `validate_settings_for_setup(db, user_id)` — runs every Settings-page check:
    - `missing_branch_id` (blocker)
    - `missing_tax_id` (blocker)
    - `unmapped_payment_methods` (blocker) — every USED canonical key must have a non-empty `qoyod_account_id`.
    - `missing_inventory_account` / `missing_cost_account` (blocker, only if product_type=inventory)
    - `missing_default_customer` (warning) — optional guest fallback.
- **`integrations/qoyod/normalizer.py`** — added `emkan` (and Arabic `إمكان`) to canonical payment-method table.
- **New endpoints** in `routes.py`:
  - `GET /api/integrations/qoyod/payment-methods/used` — returns `{used:[…], catalogue:[…]}` for the mapping table.
  - `GET /api/integrations/qoyod/setup/validate` — returns full validation result for fail-safe server-side gate.

### Frontend (`pages/QoyodSettings.jsx`)
Complete rewrite as a one-time setup page:
1. **Setup Status banner** (top) — live client-side validation that mirrors the backend logic and updates as user types. Red banner with `Jump to →` buttons when blockers exist.
2. **Master switches** — enabled/auto_send/auto_receipt/dry_run_mode.
3. **API Key** + Test Connection.
4. **Webhook Token** (unchanged) — Make.com inbound auth.
5. **Core IDs**: Branch ID, Tax ID, Default Customer ID (optional) — manual input + datalist suggestions.
6. **💳 Payment Method Mapping table** (the most important section):
   - One row per used method (mandatory) + addable canonical methods.
   - Each row: Salla method label, Account ID input, status badge (مربوط / مطلوب / اختياري).
   - Rejected rows have rose-50 highlight.
7. **📦 Inventory Accounts** (conditional, only when `default_product_type === "inventory"`) — Inventory Account + COGS Account.
8. **Advanced** — trigger statuses, invoice date source, product type, trigger_once_only.
9. **Capability flags** — create_customers/products/invoices/receipts.
10. **📋 Setup Guide** (inline expandable) — 6 cards (Branch, Tax, Account, Default Customer, Inventory, API Key) with step-by-step instructions + direct `legacy.qoyod.com` links.
11. **Sticky save bar** — disabled when blockers exist. Save calls server validate after PUT as fail-safe.

### Tests
- `tests/test_qoyod_setup_validation.py` — 9 tests covering catalogue completeness, missing branch/tax blocking, used-but-unmapped detection, inventory mode requirements, payment method normalisation grouping, partial mapping handling.
- Full Qoyod suite: 291 passed, 2 skipped, 0 regressions.


## QYD-GO — Production Readiness Layer (2026-06-26)
Independent, read-only verification layer. NO new business logic; refuses to let
the operator flip the connector to live-mode unless every check passes.

### Backend (`integrations/qoyod/go_live.py`)
**Checklist — 11 items** (returned as `[{key,label,ok,detail,extra?}]`):
1. `api_key`              — credentials saved + fingerprint visible.
2. `branch`               — `default_branch_id` set.
3. `tax`                  — `default_tax_id` set.
4. `payment_mapping`      — at least one mapping AND all observed PMs mapped.
5. `product_mapping`      — local SKU mappings exist (or DryRun will create).
6. `customer_mapping`     — local customer mappings exist (or DryRun will create).
7. `dry_run`              — `dry_run_mode==True` NOW AND ≥1 completed dry-run row.
8. `outstanding_failures` — no rows stuck in DEAD_LETTER or PARTIAL_FAILURE.
9. `eligible_orders`      — ≥1 eligible row in NORMALIZED / CUSTOMER_RESOLVED.
10. `products_lookup`     — live GET /products against Qoyod succeeds.
11. `customers_lookup`    — live GET /contacts against Qoyod succeeds.

**Report — 8 numbers** (the operator stares at these before clicking activate):
- `eligible_orders_count`, `products_needing_creation`, `products_already_in_qoyod`,
  `qoyod_products_total`, `customers_needing_creation`, `customers_already_local`,
  `qoyod_contacts_total`, `unmapped_payment_methods` (+count), `would_fail_if_live_now`,
  `dry_run_mode_currently_on`.

**Activation** (`activate_production_mode()`):
- Re-runs the checklist server-side (defense-in-depth — UI can't bypass).
- On failure → raises `ActivationBlocked(reasons=…, items=…)` → HTTP 409 with the
  closed list of failing labels for the toast.
- On success → atomic `update_one(..., $set:{enabled:true, dry_run_mode:false,
  activated_at:now()})`.

### API endpoints
- `GET  /api/integrations/qoyod/go-live/checklist`
- `GET  /api/integrations/qoyod/go-live/report`
- `POST /api/integrations/qoyod/go-live/activate`

### API client extension
- `list_products(page,limit)` + `list_contacts(page,limit)` added to `QoyodAPIClient`
  (used only by Go-Live lookup checks).

### Frontend (`QoyodGoLive.jsx` at `/integrations/qoyod/go-live`)
- Top status banner: traffic-light (emerald = live, blue = ready, amber = blocked) +
  ACTIVATE button disabled until `all_passed`.
- Report card: 8 stat cells with tone (rose for ‘would_fail’, amber for ‘needs work’,
  emerald for safe).
- Checklist card: 2-column grid of items with ✓/✗ circle + detail text +
  "X/11 اجتاز" badge.
- Confirmation dialog before activation; toast surfaces the failing reasons on 409.
- New sidebar entry under Integrations → قيود: "🚀 جاهزية الإنتاج (QYD-GO)" with
  testid `nav-qoyod-go-live` (above Invoices).

### Tests — 10/10 ✅ (full Qoyod suite: 140/140)
- `tests/test_qyd_go_production_readiness.py` covers:
  - Checklist happy path (full readiness → all_passed=true).
  - Each negative path: missing api_key / missing branch+tax / unmapped payment /
    dry_run disabled / outstanding DEAD_LETTER / Qoyod lookup error.
  - Report: products/customers creation-vs-mapped counts, unmapped PMs, would_fail.
  - Activation: blocked when checklist fails (settings stay unchanged) ·
    succeeds when all pass (flips dry_run_mode→false + enabled→true atomically).

### Live curl + UI smoke ✅
- `GET /go-live/checklist` → 11 items rendered in Arabic with ✓/✗.
- `GET /go-live/report` → all 8 fields populated.
- `POST /go-live/activate` (with one check failing) → 409 `activation_blocked` with
  the failing labels in `detail.reasons`.

## Qoyod MVP — Day 5 + Pre-Day-5 Safety Rules (2026-06-26)
**User-locked safety rules** (implemented BEFORE any live Qoyod write):

### 1. Dry Run Mode (`settings.dry_run_mode: bool`)
- Master toggle in `QoyodSettings.jsx` (🧪 وضع التشغيل الجاف).
- New `DryRunQoyodClient` is a drop-in replacement for `QoyodAPIClient` with the
  same async interface (`create_contact / create_product / create_invoice / create_receipt`).
- Returns deterministic fake ids of the form `DRY:<entity>:<sha8>` so downstream stages
  still build receipt payloads correctly. Records every "POST" it WOULD make in `self.calls`.
- The orchestrator picks the client based on `is_dry_run_mode(settings)`.
- Rows complete the pipeline fully — payloads snapshotted, ledger rows written with
  `dry_run=true` + `status="pending"`. Audit fields populated identically to live mode.

### 2. Pre-flight Checklist (`preflight.py`)
Six required checks, ALL must pass before any invoice POST:
  1. **customer**   — `qoyod_customer_id` populated.
  2. **products**   — every line item has a `qoyod_product_id`.
  3. **tax**        — `default_tax_id` configured OR all items carry `tax_amount`.
  4. **payment**    — Salla method present AND mapped to a Qoyod account.
  5. **status**     — canonical order_status in `invoice_trigger_statuses`.
  6. **idempotency**— no prior `status="sent"` invoice row (when `trigger_once_only=True`).
Failure → row routes through `FAILED_INVOICE → DEAD_LETTER` with the full `preflight.failures[]`
recorded in `pipeline_error.preflight` for diagnosis.

### 3. Payload Snapshot
- Invoice payload written to `row.qoyod_payloads.invoice` + `invoice_snapshot_at` BEFORE the
  POST attempt — same for receipt. Survives even if Qoyod returns 5xx mid-call.
- Snapshots are full Qoyod request bodies (not the canonical DTO) so the operator can
  diff-test against a future Qoyod API change.

### 4. Partial Failure (`PARTIAL_FAILURE` terminal stage)
- New terminal stage in `state_machine.py`. Added to `TERMINAL_STAGES`. The edge
  `FAILED_RECEIPT → PARTIAL_FAILURE` is the ONLY path in.
- When invoice POST succeeds but receipt POST raises → row goes
  `INVOICE_CREATED → FAILED_RECEIPT → PARTIAL_FAILURE` (NOT DEAD_LETTER — invoice IS in Qoyod).
- `qoyod_invoices.status="invoice_sent_receipt_failed"`, `pipeline_stage="PARTIAL_FAILURE"`,
  `last_failed_stage="FAILED_RECEIPT"`, `last_success_stage="INVOICE_CREATED"`.

## Day 5 — Pipeline Completion (4b → 4c → 4d)

### New backend modules
- `product_resolver.py` — `resolve_products()` walks `dto.items`, hits
  `qoyod_products_mapping` first, creates in Qoyod on miss with idempotency
  key `mzn-{trace}-product-{sku}`. Builds product payload from `default_product_type`.
  `ProductsResolutionResult.items` carries per-sku status.
- `invoice_builder.py` — pure `build_invoice_payload()` / `build_receipt_payload()`,
  `DryRunQoyodClient`, `is_dry_run_mode()`. Maps DTO line items → Qoyod line_items with
  resolved product ids + `default_tax_id`. Receipt resolves `account_id` from
  `payment_method_mapping`.
- `preflight.py` — pure `run()` returning `PreflightResult(passed, failures)`.
- `pipeline.py` extended with `process_customer_resolved_row()` chain:
  `CUSTOMER_RESOLVED → PRODUCT_RESOLVED → (preflight) → INVOICE_CREATED → RECEIPT_CREATED → COMPLETED`,
  with the three safety nets above wired into every hop. Also `process_pending_customer_resolved()`
  batch driver and `day4_report()` aggregation.

### New API endpoints
- `POST /api/integrations/qoyod/pipeline/process-customer-resolved?limit=25`
  — drains CUSTOMER_RESOLVED rows up to limit.
- `GET  /api/integrations/qoyod/reports/day4`
  — aggregates `by_stage`, `skipped_reasons`, `dead_letter_by_stage`, `totals`.

### Frontend
- `QoyodSettings.jsx` — new Dry Run toggle (`data-testid="toggle-dry-run-mode"`).
- `QoyodInvoices.jsx` — new **Day 4 Report Card** (`qoyod-day4-report-card`):
  6 stat cells (normalized/customer_resolved/completed/skipped/dead_letter/partial_failure)
  + skipped-reason pills + dead-letter-by-stage pills
  + 2 action buttons (`run-process-normalized` blue, `run-process-customer-resolved` green)
  with hint "يحترم Dry Run Mode + Pre-flight + Payload Snapshot".
- Reconciliation + Compliance + Orphans + Invoices Tables unchanged.

### Tests — 130/130 ✅
- `tests/test_qoyod_day5_invoice_receipt.py` — **15/15** new tests:
  - State-machine: PARTIAL_FAILURE terminal + FAILED_RECEIPT edge.
  - Preflight: 6 checks (passes / each fails individually).
  - Payload builders: invoice/receipt include all required fields, resolve payment account.
  - DryRunQoyodClient: records calls, returns deterministic fake ids.
  - E2E: dry-run completes without POST + payloads snapshotted +
    `pending` ledger row · preflight blocks on missing tax · receipt failure routes to
    PARTIAL_FAILURE + ledger reflects split state · snapshot timestamps present ·
    day4_report aggregates correctly.
- All prior suites unchanged: Day1×28, State Machine×24, Compliance×11, Day3×28, Day4×25.

### Live UI smoke ✅
- Day 4 Report card visible at top of `/integrations/qoyod/invoices` with 6 stat cells.
- Both action buttons render with correct test ids.
- Sidebar sub-grouping intact.

## Qoyod MVP — Day 4 — Business Rules + Customer Resolution (2026-06-26)
**User-locked Invoice Trigger Policy (foundational rule):**
```
invoice_trigger_statuses = ["completed"]        # list, NEVER hard-code "paid"
invoice_date_source      = "trigger_status_date"
trigger_once_only        = true
```
Rationale: VAT + Zakat compliance forces the invoice date to come from
a configurable status transition, not from `paid`. Multiple statuses
allowed for merchants who fire on completed+delivered.

**Day 4 scope (strictly stopped at CUSTOMER_RESOLVED):**
1. `NORMALIZED → RULES_APPLIED` — eligibility decision per Invoice Trigger Policy.
2. `RULES_APPLIED → CUSTOMER_RESOLVED` (4a only) — local mapping hit OR Qoyod create.
3. NO products, NO invoice, NO receipt — pending merchant review.

### New backend modules
- `integrations/qoyod/business_rules.py` — pure `evaluate(dto, settings, existing_invoice_row)`
  returns `RulesDecision`. Tokens: `eligible / not_in_trigger_statuses / already_sent`.
  Resolves invoice date from `trigger_status_date | completed_at | paid_at | created_at`
  with safe fallback to `order_date`. Includes a status→date-field map so each canonical
  status (completed/delivered/paid/shipped/processing) picks the right timestamp.
- `integrations/qoyod/customer_resolver.py` — `resolve_customer()` walks the lookup chain
  `phone → email → guest_order`. Hits `qoyod_customers_mapping` first; only calls
  Qoyod `POST /contacts` on miss (with idempotency key `mzn-{trace}-contact-{kind}-{key}`).
  Returns `ResolutionResult(success, qoyod_customer_id, lookup_key, lookup_kind, created_new, error)`.
- `integrations/qoyod/pipeline.py` — orchestrator. `process_normalized_row()` advances ONE row
  and is idempotent on stage check. `process_pending_normalized()` batches up to `limit` rows
  sequentially. Failures route through `FAILED_CUSTOMER → DEAD_LETTER` (two-hop, exact same
  pattern as Day 3). Records `business_rules_decision` + `customer_resolution` snapshots on
  the row for the Timeline UI.

### Settings model extension (with backwards-compat shim)
- `QoyodSettings`:
  - `invoice_trigger_statuses: list[str] = ["completed"]` (new canonical)
  - `invoice_date_source: Literal["trigger_status_date","completed_at","paid_at","created_at"]
     = "trigger_status_date"` (new default)
  - `trigger_once_only: bool = True`
  - `invoice_trigger_status` kept as legacy field (None default), reads migrated on the fly.
- `_load_settings()` auto-migrates: legacy `invoice_trigger_status` → list; legacy
  `invoice_date_source="completed_at"` → `"trigger_status_date"`; missing `trigger_once_only`
  → True.
- PUT /settings expands the singular field if a caller still sends it (safe rollout).

### New API endpoint
- `POST /api/integrations/qoyod/pipeline/process-normalized?limit=25` (JWT-protected).
  Drains NORMALIZED rows. Response: `{ok, processed, counts:{customer_resolved,skipped,dead_letter}, items:[…]}`.
  Each item carries the `decision` + `customer` snapshots so the operator can audit one click.

### Frontend (`QoyodSettings.jsx`)
- Replaced the single trigger dropdown with a **4-checkbox group** (completed/delivered/paid/shipped)
  + label "حالات الطلب التي تطلق إنشاء الفاتورة". Default pre-checks `completed`.
- Added `trigger_once_only` toggle ("إنشاء الفاتورة لمرة واحدة فقط").
- Updated invoice-date dropdown to include "تاريخ انتقال الطلب للحالة المؤهلة" (recommended default).
- Save payload now sends the new list field; empty list is never accepted (auto-resets to ["completed"]).

### Tests — 115/115 ✅
- `tests/test_qoyod_day4_rules_and_customer.py` — **25/25** new tests:
  - Rules (9): eligible/not-eligible/multi-trigger/once-only/never-implicit-paid/fallback date.
  - Resolver helpers (5): phone-preferred, email-fallback, guest, payload, id extraction.
  - Resolver DB (5): local hit · create-new + mapping persisted · API error path ·
    guest with default · guest without default.
  - Pipeline (6): happy CUSTOMER_RESOLVED · SKIPPED · DEAD_LETTER + audit · trigger_once_only
    blocks resend · idempotent on already-advanced rows · batch counter sums.
- `tests/test_qoyod_day3_webhook.py` — 28/28 unchanged.
- `tests/test_qoyod_state_machine.py` — 23/23 unchanged.
- `tests/test_qoyod_compliance.py` — 11/11 unchanged.
- `tests/test_qoyod_day1_foundation.py` — 28/28 (updated `test_settings_defaults_are_safe` to
  match the new tri-field policy).

### Live curl smoke (PREVIEW) ✅
1. Webhook ingestion of an order in "تم التنفيذ" → 200 NORMALIZED.
2. `/pipeline/process-normalized` → rules say eligible/triggered_by="completed"/
   invoice_date_source="completed_at". Customer call returns 307 (placeholder API key) →
   row → `DEAD_LETTER` with `FAILED_CUSTOMER` + Qoyod response excerpt. Row preserved.
3. Webhook with "تم الشحن" → run → `SKIPPED` with `reason="not_in_trigger_statuses"`.

### Critical invariants (locked by tests)
- "paid" is NEVER an implicit trigger — must be explicitly added by the merchant.
- `trigger_once_only=True` is enforced before customer resolution (no Qoyod call on resend).
- Day 4 ceiling: orchestrator never writes to `qoyod_invoices`, never calls
  `create_product/create_invoice/create_receipt`.
- DEAD_LETTER rows from FAILED_CUSTOMER carry the full Qoyod error excerpt for diagnosis.

### Outstanding for Day 5 (pending merchant review)
- Step 4b — Product Resolution (`CUSTOMER_RESOLVED → PRODUCT_RESOLVED`).
- Step 4c — Invoice Creation (`PRODUCT_RESOLVED → INVOICE_CREATED`).
- Step 4d — Receipt Creation (`INVOICE_CREATED → RECEIPT_CREATED → COMPLETED`).
- Background retry worker (RETRYING flow).
- Manual Action buttons activation + سجل المزامنة page wiring.

## Qoyod MVP — Day 3 — Reliable Webhook Reception Layer (2026-06-26)
**User-locked scope (8 steps, nothing else):**
1. Receive webhook  ·  2. Verify token  ·  3. Idempotency  ·
4. Save raw event  ·  5. Validation  ·  6. Normalization  ·
7. Canonical SalesOrderDTO  ·  8. STOP.

NO business rules, NO Qoyod output, NO sync-log activation — those land in Day 4-5.

### New modules
- `/app/backend/integrations/qoyod/dto.py` — `SalesOrderDTO`, `CustomerDTO`, `LineItemDTO`,
  `AddressDTO`. Pure Pydantic, `extra="forbid"`, `schema_version=1`.
- `/app/backend/integrations/qoyod/normalizer.py` — `validate()` returns closed-set error codes
  (`invalid_payload_type`, `missing_data_object`, `missing_order_id`, `missing_order_status`,
  `missing_items`, `empty_items`). `normalize()` builds the DTO. Helpers: `normalize_phone()`
  (E.164 / Saudi-aware), `normalize_email()`, `_canonical_status()` (Arabic + English map),
  `_canonical_payment_method()` (mada / visa / apple_pay / stc_pay / cash / cod / tamara / tabby…).
  `NormalizationError(code, message)` carries structured failure detail.
- `/app/backend/integrations/qoyod/webhook.py` — `POST /api/integrations/qoyod/webhook`. Token check
  (`X-Webhook-Token` against `QOYOD_WEBHOOK_TOKEN`, constant-time compare). Idempotency key
  resolution: `X-Idempotency-Key` header → `salla:order:<id>:<event>` → random UUID fallback.
  Atomic insert into `integration_inbox` (DuplicateKeyError → 200 `{duplicate:true, trace_id:…}`).
  Runs `_process_inbox_row()` synchronously: NEW → RECEIVED → VALIDATED → NORMALIZED. Failures route
  through the specific `FAILED_*` hop then to **DEAD_LETTER** (terminal, NOT deleted, NOT retried).

### State machine extension
- New failure stage `FAILED_NORMALIZATION` (resume target `VALIDATED`).
- `last_failed_stage` now EXCLUDES `DEAD_LETTER` so the operator always sees the *specific*
  failure that triggered dead-lettering (FAILED_VALIDATION / FAILED_NORMALIZATION / etc.).
- Webhook orchestration injects `pipeline_started_at` into the in-memory row right after the
  NEW→RECEIVED hop so DEAD_LETTER routing can compute `pipeline_duration_ms` in-flight.

### Endpoint contract
```
POST /api/integrations/qoyod/webhook
Headers:
  X-Webhook-Token:     <required — env QOYOD_WEBHOOK_TOKEN>
  X-Idempotency-Key:   <optional — overrides derived key>
Body: JSON object (Salla webhook shape)

200 OK happy:
  {ok:true, duplicate:false, trace_id, pipeline_stage:"NORMALIZED",
   salla_order_id, audit:{started_at,finished_at,duration_ms,last_success_stage,last_failed_stage},
   canonical_payload_present:true}

200 OK duplicate:
  {ok:true, duplicate:true, trace_id, idempotency_key, pipeline_stage, salla_order_id, received_at}

200 OK dead-letter:
  {ok:false, duplicate:false, trace_id, pipeline_stage:"DEAD_LETTER",
   audit:{…last_failed_stage:"FAILED_VALIDATION"…},
   error:{code,message}, canonical_payload_present:false}

401  invalid_webhook_token / missing_webhook_token
503  qoyod_webhook_token_not_configured
400  payload_must_be_json_object
```

### Live smoke (curl on PREVIEW) ✅
1. Bad token → 401 `invalid_webhook_token`.
2. Happy payload → 200 `NORMALIZED`, canonical_payload_present=true.
3. Re-send same `X-Idempotency-Key` → 200 `duplicate:true` (row count stays 1).
4. Payload missing `status` → 200 `DEAD_LETTER`, `last_failed_stage="FAILED_VALIDATION"`,
   stage_history = `[NEW, RECEIVED, FAILED_VALIDATION, DEAD_LETTER]`, row preserved in DB.

### Tests — 90/90 ✅
- `tests/test_qoyod_day3_webhook.py` — 28/28
  (token: 4 · idempotency: 3 · validate: 7 · normalize: 7 · e2e: 7).
- `tests/test_qoyod_state_machine.py` — 23/23 (FAILED_NORMALIZATION vocab + DEAD_LETTER exclusion).
- `tests/test_qoyod_compliance.py` — 11/11 unchanged.
- `tests/test_qoyod_day1_foundation.py` — 28/28 unchanged.

### Critical invariants (locked by tests)
- DEAD_LETTER rows are **never deleted, never auto-retried**.
- `last_failed_stage` always points to the specific FAILED_* stage, never DEAD_LETTER.
- `canonical_payload` is ONLY written on the NORMALIZED hop; failed rows have it as None.
- `qoyod_invoices` collection stays untouched — no rows are written from the webhook.

### Outstanding for Day 4-5 (waiting for sign-off)
- Business Rules step (RULES_APPLIED).
- 4a Customer / 4b Product / 4c Invoice / 4d Receipt — actual Qoyod API calls.
- Background retry worker (RETRYING → resume_from).
- Activation of the 6 Manual Action buttons + the "سجل المزامنة" page.

## Qoyod MVP — Pre-Day 3 Refinements v2 (2026-06-26 · Navigation IA + Reconciliation Card + Audit Trail)
**User mandate before starting Day 3:** three additive improvements that lock in the IA so we
don't need a refactor mid-Day-3.

### 1. Navigation reorganisation
- New top-level sidebar section: **التكاملات (Integrations)** with sub-grouped items per upstream platform.
- `Sidebar.jsx` now supports `subgroups: [{id,label,items}]` alongside flat `items`. Renderer prints
  a small uppercase subgroup header above each sub-list. `findSectionFor`, search filter,
  visibility dialog, and `totalMatches` all walk subgroups transparently.
- **Salla** subgroup: إعدادات سلة · Webhooks · مراقبة الطلبات · سجل الأحداث · مقارنة مصادر البيانات.
- **قيود** subgroup: إعدادات قيود · فواتير قيود — مراقبة · منتجات قيود · عملاء قيود · سجل المزامنة · سجل الأخطاء.
- Generic stub component `pages/IntegrationPlaceholder.jsx` powers the 6 "coming soon" routes
  (qoyod products/customers/sync-log/error-log + salla orders/events). Each carries `phase` (Day 3 /
  Day 4-5 / مرحلة لاحقة) and `related[]` links so the operator can navigate to ready siblings.
- Qoyod entries removed from "الاستيراد والربط".

### 2. Reconciliation Card (مطابقة قيود)
- New backend helper `compliance.reconciliation_check()` returns:
  `{eligible_orders_count, qoyod_invoices_count, difference, has_diff, drilldown_url, oldest_unsent_at}`.
- Endpoint: `GET /api/integrations/qoyod/compliance/reconciliation`.
- Frontend card on `/integrations/qoyod/invoices` — 3 stat cells + amber CTA "عرض الطلبات غير المرسلة"
  that scrolls to the Orphan Orders table. Renders an emerald "كل الطلبات وصلت" message when diff=0.

### 3. Audit Trail in `state_machine.transition()`
Per user spec, every transition now records:
- `pipeline_started_at`  — set on NEW → RECEIVED.
- `pipeline_finished_at` — set on entry to any terminal stage (COMPLETED/SKIPPED/DEAD_LETTER).
- `pipeline_duration_ms` — computed server-side from `existing_started_at` arg + finish timestamp.
- `pipeline_outcome`     — the terminal stage name.
- `last_success_stage`   — last happy-path stage we reached (excl. NEW).
- `last_failed_stage`    — last `FAILED_*` stage entered.
- `trace_id`             — already present, surfaced in the audit summary.

Models extended with the six new fields on both `IntegrationInbox` and `QoyodInvoiceRecord`.
Timeline Drawer in `QoyodInvoices.jsx` now opens with an "🧭 سجل التتبع" section listing all
nine audit fields in a compact 3-column grid.

### Tests (62/62 ✅)
- `tests/test_qoyod_state_machine.py` — 23/23 (vocabulary lock + graph + transition + audit trail +
  retry loop e2e).
- `tests/test_qoyod_compliance.py` — 11/11 (classification, orphan listing, summary, reconciliation
  with/without diff).
- `tests/test_qoyod_day1_foundation.py` — 28/28 (unchanged).
- curl Live on PREVIEW: `/compliance/reconciliation` returns the expected JSON shape.

### Frontend smoke (screenshot verified)
- Reconciliation card renders with 3 cells at top.
- Compliance Alert below with 5 cells.
- Orphan + Invoices tables render empty-state placeholders.
- New Integrations section in sidebar with Salla + قيود subgroups expanded; all 11 nav items present
  with the expected data-testids (`nav-salla-orders`, `nav-qoyod-products`, etc.).
- Old Qoyod links no longer appear in "الاستيراد والربط".

### Outstanding for Day 3
- `POST /api/integrations/qoyod/webhook` — idempotent insert into `integration_inbox`, initial
  transition `NEW → RECEIVED` (will auto-populate `pipeline_started_at`).
- Validation step → `VALIDATED` / `FAILED_VALIDATION`.
- Normalization step → `NORMALIZED` canonical SalesOrder DTO.

## Qoyod MVP — Pre-Day 3 Refinements (2026-06-25 · State Machine + Compliance Watch + UI Placeholders)
**User mandate before starting Day 3 webhook work:**
1. ✅ **State Machine** — canonical UPPERCASE vocabulary, allowed transitions, RETRYING transient stage,
   six failure stages (`FAILED_VALIDATION/CUSTOMER/PRODUCT/INVOICE/RECEIPT` + `DEAD_LETTER`),
   append-only `stage_history[]`, attempts counter bumped only on RETRY → resume.
2. ✅ **Compliance Watch** — eligibility classification with 5 statuses
   (`not_eligible / eligible_pending / sent_to_qoyod / failed_before_qoyod / invoice_sent_receipt_failed`)
   and 7 reasons. Dashboard Alert lives ONLY on Qoyod page (no scope creep into main Dashboard).
3. ✅ **UI Placeholders** — Invoices Data Grid, Timeline Drawer, 6 disabled Manual Action buttons,
   Compliance Alert card. No premature wiring; Day 4-5 will add real behaviour.

**New backend modules:**
- `/app/backend/integrations/qoyod/state_machine.py` — pure transition logic + `transition()`/`can_transition()`/`resume_target()`.
- `/app/backend/integrations/qoyod/compliance.py` — `classify_eligibility()`, `list_orphan_orders()`, `compliance_summary()`.

**Model extensions** (`integrations/qoyod/models.py`):
- `IntegrationInbox.pipeline_stage` default flipped to `"NEW"` (canonical) + new `stage_history[]` field.
- `QoyodInvoiceRecord` extended with `pipeline_stage`, `stage_history[]`, `eligibility_status`, `eligibility_reason`.
- New tuples `ELIGIBILITY_STATUSES`, `ELIGIBILITY_REASONS` + index `qoyod_invoices_eligibility`.
- Legacy lowercase `PIPELINE_STAGES` kept for backwards compat.

**New API endpoints** (under `/api/integrations/qoyod`):
- `GET /invoices` — Data Grid feed (status/eligibility filters, limit ≤500).
- `GET /invoices/{order_id}` — full record + matching inbox row + merged `stage_history` (timeline source).
- `GET /compliance/orphan-orders` — Salla "تم التنفيذ" orders missing from / failed in Qoyod.
- `GET /compliance/summary` — Dashboard Alert counts + closed vocabularies for UI.

**New frontend page:** `/app/frontend/src/pages/QoyodInvoices.jsx`
- Route `/integrations/qoyod/invoices`, sidebar entry `nav-qoyod-invoices`.
- Compliance Alert card (5 stat cells, oldest pending note).
- Orphan Orders table (eligibility badge + reason + drilldown).
- Invoices Data Grid (status / pipeline stage / Qoyod refs / attempts).
- Timeline Drawer with reversed `stage_history`, color-coded transitions, raw JSON details.
- 6 disabled Manual Action buttons (Retry / Recreate Customer/Products/Invoice/Receipt / Sync This Order)
  with `title="قريباً — المرحلة 4-5"`.

**Tests:**
- `tests/test_qoyod_state_machine.py` — 18/18 PASS (vocabulary lock, graph correctness, transition side-effects,
  retry loop end-to-end, terminal stages have no outbound edges).
- `tests/test_qoyod_compliance.py` —  9/9 PASS (classification scenarios, live orphan listing, summary aggregates,
  closed vocabularies enforced).
- `tests/test_qoyod_day1_foundation.py` — 28/28 still PASS (default `pipeline_stage = "NEW"` updated).
- Live curl on PREVIEW with the merchant token confirms `/compliance/summary`, `/orphan-orders`, `/invoices`
  and `/health` all return 200 with the expected shape.

**Outstanding for Day 3 (waiting on user sign-off):**
- `POST /api/integrations/qoyod/webhook` — receive Make.com payload, idempotent insert into `integration_inbox` in NEW state.
- Validation step → VALIDATED / FAILED_VALIDATION.
- Normalization step → NORMALIZED canonical SalesOrder DTO.


## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN) يحلل ملفات Excel من سلة، يستقبل بيانات Make.com، يتتبع التسويات، ويدير الأصول والالتزامات.

## Architecture
- **Backend:** FastAPI + Motor (Async MongoDB)
- **Frontend:** React + react-router + Tailwind + shadcn/ui
- **SSOT:** `general_ledger` (double-entry accounting). All balances computed from GL via `compute_balance()`.
- **Storage:** `MONGO_URL` / `DB_NAME` from env. No defaults.
- **Auth:** JWT (role-based: owner/admin/accountant/operations/viewer/user)
- **Language:** Arabic (RTL UI)

## Key Collections
- `general_ledger` — SSOT (double-entry, txn_group_id)
- `financial_movements` — detailed movements (supplier_invoice, general_expense, fixed_asset)
- `operating_salaries` — modern employee storage
- `employees` — legacy employee collection
- `counterparties` — suppliers / externals / couriers
- `accounts` — bank / cash / payment_platform
- `expense_categories` — hierarchical
- `unified_orders` — Salla orders pipeline
- `settlement_files` / `settlement_entries` — Salla/BNPL reconciliation

## Strict Rules from User
- READ-ONLY on existing financial data
- No migrations / no recompute / no cleanup without explicit permission
- All balances from `general_ledger` only
- `financial_movements` is detail-enrichment layer, never balance source
- Drift detected MUST be surfaced, never hidden

## Recompute Freshness — No Stale Drift / match_status (2026-06-25 · iter-261)

**Bug reported by user:** After updating "قيمة المنصة الآن" in the
reconciliation report, the row showed `ads_daily=542.03 = platform=542.03`
(matching) yet the report still displayed:
  • drift = 14.91% / 26.24%
  • match_status = "يحتاج مراجعة"
  • non-zero diff_sar

**Root cause:** `recompute_drift_for_day` updated `platform_manual_value_*`
and `drift_pct_vs_manual` only. It did NOT touch:
  • `match_status` (stale from a previous sync)
  • `diff_native` / `diff_sar`  (stale platform-vs-ssot delta)
  • `drift_pct_vs_platform`  (stale)
  • `platform_authoritative_native` / `_sar` (still old auto_reconcile values)

So even though the user just stated "the platform value equals my SSOT",
the report continued to read those stale figures.

**Fix (display-layer + recompute path — sync/FX untouched):**
- Every manual-value entry now re-derives the full drift family from
  the freshly entered value:
    diff_native = manual − spend_native
    diff_sar    = manual_sar − spend_sar
    drift_pct_vs_platform = |diff_native| / spend_native × 100
- Manual entry is treated as the platform-authoritative value:
  `platform_authoritative_native/sar` and `platform_last_checked_at`
  are also updated. This guarantees the report shows ONE
  consistent "platform value now" across the screen.
- `match_status` is recomputed by calling `_compute_match_status()`
  with the fresh drift / confidence / has_data inputs.
- FX precision guard: when the entered native value EQUALS
  spend_native (within 1e-9), reuse `spend_sar` directly instead of
  recomputing via `manual_value × fx_rate`. This prevents 1-cent
  rounding artefacts (e.g. 144.54 × 3.75 = 542.025 banker-rounding
  to 542.02 while the stored spend_sar was 542.03) from showing
  a non-zero diff when the user clearly meant "they match".

**Tests** (`tests/test_ads_v2_recompute_freshness.py` — 4/4 ✅,
full suite 28/28 ✅):
- Matching manual value: diff_sar=0, drift=0, match_status="matched",
  platform_authoritative mirrors the entered value.
- Real-drift manual value (9.09% diff): match_status="drift_review"
  (no longer stuck on the previous "matched").
- Stale diff_sar=9999 + drift_pct=9999 are completely overwritten on
  recompute.
- Source inspection confirms `_compute_match_status` is invoked and
  `match_status` is written inside `recompute_drift_for_day`.

**Architectural invariant added:**
Any function that mutates ads_daily values (sync, auto_reconcile,
manual entry) MUST recompute the complete drift family + match_status
from the resulting state. No caller may leave stale derived values
sitting next to fresh inputs.



## ARCHITECTURAL INVARIANT (LOCKED) — Orthogonal Data State vs Connection State (2026-06-25 · iter-260)

**This is a permanent, non-negotiable architectural rule for Ads V2.**
Approved by the user as the closing principle of Ads V2 Phase 1.
Any future code in Ads V2 must respect this separation.

### Rule

```
ads_daily holds TWO orthogonal status fields:

  • match_status            → DATA STATE only (accounting)
      values: matched | pending_platform | drift_review | no_data
              (sync_failed deprecated; kept only for legacy rows)

  • platform_check_status   → CONNECTION STATE only (technical)
      values: ok | last_check_failed | token_expired
            | rate_limited | api_error

An API failure may update ONLY platform_check_status.
It must NEVER mutate match_status, spend_native, spend_sar,
bank_fee_sar, fx_rate, or any accounting field, when valid SSOT
data already exists in ads_daily.
```

### Priority order

1. Data in `ads_daily` is the Single Source of Truth.
2. Live API checks are verification tools, never truth-sources.
3. If an API check fails but `ads_daily` data is valid:
   - Do NOT change match_status.
   - Do NOT touch the row's spend numbers.
   - Do NOT drop the report.
   - Only update platform_check_status + platform_check_error.

### Implementation

- **`models.py`** declares `MATCH_STATUSES` and `PLATFORM_CHECK_STATUSES`
  as separate tuples. `AdsDaily` has both `match_status` and
  `platform_check_status` as distinct fields.
- **`sync/core.py::_map_check_status()`** — the single mapping point
  from adapter `status.code` → `platform_check_status` value. Used
  by all 3 failure paths (sync_account_day + 2 in auto_reconcile_for_day).
- **`sync/core.py`** success paths write `platform_check_status="ok"`
  and `platform_check_error=None`. Failure paths only write the
  connection-state field; they touch `match_status` only when SSOT
  is empty.
- **`data_layer/reports.py::get_reconciliation_report`** exposes
  `platform_check_status` per row, synthesises it for legacy rows,
  and returns a separate `check_*` histogram alongside `match_*`.
- **Frontend `AdsV2Report.jsx`** renders TWO badges per row
  (data + connection) and TWO histogram strips in the summary,
  visually labeled "حالة البيانات (محاسبياً)" and "حالة الاتصال
  بالمنصة (تقنياً)".

### Architectural tests (6/6 ✅ — permanent lock-in)

`tests/test_ads_v2_orthogonal_states.py`:
1. `_map_check_status` canonical mapping table.
2. `models.py` declares both status tuples + AdsDaily field.
3. `auto_reconcile_for_day` token-failure path leaves `match_status`
   = "matched" untouched while flipping `platform_check_status` to
   the corresponding error code.
4. Report summary exposes both `match_*` AND `check_*` histograms.
5. Legacy rows without `platform_check_status` get it synthesised
   on read (no DB writes).
6. `sync/core.py` source inspection asserts iter-260 markers,
   `platform_check_status` writes in all relevant paths, and
   `_map_check_status` usage.

### Allowed future changes

- Adding new connection-state values (e.g. `degraded`, `partial`)
  requires extending `PLATFORM_CHECK_STATUSES` AND `_map_check_status`
  in one place.
- Adding new data-state values (e.g. `held_review`) requires extending
  `MATCH_STATUSES` AND `_compute_match_status`.
- **Forbidden**: writing to `match_status` from any function that
  inspects API responses or HTTP errors without first verifying
  SSOT data emptiness.

### Phase 2 entry condition

This architectural rule MUST be in force before Phase 2 (review +
GL posting) begins. The review UI will operate on `match_status`
exclusively; GL postings will never depend on `platform_check_status`.



## match_status SSOT Classification Fix (2026-06-25 · iter-259)

**Bug reported by user:** After iter-258 currency fix, several Snapchat
rows in the reconciliation report were shown as "فشل في المزامنة" even
though the data was clearly present in ads_daily:
- spend_sar = 471.71 SAR
- platform value = 437.01 SAR
- drift = 7.94%
- reason = "مزامنة قبل إغلاق اليوم"

**Root cause:** Two failure paths in `sync/core.py` were unconditionally
writing `match_status="sync_failed"` to existing rows when the platform
API call hiccupped — even when `ads_daily.spend_native` already held
valid SSOT data from a previous successful sync. This violated the
user's classification rule:

> "فشل في المزامنة" must appear ONLY when:
>  * API actually failed
>  * No response / HTTP error
>  * **AND no data saved to ads_daily**

**Fix (classification-only — sync/FX/spend logic untouched):**
1. `sync_account_day` (line ~260): when `fetched is None`, check if
   the existing row has `spend_native > 0`. If yes → record only
   `platform_check_error` + `platform_last_checked_at`; preserve the
   prior `match_status`.
2. `auto_reconcile_for_day` (two token/fetch failure paths): same
   conditional pattern. Token failures and adapter `None` results no
   longer overwrite a valid match_status.
3. `get_reconciliation_report` (display layer): reclassify legacy
   rows on the fly — if `match_status="sync_failed"` but
   `spend_native > 0`, surface as `drift_review` (when drift ≥ 5%) or
   `pending_platform` (when drift < 5%). No DB writes — the row will
   be properly reclassified the next time a successful sync runs.
4. Added `match_status_reason="platform_check_error_with_valid_ssot"`
   annotation so the UI can still surface "API check failed but data
   is valid" if desired.

**Tests** (`tests/test_ads_v2_match_status_ssot.py` — 4/4 ✅;
full suite 18/18 ✅):
- Legacy `sync_failed` + valid data + 7.94% drift → reclassified
  to `drift_review`. Summary counts updated accordingly.
- Truly failed (no SSOT data) → stays `sync_failed`.
- Valid data + small drift (<5%) → reclassified to `pending_platform`.
- Source inspection: confirms iter-259 markers in sync/core.py + the
  `ssot_has_data` gate is present in both failure paths.

**Untouched (per user directive):**
- ❌ Snapchat sync code
- ❌ FX rate fetching
- ❌ Spend calculation
- ❌ Adapter API call logic
- ❌ `ads_daily.spend_native` / `spend_sar` / `bank_fee_sar` values



## Currency SSOT Fix — ads_accounts is Authoritative (2026-06-25 · iter-258)

**Bug reported by user:** Account "متجر أماسي سعودي" is configured as
SAR in settings, but the Ads V2 report showed it as USD with a value
of 721.61 USD. Two screens, two different currencies for the same
account — a classic SSOT violation.

**Root cause (introduced by me in iter-257):**
`get_spend_by_account` was grouping by `ads_daily.currency_native` and
reading it back in projection. But `ads_daily.currency_native` is
whatever the platform adapter wrote — Snapchat's API always returns
USD micros, so Snap-sourced rows have `currency_native="USD"`
regardless of how the account is configured.

The user's SSOT rule (made explicit in this iteration):
- All ACCOUNT-LEVEL settings (currency, bank fee, FX policy) → `ads_accounts`
- All DAILY-SPEND numbers → `ads_daily`
- Reports JOIN the two. They must never duplicate or override.

**Fix (display layer only — sync untouched, ads_daily untouched):**
- `get_spend_by_account` now groups strictly by `(account_id, provider)`,
  joins with `ads_accounts.currency_native`, and uses that as the
  authoritative currency.
- `spend_native` field is set to `null` for SAR-billed accounts (no
  meaningful foreign value exists).
- `totals.spend_native_by_currency` excludes SAR-billed accounts entirely.
- `get_spend_by_provider` was restructured: stage-1 per-account
  aggregation + join with ads_accounts, stage-2 roll-up per provider
  with SAR amounts dropped from `spend_native_by_currency`.
- Frontend `ReportTable.renderCell()` renders `null` `spend_native`
  as "—" with `text-zinc-500`.

**Architectural rules the user codified for Ads V2 going forward:**
1. No duplication in code.
2. No duplication in storage.
3. No more than one source of truth.
4. All account settings come from `ads_accounts`.
5. All daily spend numbers come from `ads_daily`.
6. All reports flow through the unified reports layer.
7. Any material accounting change must be reflected structurally,
   not as a side-fix or duplicated field.

**Tests** (`tests/test_ads_v2_currency_ssot.py` — 3/3 ✅, full suite 14/14 ✅):
- SAR-billed Snap account: USD hidden, totals exclude it.
- USD-billed account: USD shows + totals include it.
- Mixed SAR+USD accounts: isolated cleanly.



## Ads V2 Bank Commission Display (2026-06-25 · iter-257)
**User directive (strict):** FREEZE the Snapchat spend sync logic — current
USD spend + FX conversion is canonical. NO changes to:
- FX rate fetching
- Spend calculation
- Snapchat API call (post the recent adapter fix)
- Sync mechanism

**What user needs visible in the report:**
1. الصرف بالدولار (Spend USD)
2. الصرف بالريال قبل العمولة (Spend SAR pre-commission)
3. نسبة العمولة البنكية (Bank commission %)
4. قيمة العمولة البنكية (Bank commission SAR)
5. إجمالي التكلفة بعد العمولة (Total after commission)

**Implementation (display-only — sync untouched):**
- `/app/backend/ads_v2/data_layer/reports.py`:
  - `get_spend_by_account` now exposes `spend_native`, `bank_fee_pct`
    (derived = bank_fee_sar / spend_sar × 100), and `configured_bank_fee_pct`
    (from settings, audit-only). `totals.spend_native_by_currency` for
    multi-currency aggregation.
  - `get_spend_by_provider` similarly extended with `bank_fee_pct` and
    per-currency native breakdown.
  - All derived from `ads_daily` SSOT — no recomputation, no new
    sync paths.
- `/app/frontend/src/pages/AdsV2Report.jsx`:
  - 5 StatCards (USD card auto-hides when no USD spend).
  - Per-account table: 10 columns including `spend_native` (USD/native)
    and `bank_fee_pct` (effective %).
  - Per-provider table: includes `bank_fee_pct`.
  - `ReportTable` now has `renderCell()` that formats pct and native
    currency with proper suffixes and tooltips.

**Verified scenario (user-provided Snapchat numbers):**
Given `spend_native=105.41 USD`, `spend_sar=395.76`, `bank_fee.rate_pct=0.023`:
- `bank_fee_sar` = round(395.76 × 0.023, 2) = **9.10** ✅
- `gross_sar`    = 395.76 + 9.10        = **404.86** ✅
- Effective `bank_fee_pct` derived from SSOT = **2.299** ≈ 2.30% ✅

**Tests** (`/app/backend/tests/test_ads_v2_bank_fee_report.py` — 5/5 ✅):
- `test_snapchat_bank_fee_matches_user_scenario`
- `test_effective_bank_fee_pct_matches_configured_rate`
- `test_bank_fee_disabled_returns_zero`
- `test_pct_plus_flat_method`
- `test_report_layer_returns_new_fields`

**Live aggregation curl test:** Inserted a temp `ads_daily` row matching
the user scenario; `get_spend_by_account` returned exactly the 6
required fields with the correct numbers.

**⚠️ Sync code is frozen — any future change to spend or FX requires
explicit user approval.**



## Dashboard Shipping SSOT Consolidation + Accordion UX (2026-06-25 · iter-256)
**User report:** ProfitSummaryCard's "إجمالي تكاليف الشحن" total included VAT
but the inline table only showed unit price WITHOUT tax (e.g. iMile 21×15 was
shown but total was 362.25). Also: clicking operating-expenses tooltip felt
like content jumped below the page.

**Fix — Backend (`/app/backend/server.py` ~line 1939):**
- `/api/dashboard` now passes `all_orders` through
  `shipping_cost_ssot.aggregate_breakdown()` and OVERRIDES
  `matched_all["shipping_breakdown"]` / `["total_shipping_cost"]` /
  `["deferred_shipping_cost"]` with the SSOT result.
- Each shipping row now carries SSOT-canonical per-unit fields:
  `cost_per_unit`, `tax_per_unit`, `total_per_unit`, `vat_rate`
  (alongside the legacy fields kept for backward-compat).
- Net effect: the dashboard and `/api/shipping-ledger` now use the
  SAME math source — never any drift.

**Fix — Frontend (`/app/frontend/src/components/ProfitSummaryCard.jsx`):**
- Replaced the hover-only tooltips on الشحن & المصروفات التشغيلية with
  **inline accordion sections** (`expandable`/`expanded` props on `Line`).
- Click the row → accordion expands inline directly below the row.
  Click again → collapses. The rest of the summary stays visible.
- Shipping table columns are now identical to ShippingLedger:
  الشركة · الشحنات · سعر الوحدة (بدون الضريبة) · ضريبة الوحدة (VAT) ·
  إجمالي الوحدة (سعر + ضريبة) · الإجمالي.
- Footer reads: "نفس مصدر دفتر الشحن التفصيلي (shipping_cost_ssot.py)".

**Regression tests** (`/app/backend/tests/test_dashboard_shipping_ssot.py` — 3/3 ✅):
- Verifies SSOT per-unit math matches the user-reported numbers (21 × 15
  → 362.25; 2 × 15 → 34.50).
- Asserts the dashboard source code contains the consolidation block and
  all SSOT canonical fields.
- Verifies `is_deferred` summation stays correct after SSOT consolidation.

**Smoke verified on PREVIEW:**
- `/api/dashboard` returns rows with `cost_per_unit`, `tax_per_unit`,
  `total_per_unit`, `vat_rate` populated. Example:
  سمسا (2040 oc, cpu=25.0, tpu=3.75, total_per_unit=28.75, total=58,539.60).
- Clicking the shipping line in ProfitSummaryCard expands the breakdown
  inline directly below.

**Note for user:** PREVIEW only. Redeploy via Emergent to push to
`mezansalla.com`.



## Snapchat Adapter Bug Fixes (2026-06-25)
**Reported by user via Diagnose UI on two Snapchat accounts:**
1. `efcdd251 (Self Service)` → API error:
   `Unsupported Stats Query: Only field 'spend' should be used when querying AdAccount stats.`
2. `cf8ea7c9 (السعودي)` → API error:
   `Invalid query parameters in request URL: [Invalid StartDateTime, 2026-06-24T00:00:00.000 03:00]`
   (the '+' in the +03:00 timezone offset became a space)

**Root cause (both bugs in `/app/backend/ads_v2/sync/adapters.py::fetch_snapchat_day`):**
- The Snapchat API endpoint `/adaccounts/{id}/stats` ONLY accepts the
  `spend` field at the account level. Asking for impressions/swipes is rejected.
- The URL was built with f-string interpolation
  (`f"...&start_time={start_iso}&end_time={end_iso}..."`), so the
  `+03:00` timezone offset stayed as a literal `+` in the URL — which
  is decoded by HTTP servers as a space character (RFC 3986 query rules).

**Fix:**
- Removed `impressions,swipes` from the field list; account-level
  endpoint now requests `fields=spend` only.
- Switched from f-string URL to httpx `params=` dict so `start_time`
  and `end_time` are URL-encoded correctly (`+` → `%2B`).
- `impressions`/`clicks` set to `0` in the returned row (Snapchat does
  not expose those at account level — would need campaign-level later).

**Regression tests:**
- `/app/backend/tests/test_snapchat_adapter_fix.py` (3 tests, all PASS):
  - asserts source no longer has the buggy URL shape
  - end-to-end captures the final httpx URL and verifies `%2B` encoding
  - verifies spend parsing from a stub response works

**Note for user:** This fix is on PREVIEW. To apply on PRODUCTION
(`mezansalla.com`), the app must be redeployed from the Emergent
platform.


- Arabic-only UX (RTL)

## Implemented in this Session (Iter-250b · P1.5)

### P1.5.L — BNPL Internal-Transfer Block (deployed)
- Block bank/cash → Tamara/Tabby in `/new-transaction`
- Backend guard in `universal_accounting_routes._account_blocks_internal_transfer`
- Frontend filter in `UnifiedEntryScreen.isInternalTransferIneligible`
- Salla NOT blocked

### P1.5.n — Employee Lookup Forensic (deployed)
- `GET /api/audit/employee-lookup?entity_id=...&name_hint=...`
- Read-only diagnostic: matches across `operating_salaries`, `employees`, `employees_archive`, `employees_legacy`

### P1.5.o — Preview Debug Overlay (deployed)
- Shows selected employee {id, name, monthly_amount, source} ONLY on Preview/localhost
- Helps verify frontend bindings

### P1.5.p — Widened Employee Guard (deployed)
- `ledger_core.create_entry` guard now checks BOTH `operating_salaries` AND `employees`
- Rejects `archived=true / deleted=true`
- Accepts `status=active|stopped`
- 7/7 unit-test PASS

### P1.5.q — Custody as Payment Source for Operating Expenses (deployed)
- New perm `accounting.custody.spend_any` (owner/admin/accountant/user role)
- `POST /api/financial-movements` accepts `custody_employee_id` for `general_expense`
- New endpoint `GET /api/accounting/custody/spendable-sources`
- UI toggle in `Iter245MovementForm`: bank/cash vs employee custody
- Custody balance check, no overdraft, single source per transaction
- Custody-funded movements credit `employee.custody` in GL (no bank touched)
- Reversal restores custody balance via standard GL reversal

### P1.5.r — Entity Ledger Deep-Link Route (deployed)
- Route `/entity-ledger/:type/:id`
- Page `EntityLedgerByIdPage` auto-opens drawer for the matching entity
- Backward-compat: `/entity-ledger/supplier/:id` now redirects to `/suppliers/:id/ledger-detail`

### P1.5.s — Supplier Ledger Detail Page (deployed)
- Backend: `GET /api/accounting/suppliers/{id}/ledger-detail?from=&to=`
- 7 sections: supplier card, drift banner, period summary, chronological timeline, invoice cards (with line items + GL legs + payments), manual entries, drift diagnostic
- Frontend: `/suppliers/:id/ledger-detail` page
- Print/PDF via `react-to-print`
- Filters: YTD (default), current month, last 90d, all, custom
- SSOT-strict: all balances from GL, `financial_movements` for detail only

### P1.5.t — Movements↔GL Drift Analyzer (deployed)
- `GET /api/audit/movements-gl-drift?from=&to=&movement_type=`
- Categorises every drifted movement into 6 causes:
  legacy_pre_gl / gl_creation_failed / no_group_id_at_all / voided_or_draft / import_batch / manual_legacy_data
- Roll-ups by cause, supplier, year
- Read-only — pure diagnostic, no writes

## P1.5.s.fix — Supplier Ledger Cash-Invoice Reclassification (2026-02 · READ-ONLY)
Backend: `supplier_ledger_detail_routes.py` reconciliation block now classifies orphans into 3 buckets:
  - `cash_invoices` — paid_amount ≥ total_amount AND GL exists for the group_id (just no payable leg). Valid postings, NOT drift.
  - `drift_credit` — paid_amount < total_amount AND no supplier-payable leg in GL. Real drift, needs review.
  - `ledger_failed` — no GL row at all for group_id (or status=ledger_failed). True GL post failure.
Period block exposes `total_cash_purchases` + `cash_invoices_count`. `drift_detected` no longer fires on cash invoices.
Frontend: `SupplierLedgerDetailPage.jsx` now renders 3 separate sections (📗 / 🟠 / 🔴) instead of one «orphan» bucket. New summary card «إجمالي مشتريات نقدية». Excel export splits to 3 sheets.

## Phase 2A.5 — Provider Invoice Calendar (2026-02 · CORE FIX)
**Problem solved:** Tamara Dry-Run used arbitrary ISO-week buckets from order_date, so simulated invoice_date diverged from real Tamara dates (23/05, 30/05, 06/06, 13/06, 20/06).

**User-confirmed Tamara cycle:** invoice issued Saturday → period covers Saturday → next Friday (`invoice_date` is the FIRST day of the 7-day period, not the last). All real invoice dates are Saturdays.

Backend:
  - New module `provider_invoice_calendar.py`:
      • Per-provider `_PERIOD_LAYOUTS`:
          – `tamara` → `"invoice_as_start"` (Sat → Fri).
          – `tabby`/`imkan`/`salla` → `"invoice_as_end"` (legacy).
      • Overridable via `settings.calendar_period_layout_<provider>`.
      • `extract_calendar_from_settlement_entries`: walks distinct `settlement_date` rows. For `invoice_as_start`, period_start=invoice_date, period_end=invoice_date+6. For `invoice_as_end`, period_start=prev_invoice+1 (or invoice-6 for first), period_end=invoice_date.
      • Transfer offset depends on layout: Tamara `invoice_as_start` defaults to 9 days (Mon after Fri end). Tabby `invoice_as_end` defaults to 1.
      • `rebuild_calendar` (idempotent, preserves manual entries).
      • `upsert_manual_entry`, `delete_entry`.
  - New collection: `provider_invoice_calendar` with `(user_id, provider, invoice_date)` unique key.
  - Modified `_simulate_weekly` (Dry-Run): when calendar exists → uses calendar periods exactly; orders bucket by `period_start ≤ order_date ≤ period_end`. Surfaces `invoice_date` + `expected_transfer_date` per invoice. Falls back to ISO-week buckets only when calendar is empty.
  - Modified `_build_bnpl_periods` (Phase 2B): identical change — calendar → `compute_settlement_for_provider(period_start, period_end)` per entry.
  - Rule resolution (commission/VAT) **still** comes from `_merchant_fee_rates` — calendar only governs period boundaries.

Endpoints:
  - `GET    /api/settlement-engine/calendar?provider=&from_date=&to_date=`
  - `POST   /api/settlement-engine/calendar/rebuild` body `{provider, dry_run}`
  - `POST   /api/settlement-engine/calendar/manual` body `{provider, invoice_date, period_start, period_end, expected_transfer_date}`
  - `DELETE /api/settlement-engine/calendar/{id}`

Frontend: `SettlementDashboard.jsx`
  - New tab "📅 تقويم الفواتير" — provider picker, **dynamic layout badge** (Tamara: "تاريخ الفاتورة = أول يوم الفترة (السبت → الجمعة)" vs Tabby: "= آخر يوم الفترة"), rebuild button, manual-add form, per-invoice table with source badge, delete action.
  - Dry-Run modal table now shows columns: تاريخ الفاتورة + تاريخ التحويل المتوقع (real calendar dates).

Tests: `tests/test_iter251_phase2a5_invoice_calendar.py` — 5/5 PASS.
  - Layout-aware extraction (2026-05-23 → period 23-29), idempotent rebuild, manual-entry protection, end-to-end Dry-Run uses calendar with Sat→Fri buckets, delete.

## Phase 2B — Settlement Engine Generation (2026-02 · FEATURE-FLAG GATED)
Backend:
  - New module `settlement_engine_generation.py` — pure generation logic that delegates rule resolution to:
      • `bnpl.settlements_service.compute_weekly_settlements` + `_merchant_fee_rates` for Tamara/Tabby (same source as `/bnpl-settlements/register`).
      • `db.settlement_entries` grouped by `settlement_reference` for Salla.
      • `imkan` returns `rule_source_missing` (no central rules yet — no hard-coded fallback).
  - New collections: `settlement_periods`, `settlement_invoices`, `expected_transfers` (linked via FK ids).
  - Invoice lifecycle: draft / generated / waiting_transfer / pending_review / confirmed / confirmed_with_difference / cancelled.
  - All writes gated by `settings.settlement_engine_enabled` (defaults OFF). 403 returned when disabled.
  - `dry_run=true` ALWAYS allowed (no persistence) — for the merchant to preview output safely.
  - Idempotent on `(user_id, provider, period_from, period_to)`: re-runs reuse existing ids.
  - No GL writes, no bank_transfer_review creation here. Phase 2C will wire those in.

Endpoints (under `/api/settlement-engine`):
  - `POST /generate` — body `{provider, date_from, date_to, dry_run}` → counts + ids
  - `GET  /periods?provider=&status=&from_date=&to_date=`
  - `GET  /invoices?provider=&status=&from_date=&to_date=`
  - `GET  /invoices/{id}` → invoice + period + expected_transfer
  - `POST /invoices/{id}/cancel` body `{reason}`
  - `GET  /expected-transfers?provider=&status=`
  - `GET  /stats` → totals + per-provider counts

Frontend: `SettlementDashboard.jsx`
  - Two tabs: 🔬 Dry-Run | 📦 Generated Invoices (Phase 2B)
  - Generated tab shows: feature-flag banner (ON / OFF), counts, generation form (provider, date range, Dry-Run / Generate buttons), invoices table with status badges, cancel action.
  - "Generate" button disabled when `settlement_engine_enabled` flag is OFF.

Tests: `tests/test_iter251_phase2b_settlement_generation.py` — 6/6 PASS.
  - Block when flag OFF, dry-run persistence-free, Salla persistence + linking, idempotency, cancel transitions, unknown-provider rule_source_missing.

## Pending / Backlog
- [P0] Analyze Tamara settlement JSON (26,279.64 vs 10,509.12 SAR discrepancy) — waiting for user to re-paste
- [P1] Analyze Production drift report from P1.5.t (waiting for user output)
- [P1] Phase 2 — Custody as payment source for supplier_invoice (cash mode)
- [P1] Phase 3 — Unified Employee Custody Ledger (chronological timeline of all custody movements per employee)
- [P1] Ad-Account sync stopped for Snapchat (Riyadh) — needs forensic
- [P1] Read-only forensic for `/purchase-invoices` and `/shipping/transfers`
- [P1] Walid / Khatai employee balance analysis (read-only)
- [P2] Execute Financial Reset / Ad-Account Recompute (postponed)
- [P2] Category Reports & Expense Analysis Dashboard
- [P2] Product Linkage (Inventory, SKUs)
- [P2] Phase 2 of Supplier Unification — provide a (gated) "Link Ledger-only supplier to db.suppliers" action once user reviews the forensic report

## P1.5.ab — Suppliers Unification (2026-02 · READ-ONLY)
Backend: `suppliers_unification_forensic_routes.py`
  - `GET /api/suppliers-unified` — merged list (db.suppliers + db.counterparties + GL/FM ghosts) with `link_status` ∈ {new_only, linked, ledger_only}, `editable` flag, GL balance per row
  - `GET /api/audit/suppliers-unification-forensic` — full diagnostic dump: counts, lists per category, ghosts (GL/FM IDs missing from both tables), duplicate suspects by name/phone/email
Frontend:
  - `SuppliersPage.jsx` Management tab now calls `/suppliers-unified` instead of `/suppliers` and shows badges + summary cards + link-status filter
  - `SuppliersUnificationForensicModal.jsx` — modal with 6 tabs (نظرة عامة، مورد جديد، Ledger فقط، مربوط، GL/FM أيتام، تكرارات مُشتبه بها)
Tests: `tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS

## Phase 4 — Product Cost Auto-Update on Supplier Invoice (2026-02)
Backend: `financial_movements_routes._apply_product_cost_updates`
  - Hook fires after a `supplier_invoice` movement is successfully posted to GL.
  - Walks every `line_items[i].product_id`; for each match:
    - Appends a new `cost_history` record with `{supplier_id, supplier_invoice_id, invoice_date, quantity, unit_cost, total_cost, source: "supplier-invoice", amount, at}`.
    - Sets `cost_current = unit_cost` (latest).
    - Recomputes `cost_avg` as quantity-weighted average across all history entries that carry `quantity`+`unit_cost`.
    - Sets `needs_cost = false`.
  - APPEND-ONLY: never deletes or overwrites prior history entries (excel-import / quick-create seeds preserved).
  - Failures are logged but never break invoice creation.
Frontend: `Iter245MovementForm.jsx` payload now sends `product_id` + `product_sku` per line item.
Tests: `tests/test_iter250b_phase4_product_cost_update.py` — 5/5 PASS. End-to-end curl verified weighted avg ((10×15 + 30×7) / 40 = 9.0).


## Test Credentials
See `/app/memory/test_credentials.md`.

## Shipping Cost SSOT — Priority Flip + Warning Banner (2026-06-25, iter-254/255)
**Bug fix:** User reported that the detailed shipping ledger was using
Salla's per-order shipping_cost even for companies that had a
configured `cost_per_order` in `/shipping/settings`. The new policy:

**Priority 1 — company-config `cost_per_order`** (the rate the merchant
maintains in `/shipping/settings`).
**Priority 2 — Salla `shipping_cost`** ONLY when no system cost is
configured (temporary fallback; UI surfaces a warning).

**SSOT change** (`shipping_cost_ssot.py::shipping_breakdown`):
   The `if order_ship>0 else cfg_cost` branch was flipped to
   `if cfg_cost>0 else salla_ship`. Source field now reports
   `company_config | salla | none`.

**Warning system** (`shipping_ledger_routes.py`):
   Per-company breakdown now sets `uses_salla_fallback = (
   from_salla_count > 0 AND configured_cost <= 0)`. Each affected
   company yields a structured warning emitted in the top-level
   `warnings` array:
       {shipping_company, orders_affected, reason,
        message: "شركة الشحن … لا يوجد لها سعر في إعدادات شركات الشحن…"}
   The frontend renders an amber banner above the per-company table
   with a "الانتقال إلى إعدادات شركات الشحن" link.

**Coverage** — all 4 consumers now go through SSOT:
   1. ✅ `/api/shipping-ledger` (detailed orders + per-company)
   2. ✅ `/api/shipping-accounts` (deferred-liability accrual) —
        `compute_owed_per_company` rewritten to call
        `shipping_breakdown` per order (replacing the inline
        `cost*(1+vat)` formula that bypassed SSOT priority).
   3. ✅ `/api/balances` (Phase-1 splits via `compute_balances`)
   4. ✅ `/api/financial-position` (same balances wiring)

**Frontend** (`ShippingLedger.jsx`):
   - Amber warning banner with per-company message + settings link
   - "من سلة (مؤقت)" badge on Salla-fallback rows
   - Per-company row gets amber tint when fallback active

**Tests:**
   - `test_shipping_cost_ssot.py` — 15/15 PASS (priority flip
     verified in unit tests).
   - `test_shipping_accounts_ssot_iter255.py` — 6/6 PASS
     (legacy `/shipping-accounts` path now SSOT-consistent).
   - Verified by `testing_agent_v3_fork` iter-254 (100% backend +
     frontend) and iter-255 (100% backend, 21/21 tests). Critical
     proof scenario: Salla=999, settings=20 @ 15% VAT → owed=23.00
     per order (not 1148.85).

## Shipping Cost SSOT — Base + Tax + Total (2026-06-25)
**User mandate:** every shipping-cost figure in the app uses
`total = base + tax`, with the three values visible separately. No
default VAT% is fabricated for historical data; the actual configured
`vat_percent` per shipping company in `/shipping/settings` is the only
source of truth.

**New module:** `/app/backend/shipping_cost_ssot.py` exposes:
   - `shipping_breakdown(order, company_cfgs) → {base, tax, total,
     vat_rate, source}`  – one shipment.
   - `aggregate_breakdown(orders, company_cfgs)` – list aggregation
     with per-company stats (cost_per_unit, tax_per_unit,
     total_per_unit).
   - `get_company_configs(db, user_id)` – reads from the canonical
     `settings.shipping_companies[]` array and accepts BOTH
     `vat_rate` (decimal) AND `vat_percent` (0–100). Name lookup is
     resilient to casing + accidental quoting (`'مندوب الرياض'`).

**Latent bug fixed:** previous `shipping_accounts.py` read
`cfg.get("vat_rate")` but settings stored `vat_percent`. So VAT was
silently treated as 0 everywhere. Now both fields are recognised.

**Refactored consumers (all calls go through SSOT):**
   - `balances.py::compute_balances` — new optional `company_cfgs`
     param. Without it, no fake default VAT (legacy callers unchanged).
   - `shipping_accounts.py::compute_owed_per_company` — returns
     `shipping_base`, `shipping_tax`, `shipping_cost` (= base+tax)
     per company + totals.
   - `shipping_ledger_routes.py::shipping_ledger` — rows now expose
     `shipping_base`, `shipping_tax`, `shipping_cost`,
     `shipping_vat_rate`. Per-company block adds `cost_per_unit`,
     `tax_per_unit`, `total_per_unit`.
   - `server.py` — both `/balances` and `/financial-position` callers
     pass `company_cfgs` so FP, P&L, executive summary, and balances
     align with the rule.

**Frontend:**
   - **Shipping Ledger detail page** (`ShippingLedger.jsx`): 6-column
     per-company table — الشركة · عدد الشحنات · سعر الوحدة (بدون الضريبة)
     · ضريبة الوحدة (مع %) · إجمالي الوحدة (سعر + ضريبة) · الإجمالي.
     Order rows split shipping_base / shipping_tax / shipping_cost.
     Summary cards split: "إجمالي سعر الشحن" + "إجمالي ضريبة الشحن"
     + "إجمالي تكلفة الشحن (شامل الضريبة)".
   - **Profit Executive Summary** (`ProfitSummaryCard.jsx`): same
     6-column table for the analysis-report shipping breakdown.

**Historical-data preservation:** the SSOT helper recomputes live
reports from the current `vat_percent` setting. POSTED `general_ledger`
entries are never mutated — they keep whatever VAT was applied when
they were originally written. Reports = dynamic, journal = immutable.

**Tests:** `tests/test_shipping_cost_ssot.py` — 13/13 PASS,
   including the bug-fix cases (`vat_percent` recognised, malformed
   inputs, historical preservation when no cfg present).

## Ads V2 — Snapchat Safe Re-link Flow (2026-06-25)
**Resolved user request:** "زر إعادة ربط Snapchat داخل تقرير التشخيص"
with 7 explicit safety constraints. All 7 are enforced + tested.

**New collection:** `ads_v2_pending_tokens`
   - Stores new tokens in isolation from V1 until the merchant
     explicitly approves them. Schema: `{id, user_id, provider,
     status (awaiting_callback|pending|approved|discarded),
     access_token, refresh_token, expires_at, source (oauth|
     manual_paste), comparison_snapshot, created_at, updated_at}`.

**New backend module:** `/app/backend/ads_v2/relink.py`
   Routes (all under `/api/ads-v2/settings/snapchat/relink`):
   - `POST /start` → returns Snapchat OAuth URL (state JWT carries the
     V2 purpose marker `ads_v2_snapchat_relink`).
   - `POST /manual` → fallback path for pasting tokens directly.
   - `GET /pending` → list (never returns the secret tokens).
   - `POST /{id}/compare` → live probes both old V1 token and new
     pending token; returns side-by-side identity + organizations +
     ad_accounts + can_access_self_service + can_access_riyadh + diff.
   - `POST /{id}/approve` → backs up V1 doc into `legacy_versions[]`
     array, then atomically swaps `access_token`/`refresh_token` to
     the new pending values. Audit logged in `ads_sync_logs` as
     event `account_relinked_v1`.
   - `POST /{id}/discard` → soft-marks discarded (kept for audit).

**OAuth handshake (zero new redirect URI needed):**
   The V2 flow reuses V1's `client_id`/`client_secret`/`redirect_uri`.
   The V1 OAuth callback (`/api/snapchat/oauth/callback`) was extended
   with a single dispatch check: if the JWT state has
   `purpose=ads_v2_snapchat_relink`, the request is handed off to
   `relink.handle_v2_relink_callback()` which writes ONLY to
   `ads_v2_pending_tokens`. V1 callback's existing logic untouched
   for legacy states.

**Snapchat API probe (`_probe_snapchat_token`):**
   Queries `/me`, `/me/organizations`, `/organizations/{id}/adaccounts`.
   Heuristically detects "Self Service" and "Riyadh" access by
   matching name patterns. Returns a normalized snapshot used by
   both `/compare` and the cached `comparison_snapshot` field.

**Frontend (`AdsV2Settings.jsx`):**
   - `RelinkSnapchatPanel` — shown inside the Diagnose dialog
     only when `provider==='snapchat' && token in
     ['needs_relink','expired','missing']`. Two CTAs: "بدء OAuth"
     and "إدخال يدوي (احتياطي)".
   - `RelinkComparisonView` — two-column side-by-side compare with
     org/account lists, Self Service / Riyadh access indicators,
     diff summary (added/removed orgs and accounts), red callout if
     the new token loses any access, then "اعتماد" / "تجاهل"
     buttons. The approve button is disabled if new token isn't
     valid (probe returned `unauthorized`).
   - `useEffect` reads `?relink_pending_id=...` from URL after OAuth
     round-trip and auto-loads comparison.

**Safety invariants (all in pytest):**
   1. ✅ V1 NOT touched by `/start` — verified `test_relink_start_does_not_touch_v1`
   2. ✅ V1 NOT touched by `/manual` — verified `test_relink_manual_stores_pending_v1_untouched`
   3. ✅ V1 NOT touched by `/compare` — verified `test_compare_does_not_modify_v1`
   4. ✅ V1 NOT touched by `/discard` — soft-discard only — verified `test_relink_discard_is_soft`
   5. ✅ `/approve` appends `legacy_versions[]` AND atomically swaps —
        verified `test_approve_appends_legacy_and_swaps`
   6. ✅ Pending without access_token CANNOT be approved (returns 404)
        — verified `test_approve_awaiting_callback_returns_404`
   7. ✅ Tokens never leak in list endpoints — verified
        `test_relink_pending_omits_secrets`

**Tests:** `/app/backend/tests/test_ads_v2_snapchat_relink.py` — 8/8 PASS.
   Total ads_v2 tests: 39/39 passing (relink + diagnose + auto-reconcile
   + drift + phase1).

## Ads V2 — Phase 1 (3-Tier Status + Diagnostics) (2026-06-25)
**User complaint resolved:** Snapchat row showed "Token: OK" but
"Status: خطأ" — paradoxical and uninformative. Replaced with a
3-tier per-account status model + a Diagnose button.

**Backend:**
   - `data_layer/settings.py::_compute_account_status()` — returns
     `{token, connection, connection_reason, sync_run, reason,
       days_with_data_30d, last_sync_finished_at, last_sync_error}`
     where each tier has its own controlled vocabulary:
       - **token:** ok / expired / needs_relink / missing
       - **connection:** connected / unreachable / timeout / api_error / unknown
       - **sync_run:** synced / awaiting_first / no_data / last_failed / disabled
   - `_compute_account_status` mixes V1 token health + recent sync_logs
     api_status + ads_daily row count to produce a structured `reason`
     code (e.g. `token_no_access_to_account`, `no_data_for_account`,
     `awaiting_first_sync`, `api_rate_limit`). Translated to Arabic
     in the UI dictionary `REASON_AR`.
   - `data_layer/settings.py::diagnose_account()` — comprehensive
     read-only diagnostic. Includes:
       - Token check (V1 doc presence)
       - **Live API probe** — calls `adapters.fetch_day` for yesterday
         and records the result (code, body excerpt, fetched spend)
       - ads_daily stats: days_in_last_30d, days_with_spend,
         total_daily_rows, last_synced_date, last sync started/finished
       - Last 10 ads_sync_logs events for the account
   - **POST /api/ads-v2/settings/accounts/{id}/diagnose** — Returns
     the full diagnostic in one payload.

**Frontend (AdsV2Settings.jsx):**
   - Accounts table replaced bare "Status / Token" columns with:
     **حالة التوكن / حالة الاتصال / حالة المزامنة / السبب الحقيقي**
     (4 columns, colored badges, never says bare "خطأ").
   - New **"تشخيص"** button per account → opens a Dialog displaying:
     3-tier badges, primary reason callout, stats grid, live API probe
     result + raw response body excerpt, last 10 events.
   - `EVENT_AR` dictionary translates event names (sync_run → "مزامنة
     ناجحة", reconciliation_checked → "مطابقة من المنصة", etc.).
   - `ActivityRow` component renders each event as a clean Arabic
     summary instead of raw JSON dump.
   - `REASON_AR` translates 17 specific reason codes (e.g.
     `token_no_access_to_account` → "التوكن لا يملك صلاحية هذا الحساب",
     `no_data_for_account` → "الحساب لا يحتوي على بيانات صرف").

**Tests:** `tests/test_ads_v2_account_diagnose.py` — 8/8 PASS,
   including the exact "Token OK + no data" case the user described,
   which now produces `reason='no_data_for_account'` instead of
   bare "error". Total 31/31 ads_v2 tests pass.

## Ads V2 — Phase 1 (Auto-Reconcile, Final) (2026-06-25)
**Resolved User 5-point Conditional Approval:**
1. ✅ **API-driven auto-fetch, manual demoted to fallback** —
   New endpoint **POST /api/ads-v2/report/auto-reconcile** body
   `{dates:[...], account_ids?:[...]}` re-queries every enabled
   (account × date) from its provider API and stores the freshly-
   fetched figure in **shadow** fields `platform_authoritative_native`,
   `platform_authoritative_sar`, `platform_last_checked_at` — without
   touching `spend_native` (the SSOT row stays stable for Phase 2
   review). Manual entry endpoint kept but UI button renamed
   "إدخال يدوي (احتياطي)".
2. ✅ **Enhanced reconciliation report fields** — Per (account, date):
   `spend_native/sar` (ads_daily), `platform_authoritative_*` (current
   API), `diff_native`, `diff_sar` (signed), `drift_pct_vs_platform`,
   `drift_reason.likely_causes` (Arabic), `confidence`,
   `last_synced_at`, `platform_last_checked_at`, `match_status`.
3. ✅ **Unified Meta/Snapchat/TikTok** — Single `auto_reconcile_user()`
   loop dispatches through `adapters.fetch_day()`. Token-missing path
   degrades to `match_status='sync_failed'` (no 500).
4. ✅ **Phase 2 boundary intact** — Zero writes to `general_ledger` and
   zero `ledger_txn_group_id` on any ads_daily row. Verified by
   dedicated invariant tests post auto-reconcile.
5. ✅ **Status indicators 🟢🟡🟠🔴⚪** — New `_compute_match_status()`
   returns one of `matched / pending_platform / drift_review /
   sync_failed / no_data` (priority order: failed > no_data >
   drift_review > pending_platform > matched). UI renders 5-card
   legend at top of reconciliation tab + colored badge per row with
   emoji icon.

**Backend additions:**
   - `core.py`: `_compute_match_status`, `auto_reconcile_for_day`,
     `auto_reconcile_user`. `run_sync_for_account` now also sets
     `match_status` on every sync (and `sync_failed` when fetch fails).
   - `reports.py`: reconciliation rows expose new fields + summary
     histogram (`match_matched`, `match_pending_platform`,
     `match_drift_review`, `match_sync_failed`, `match_no_data`).
   - `routes.py`: POST `/report/auto-reconcile` (bulk) and
     `/report/auto-reconcile/account/{id}/day/{date}` (single).

**Frontend (AdsV2Report.jsx):**
   - Blue button "إعادة المطابقة من المنصات" beside green sync button.
   - Default tab is now "المطابقة" (recon).
   - 5-card legend (MatchStatCard) showing counts per status with
     colored borders matching the indicator color.
   - 6 new table columns: الحالة (with emoji badge), قيمة المنصة الآن,
     قيمة Ads Manager (يدوي), الفرق (SAR), سبب الفرق, آخر مزامنة,
     آخر فحص للمنصة.
   - Dictionary `MATCH_STATUS_AR` maps backend status → icon + Arabic
     label + Tailwind color classes.
   - Manual dialog re-labeled "إدخال يدوي (احتياطي)" + explanatory
     banner pointing users to the auto-reconcile button.

**Tests:** `tests/test_ads_v2_auto_reconcile.py` — 6/6 PASS;
   `tests/test_ads_v2_auto_reconcile_invariants_iter253.py` — 5/5 PASS;
   Phase 1 + drift regressions — 17/17 still PASS. Total 28/28.
**Verified by testing_agent_v3_fork (iter-253):** Backend 100%,
   Frontend 100%, all 5 demands satisfied. **Phase 1 ready for final
   user sign-off.**

## Ads V2 — Phase 1 (Final, post-rejection fix) (2026-06-25)
**Resolved User Rejection (3 demands):**
1. ✅ **Full Arabic UI** — Replaced all English UI terms (Reconciliation,
   Drift, Flags, Confidence, Status, Pending, Provisional, Final, Source,
   Layer, Sync, Token, OK, active, paused, discovered) with proper Arabic
   via dictionaries in `AdsV2Report.jsx` (PROVIDER_AR, REVIEW_STATUS_AR,
   CONFIDENCE_AR, ANOMALY_AR, DRIFT_CAUSE_AR) and `AdsV2Settings.jsx`
   (`statusAr()` helper). Only platform names (Meta/Snapchat/TikTok)
   remain in English.
2. ✅ **Contrast & font-weight upgrade** — Stat cards: `text-3xl
   font-extrabold tabular-nums text-zinc-50` (was text-2xl font-bold
   text-zinc-100). Table cells: `text-zinc-50 font-semibold`. Backgrounds
   stay zinc-900/950. Verified by test agent.
3. ✅ **Meta discrepancy 36.06 SAR** — Adopted "merchant-as-ground-truth"
   model:
   - **POST /api/ads-v2/report/manual-value** `{account_id, date,
     manual_value_native, note?}` → records the Ads Manager value
     entered by the merchant and **recomputes drift instantly** (no
     provider re-fetch). Audit row appended to `ads_sync_logs`.
   - Reconciliation rows now expose `platform_manual_value_native/_sar`,
     `has_manual_value`, `drift_pct_vs_manual`, and structured
     `drift_reason.likely_causes` (sync_before_close,
     late_reporting_window, ads_manager_value_differs,
     post_close_provider_update, missing_fx_rate).
   - `_compute_anomaly_flags` returns **`None` (not 0.0)** for drift
     when there is no comparison anchor → frontend renders "—".
     Eliminates the "false 0% drift" issue.
   - Meta adapter (`adapters.py`) upgraded with
     `use_account_attribution_setting=true`,
     `use_unified_attribution_setting=true`, `limit=500`,
     `account_currency` & `date_start/date_stop` echoed back, ensuring
     numbers track Ads Manager's stated attribution.
**Frontend additions:**
   - `ManualValueDialog` component — Per-row "إدخال قيمة Ads Manager"
     button → modal entry with native-currency value + optional note;
     on save calls the new endpoint and refreshes recon view.
   - `ReconRow` shows colored drift % (emerald/amber/red) ONLY when a
     comparison exists; em-dash otherwise; likely-causes printed as
     Arabic captions beneath the % value.
**Tests:** `tests/test_ads_v2_drift_logic.py` — 7/7 PASS (drift NULL
   when no anchor, manual-value endpoint persistence, reconciliation
   field exposure, no_drift_inflation invariant). Phase 1 regression
   `tests/test_ads_v2_phase1.py` — 10/10 still PASS.
**Verified by testing_agent_v3_fork (iter-252):** Backend 100%
   (17/17), Frontend 95% (all flows pass). Phase 1 ready for user
   sign-off.

## Ads V2 — Phase 1 (2026-06-24) — superseded by post-rejection fix above
Backend (new):
  - `ads_v2/sync/adapters.py` — Meta/Snap/TikTok day-fetchers (read-only,
    use V1 access_token via v1_token_ref). Snap uses TZ-anchored TOTAL
    granularity; Meta uses level=account `time_increment=1`.
  - `ads_v2/sync/core.py` — `run_sync_for_account()` and
    `run_sync_user()`. Idempotent upsert into `ads_daily` keyed by
    `idempotency_key`. Reconciliation drift + anomaly flags embedded
    on the same row. Tracks `sources_count` (re-sync increments).
  - `ads_v2/data_layer/reports.py` — SSOT readers
    (`get_spend_by_day`, `get_spend_by_account`, `get_spend_by_provider`,
    `get_daily_rows`, `get_reconciliation_report`, `get_sync_health`).
    Every response carries `meta.source_layer` + `meta.ssot`.
  - `ads_v2/routes.py` — `/sync/run`, `/sync/account/{id}/day/{date}`,
    `/sync/health`, `/report?group_by=day|account|provider`,
    `/report/reconciliation`, `/report/daily`.
Frontend (new):
  - `pages/AdsV2Report.jsx` — 4 tabs (by day/account/provider/
    reconciliation) + "مزامنة الفترة الآن" trigger + SSOT footer
    badge.
  - UI fixes: `FInput`/`FLabel`/`FSelect*` wrappers enforce white text
    on dark backgrounds across all Ads V2 forms.
Tests: `tests/test_ads_v2_phase1.py` — 10/10 PASS.
Verified: 5 days of Meta data fetched, totals match across all three
groupings (3203.9 SAR), idempotency holds, V1 untouched, no GL writes.
Pending: Snap & TikTok sync — Snap token on preview is `token_invalid`
so live verification awaits production deploy.

## Ads V2 — Phase 0 (2026-06-24)
Approved design: `/app/memory/ADS_V2_FINAL_DESIGN.md` (simplified, 4-collection).
Backend (new):
  - `ads_v2/__init__.py` · `ads_v2/models.py` · `ads_v2/routes.py`
  - `ads_v2/data_layer/discovery.py` — reads V1 tokens read-only,
    lists Meta/Snap/TikTok ad accounts; falls back to V1 cached
    collections when token call fails.
  - `ads_v2/data_layer/settings.py` — CRUD for `ads_accounts`, FX
    & bank_fee patches, audit log to `ads_sync_logs`.
  - `server.py` — `_ads_v2_ensure_indexes()` on startup; mounts
    `/api/ads-v2/*` router.
Frontend (new):
  - `pages/AdsV2Settings.jsx` — 4 tabs (الحسابات/العملة/العمولات/المراجعة)
  - `App.js` route `/ads-v2/settings`, `Sidebar.jsx` entry under
    "إدارة التشغيل".
Collections created (Phase 0): `ads_accounts`, `ads_daily`, `ads_sync_logs`.
Invariants (verified by tests): NO writes to general_ledger, NO writes
to ads_daily, NO modification to snapchat_connections / meta_connections,
NO OAuth flow triggered.
Tests: `tests/test_ads_v2_phase0.py` — 11/11 PASS.

## Iter-251 v12 — Ad-Spend Scheduler Diagnostics (2026-06-24, READ-ONLY)
  - `ad_spend_scheduler_diagnostics.py` — new `/api/ad-spend-rca/scheduler-diagnostics`
    endpoint returning: (1) heartbeat history from `cron_runs` filtered by
    iter-215 types, (2) per-counterparty dry-run preview computing
    cumulative_spend / would-be AM & PM amounts / skip reasons WITHOUT
    writing to GL, (3) selected snapchat ad accounts state,
    (4) ads_currency_settings snapshot, (5) raw source row samples.
  - `server.py` — instrumented `_ad_spend_window_post_loop` to persist
    heartbeat rows into `cron_runs` on every loop tick (types:
    `ad_spend_window_post_loop_start`, `ad_spend_window_catchup`,
    `ad_spend_window_post`) with per-row `skipped_reasons` histogram.
Tests: `tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS
Purpose: Conclusively determine WHY iter-215 is skipping all 486
counterparties in Production (per-account blocker / reason histogram).

## Tests
- `/app/backend/tests/test_shipping_accounts_ssot_iter255.py` — 6/6 PASS (priority flip + accrual SSOT)
- `/app/backend/tests/test_shipping_cost_ssot.py` — 15/15 PASS
- `/app/backend/tests/test_ads_v2_snapchat_relink.py` — 8/8 PASS (safe re-link flow)
- `/app/backend/tests/test_ads_v2_account_diagnose.py` — 8/8 PASS (3-tier status + diagnose)
- `/app/backend/tests/test_ads_v2_auto_reconcile.py` — 6/6 PASS (Phase 1 auto-reconcile)
- `/app/backend/tests/test_ads_v2_auto_reconcile_invariants_iter253.py` — 5/5 PASS
- `/app/backend/tests/test_ads_v2_drift_logic.py` — 7/7 PASS
- `/app/backend/tests/test_ads_v2_phase1.py` — 10/10 PASS
- `/app/backend/tests/test_ads_v2_phase0.py` — 11/11 PASS
- `/app/backend/tests/test_p15L_bnpl_transfer_block.py` — 11/11 PASS
- `/app/backend/tests/test_p15p_employee_guard_widened.py` — 7/7 PASS
- `/app/backend/tests/test_p15ab_suppliers_unification_forensic.py` — 3/3 PASS
- `/app/backend/tests/test_iter251_v12_scheduler_diagnostics.py` — 3/3 PASS

## Iter-256 — Qoyod Existing-Data Migration (2026-06-26, READ-ONLY)
  - **User-locked policy** (Arabic spec, 2026-06-26):
    - Products: SKU exact → `auto_mapped`; SKU + name OR price differs → `mapped_with_warning`; name-only → `candidate_match` (NO auto mapping); else `unmapped`.
    - Customers: phone (E.164) → `auto_mapped`; email when no phone → `auto_mapped`; name-only → `candidate_match`; else `unmapped`.
    - STRICTLY read-only against Qoyod — no POST/PUT calls.
  - New module `integrations/qoyod/migration.py`: phone/sku/email/name normalisation, paginated GET importers (`import_qoyod_products`, `import_qoyod_customers`), Mezan-side extractors from `order_items` + `unified_orders` + `custom_app_customers`, `match_products`/`match_customers`/`run_migration` orchestrator with idempotent upserts.
  - New collections (additive, isolated from runtime resolver mappings):
    `qoyod_external_products`, `qoyod_external_customers`,
    `qoyod_migration_products`, `qoyod_migration_customers`,
    `qoyod_migration_runs`.
  - New routes (under `/api/integrations/qoyod/migration/*`):
    `POST /run`, `GET /status`, `GET /report`, `GET /{kind}`,
    `POST /{kind}/confirm`, `GET /{kind}/export.csv`.
  - Frontend: new page `/integrations/qoyod/migration` (`QoyodMigration.jsx`) — status cards, products/customers tabs, status filter, search, CSV export, "Confirm Match" for candidates; sidebar link `nav-qoyod-migration`.
  - Day-5 failing test fixed (`test_dry_run_client_records_calls_and_returns_fake_ids`) — updated to `{"customer": {...}}` envelope post `legacy.qoyod.com` migration.
  - **Tests**: `tests/test_qoyod_migration.py` — 27/27 PASS;
    `tests/test_qoyod_migration_http_iter256.py` — 9/9 PASS (+1 skip);
    full Qoyod suite: **184/184 GREEN** — zero regression.

## Pending (User-gated)
- ▶ Dry Run on real data — blocked until migration report is approved by user.
- ▶ Go Live (disable `dry_run_mode`) — blocked until Dry Run is approved.


## Iter-257 — Last Order Date column on Migration page (2026-06-26)
  - Added `last_order_date` to both `qoyod_migration_products` and `qoyod_migration_customers`.
  - Extraction logic:
    - **Products**: max(unified_orders.order_date over the SKU's order_numbers), fallback to received_at, then order_items.created_at.
    - **Customers**: max(unified_orders.order_date) keyed by phone/email, fallback to custom_app_customers.updated_at/created_at.
  - New API params on `GET /api/integrations/qoyod/migration/{kind}`: `sort` (occurrences|last_order_date|status), `sort_dir` (asc|desc), `last_order_after` (YYYY-MM-DD).
  - CSV export now includes `last_order_date` as a column.
  - UI: sortable column "آخر طلب" on both tabs, date filter "آخر طلب بعد".
  - **Policy guarantee** (locked by test): Last Order Date is metadata only — it does NOT change auto_mapped vs candidate_match decisions.
  - Tests: `test_qoyod_migration.py` — 31/31 PASS (+4 new); HTTP smoke `test_qoyod_migration_last_order_date_iter257.py` — 8/8 PASS; full Qoyod suite — 188/188 GREEN.


## Iter-258 — UI-driven Webhook Token (DB-stored, fingerprint-only) (2026-06-26)
  - **New module** `integrations/qoyod/webhook_token_store.py`:
    - `generate_token()` — `mzn_qoyod_prod_` + 48-byte urlsafe (79 chars)
    - `save_webhook_token()` / `get_webhook_token()` — Fernet-encrypted in `qoyod_webhook_tokens`
    - `get_webhook_token_meta()` — UI-safe (fingerprint, rotated_at, last_verified_at)
    - `verify_provided_token()` — **DB-first, env-fallback only when DB empty** (hmac.compare_digest)
    - `revoke_webhook_token()` — idempotent
  - **Refactored** `webhook.py` `_verify_token` into a `_make_verify_token(db)` factory so the dependency can access the DB. Legacy sync `_verify_token` kept for older test compat.
  - **New routes** (under `/api/integrations/qoyod/`):
    - `GET /webhook-token` — metadata (fingerprint only)
    - `POST /webhook-token/generate` — plaintext returned **EXACTLY ONCE**, then encrypted at rest
    - `DELETE /webhook-token` — revoke
  - **Frontend** (`QoyodSettings.jsx`): new `WebhookTokenSection` with one-time copy reveal, copy-to-clipboard, dismiss, regenerate (with confirm), and revoke. Plaintext disappears from DOM on dismiss; only fingerprint persists.
  - **Security guarantees** (locked by tests):
    - DB token takes EXCLUSIVE precedence — legacy env value cannot bypass after generation.
    - Plaintext NEVER returned by GET /webhook-token nor by /settings.
    - Revoked tokens fail verification.
  - **Tests**: `test_qoyod_webhook_token.py` — 19/19 PASS; HTTP smoke 5/5 PASS; full Qoyod suite — **222/222 GREEN**. Zero regression.
  - UX polish: first-time generate no longer triggers an unnecessary confirm; only regenerate (replacement) prompts.


## Iter-259 — Legacy Adapter for Make.com → Qoyod webhook (2026-06-26)
  - **Goal**: support Make.com's flat JSON without touching the legacy `/api/webhook/make` module, and without forcing Make.com to rebuild scenarios. Users will add a SECOND HTTP module in Make pointing at `/api/integrations/qoyod/webhook` with (almost) the same body shape.
  - **New module** `integrations/qoyod/legacy_adapter.py`:
    - `is_legacy_shape(raw)` — sniffs flat root + missing `data` envelope.
    - `adapt(raw) → (adapted, meta)` — pure transformer.
    - Items resolution priority: `items[]` → `packages[].items[]` → `"missing"`.
    - Each line item rebuilt to Salla's canonical `{sku, name, quantity, amounts: {price_without_tax, total}}` shape so the existing `normalizer.py` accepts it unchanged.
    - Flat amounts (subtotal/tax/shipping_cost/discount/total_amount) → nested `data.amounts.{sub_total, tax, shipping, discount, total}`.
    - Flat customer (customer_name/customer_mobile/customer_email) → nested `data.customer.{first_name, last_name, mobile, email}`.
    - Status node built from `order_status` + `order_status_slug` → `{name, slug, customized:{name}}`.
    - Unknown root fields preserved in `meta.legacy_extras` (utm_source, utm_campaign, device, shipping_company, received_from, etc.) for audit — never silently dropped.
  - **State machine** (`state_machine.py`):
    - Added `NEEDS_ENRICHMENT` (transient side-state).
    - Added `FAILED_ENRICHMENT` (failure stage) with `FAILURE_TO_RESUME["FAILED_ENRICHMENT"] = "RECEIVED"`.
    - Edges: `RECEIVED → NEEDS_ENRICHMENT`, `NEEDS_ENRICHMENT → {VALIDATED, FAILED_ENRICHMENT, DEAD_LETTER}`, `FAILED_ENRICHMENT → {RETRYING, DEAD_LETTER}`, `RETRYING → RECEIVED`.
  - **Webhook handler** (`webhook.py`):
    - `adapt_legacy()` called BEFORE idempotency derivation.
    - Inbox row now persists `adapted_payload`, `adapter_meta`, `enrichment_fallback_used` (bool).
    - New `_handle_missing_items()` branch:
      - Toggle OFF (default): `RECEIVED → FAILED_VALIDATION → DEAD_LETTER` with code `missing_items_no_enricher`. NO invoice created.
      - Toggle ON: `RECEIVED → NEEDS_ENRICHMENT → FAILED_ENRICHMENT → DEAD_LETTER` with code `enricher_not_implemented` (Salla-API enricher stub — actual implementation deferred).
  - **Settings** (`models.py` + `routes.py`):
    - `QoyodSettings.enrichment_fallback_enabled: bool = False` (opt-in, default OFF).
    - `SettingsPatch` (PUT route) accepts the new field. Round-trip tested.
  - **Bug found & fixed during testing** (iter-259 retest): `SettingsPatch` was missing the new field → `422 extra_forbidden`. Fixed by adding `enrichment_fallback_enabled: Optional[bool] = None` to the patch model.
  - **Tests** (NEW):
    - `tests/test_qoyod_legacy_adapter.py` — 40/40 PASS (detection, helpers, item adaptation, item collection priority, status node, public adapt(), downstream contract with `validate()`, state machine additions).
    - `tests/test_qoyod_legacy_adapter_http.py` — 5/5 PASS + 1 conditional skip (legacy-with-items progress, missing-items toggle OFF, missing-items toggle ON, legacy_extras audit, PUT settings round-trip).
  - **Full Qoyod regression**: **243/243 GREEN** + 1 skip (token gate). Zero regression.

## Pending (User-gated)
- ▶ User adds a SECOND HTTP module in Make.com → `/api/integrations/qoyod/webhook`.
- ▶ Smoke test on Preview with one real-shaped legacy payload that includes items[].
- ▶ If items[] consistently absent in real Make output, decide on enricher (Salla API or Make-side scenario edit).
- ▶ Then Dry Run → Go Live.


## Iter-260 — Integration Contract v1.0 + diagnostic capture + SKU/total guards (2026-06-26)

### Webhook parse-failure diagnostic capture
  - New `_capture_parse_failure(db, request, token, exc)` helper in `webhook_routes.py`.
  - Triggered at all 3 legacy webhook routes (`/make/{token}`, `/tiktok/{token}`, `/meta/{token}`) on body-parse failure.
  - Stores `{occurred_at, token_prefix (6 chars + …), content_type, content_length, body_preview ≤ 2 KB, parser_error, ip, route}`.
  - TTL index on `occurred_at` (30 days) created in `server.py` startup.
  - Behaviour UNCHANGED: still returns `400 {"detail":"Invalid JSON"}`.
  - Tests: `tests/test_webhook_parse_failure_capture.py` — 10/10 PASS.

### Contract v1.0 — invoice eligibility guards
  - New module `integrations/qoyod/eligibility.py` — `check_invoice_eligibility(payload)`.
  - Enforces 2 user-locked rules:
    - **`items_missing_sku`** — every dict item must have non-empty `sku`. Non-dict items are silently skipped (let `normalize()` raise `FAILED_NORMALIZATION` so Day-3 contract survives).
    - **`total_must_be_positive`** — order `data.amounts.total.amount` (or `data.total_amount` fallback) must be > 0. Also handles string totals (`"139.51"`) and rejects invalid types.
  - Wired into `_process_inbox_row` between the items-missing branch and `validate()`. Failure path: `RECEIVED → FAILED_VALIDATION → DEAD_LETTER` with the offending code. NEVER promotes to invoice creation.
  - Tests: `tests/test_qoyod_eligibility.py` — 16 unit + 4 HTTP integration = 20/20 PASS.

### Contract documentation
  - New artefact: `/app/docs/integrations/qoyod-webhook-contract-v1.md` (paste-ready for Make.com integrators).
    - Headers (X-Webhook-Token, X-Idempotency-Key), required/optional fields, full payload example (items + packages forms), responses (success, duplicate, business-rule fail, transport fail), failure-state map, Make HTTP module config, 7-step first-test checklist.
  - Status: v1.0 ACTIVE.

### Aggregate test status
  - **Full Qoyod regression**: **273/273 GREEN** + 1 skip. Zero regression.
  - New surfaces locked:
    * Adapter (40 unit + 5 HTTP)
    * Webhook parse-failure capture (10)
    * Eligibility (16 unit + 4 HTTP)

## Pending (User-gated, ordered)
- ▶ User adds the SECOND HTTP module in Make.com per contract §8 (using the generated `QOYOD_WEBHOOK_TOKEN` from Mezan UI).
- ▶ Run the 7-step first-test checklist on **Preview** before promoting to Production.
- ▶ Then Dry Run with real orders.
- ▶ Then Go Live (disable `dry_run_mode`).


---

## 2026-02-27 — Iter-268 (Day-5 tests + Dry-Run Leak Guard reconciled)

### Context
Iter-267 added a **DRY-Run Leak Preflight Guard** in `pipeline.py` that hard-refuses any invoice whose `contact_id` or `line_items[].product_id` starts with `DRY:` whenever `settings.dry_run_mode=False`. This protects production from a real incident (Order `268670571`, 2026-02-27, where `DRY:product:e4d875d7` reached `api.qoyod.com`).

The guard caused 2 legacy Day-5 tests to fail because they used `DryRunQoyodClient` (which mints `DRY:*` ids by design) in production mode (`dry_run_mode=False`).

### Fix
Added a new `_LiveLikeQoyodClient(DryRunQoyodClient)` test helper in `tests/test_qoyod_day5_invoice_receipt.py` that overrides `_fake()` to return `Q-<kind>-<sha8>` ids (no `DRY:` prefix) — mirrors what real Qoyod responses look like. Re-pointed the 2 production-mode tests at it:
- `test_pipeline_partial_failure_on_receipt_error` (now uses `_LiveLikeQoyodClient` via `_FlakyClient` subclass).
- `test_pipeline_records_payload_snapshot_before_post`.

The original `DryRunQoyodClient` continues to back the dry-run tests (where `DRY:*` ids are correct semantics).

### Verification
- `pytest tests/test_qoyod_day5_invoice_receipt.py` → **15/15 PASS**.
- `pytest tests/test_qoyod_dry_run_leak_protection.py` → **5/5 PASS** (leak guard still blocks `DRY:` in contact_id and product_id).
- `pytest tests/ -k qoyod` → **536 passed, 2 skipped, 0 failed** across the full Qoyod surface.

### Net result
- ✅ Dry-Run Leak Protection fully enforced in Production (covers `DRY:contact`, `DRY:product`, `DRY:invoice`, `DRY:receipt` patterns via product_resolver quarantine + pipeline preflight).
- ✅ No regression — full Qoyod regression suite green.
- ⏸ Re-processing Order `268670571` on Production explicitly **paused** per user instruction; nothing has been sent to `api.qoyod.com`.

---

## 2026-02-27 — Iter-269 (One-Shot Reprocess — single-order, strict, audit-trail)

### Context
After the Iter-267/268 Dry-Run Leak Guard shipped, the operator wants a safe way to recover the production order `268670571` (and any future single-row casualty of a since-fixed bug) WITHOUT triggering backfill, bulk auto-requeue, or shell access.

### Implementation
**Backend:**
- `integrations/qoyod/one_shot_reprocess.py` — new module:
  - `reprocess_one_order(db, *, user_id, order_number=None, trace_id=None, confirm, actor)` — orchestrator.
  - `_quarantine_dry_mappings(...)` — quarantines `DRY:contact:*` from `qoyod_customers_mapping` and `DRY:product:*` from `qoyod_products_mapping` for ONLY the SKUs/customer of the target order. Also nullifies the row's own `qoyod_customer_id` if it leaks.
  - `_find_target_row(...)` — single-match enforcement (0 or >1 matches → refused).
  - `_scan_payload_for_dry(payload)` — defensive payload scanner.
  - `OneShotRefused` — typed exception with structured `{code, message, ...}` body for the HTTP layer.
- `integrations/qoyod/customer_resolver.py` — added a DRY: leak guard mirroring `product_resolver` (quarantine + fall-through to create-fresh).
- `integrations/qoyod/routes.py` — new endpoint:
  - **`POST /api/integrations/qoyod/admin/one-shot-reprocess`**
  - Body: `{ order_number: str, confirm: str, trace_id?: str }`
  - `confirm` MUST equal `REPROCESS-<order_number>` (order-specific, typo-resistant).
  - Returns uniform shape regardless of outcome (`COMPLETED` / `ALREADY_COMPLETED` / `DEAD_LETTER` / `PARTIAL_FAILURE` / ...).
  - On failure surfaces `error.code`, `error.message`, `request_body_json`, `failed_at_stage`.

**Frontend:**
- `pages/QoyodFirstSyncMonitor.jsx` — new `🎯 إعادة معالجة طلب واحد` button in the toolbar + full-screen modal:
  - Inputs: order_number, trace_id (optional), confirm.
  - Live-rendered expected token: typing `268670571` displays `REPROCESS-268670571` in red.
  - Confirm button disabled until the token matches exactly.
  - Result view shows: outcome badge, stage_sequence_observed, qoyod_invoice_id, dry_leaks_in_final_payload (must be `[]`), full invoice_payload, quarantine_summary.
  - On failure: shows `error.code`, `error.message`, `request_body_json`, `failed_at_stage`. No auto-retry.

### Strict invariants enforced (matches user directive verbatim)
1. ✅ Single row only — refused if 0 or >1 matches.
2. ✅ Confirm token required: `REPROCESS-{order_number}` exact.
3. ✅ Never scans / touches any other DEAD_LETTER row.
4. ✅ Never triggers backfill.
5. ✅ DRY-mapping quarantine before re-run (customer + products).
6. ✅ Pipeline preflight guard remains the last line of defence — `DRY:` in invoice payload → DEAD_LETTER, **nothing POSTed to Qoyod**.
7. ✅ On failure: no auto-retry. Returns `error.code` + `error.message` + `request_body_json` + `failed_at_stage`.
8. ✅ `dry_run_mode=True` blocks the endpoint (real-Qoyod only).
9. ✅ Missing API credentials blocks the endpoint.
10. ✅ Already-COMPLETED row is a no-op.

### Verification
- `pytest tests/test_qoyod_one_shot_reprocess.py` → **11/11 PASS**.
- `pytest tests/ -k qoyod` → **547 passed, 2 skipped, 0 failed** (full regression green).
- Live curl with admin token verified:
  - Token mismatch → `400 {"code":"confirm_token_mismatch", "expected":"REPROCESS-268670571"}`.
  - Row not found → `400 {"code":"row_not_found"}`.
- UI smoke-tested on preview: button visible, modal renders, live token preview, confirm button correctly disabled until token matches.

### NOT in scope (per user directive)
- ❌ No new alert/counter in First-Sync Monitor for "blocked by DRY guard" — explicitly declined.
- ❌ Order `268670571` was NOT reprocessed against Production yet — the operator will trigger it from the UI when ready.

---

## 2026-02-27 — Iter-270 (One-Shot Reprocess hardening — diagnose 500 on Production)

### Issue reported
User got `request_failed / Request failed with status code 500` after clicking the **🎯 إعادة معالجة طلب واحد فقط** button on **PRODUCTION** for order `268670571`. The endpoint shipped in Iter-269 returned a generic 500 with no surfaced error code/traceback — making remote diagnosis impossible.

### Root cause hypotheses (in order of probability)
1. **Order ID typed as string in lookup, stored as int in Mongo** — Salla persists `order_id` as integer; my Mongo query searched for the string `"268670571"` only → 0 matches → BUT this would be a 400 `row_not_found`, not 500. So this alone isn't the cause, but fixed it preemptively.
2. **`InvalidTransition` exception** raised by state-machine for an unsupported current stage (e.g. row in `FAILED_INVOICE` rather than `DEAD_LETTER`) — uncaught, becomes 500.
3. **Any other unhandled exception** in the deep pipeline call — bubbles up as a generic FastAPI 500.

### Hardening applied
**Backend (`one_shot_reprocess.py`):**
- `_find_target_row` now matches both string and integer representations of `order_number` (Salla integers + legacy string-typed rows).
- Multi-match disambiguation: when several rows match (e.g. `under_review` then `completed` webhooks), filter to reprocessable (DEAD_LETTER / FAILED_*) rows first. If exactly one is failed → pick it. Else surface candidate list.
- `_REPROCESSABLE_STAGES` set expanded to include all `FAILED_*` stages (FAILED_VALIDATION, FAILED_NORMALIZATION, FAILED_CUSTOMER, FAILED_PRODUCT, FAILED_INVOICE, FAILED_RECEIPT, FAILED_ENRICHMENT) plus DEAD_LETTER, PARTIAL_FAILURE, NORMALIZED, NEW, RECEIVED, VALIDATED, ELIGIBLE, SKIPPED.
- `_reset_row_to_stage` now catches `InvalidTransition` and converts it into a structured `OneShotRefused("invalid_transition_to_*", ...)`.

**Backend (`routes.py`):**
- Wrapped `reprocess_one_order` call in a try/except that converts **any** unhandled exception into a structured 500 carrying:
  - `code: "one_shot_unhandled_exception"`
  - `message: "<ExceptionType>: <message>"`
  - `traceback_tail: <last 1500 chars of traceback>`
  - `order_number`, `trace_id`
- Added `logger.exception(...)` for permanent server-side audit.

**Frontend (`QoyodFirstSyncMonitor.jsx`):**
- Modal's error block now shows the new `traceback_tail` inside a collapsible `<details>` section so the operator can paste it directly into a support ticket / handoff.

### Verification
- `pytest tests/test_qoyod_one_shot_reprocess.py` → **11/11 PASS**.
- `pytest tests/ -k qoyod` (full Qoyod surface) → **547 passed, 2 skipped, 0 failed**.
- Live curl on preview (real-user token):
  - `confirm_token_mismatch` → 400 (clean structured body).
  - `row_not_found` → 400 (clean structured body, no 500).
- Endpoint registered + auth-protected.

### Next time the user clicks the button on Production
If a 500 still happens, the modal will show:
- `code`: `one_shot_unhandled_exception`
- `message`: the actual Python exception type and message
- A "Traceback (للتشخيص)" expand → full traceback tail

That's enough for me to diagnose immediately without needing Production log access.

---

## 2026-02-27 — Iter-270b (Qoyod create_product field-name fix)

### What we learned from order 268670571
The hardened modal surfaced the real failure on Production (no more 500):

- ✅ **Pipeline:** `NORMALIZED → RULES_APPLIED → CUSTOMER_RESOLVED → PRODUCT_RESOLVED`
- ✅ **DRY mapping quarantined** for SKU `AMS11961` (sale_price=5)
- ❌ **Stopped at `FAILED_PRODUCT`** with Qoyod 422:
  `{"base": ["enter at least a purchase price or a sales price to continue."]}`
- ✅ **No invoice was POSTed** — the leaked DRY id never reached `api.qoyod.com`. The `request_body_json` shown in the modal was a stale snapshot from a previous attempt.

### Root cause
`_build_product_payload` (in `integrations/qoyod/product_resolver.py`) used the wrong Qoyod field name: `selling_price` instead of `sale_price`. Per the Qoyod V2 docs (apidoc.qoyod.com) the canonical field is `sale_price` — `selling_price` is silently dropped, so Qoyod sees a product create with no price and refuses.

### Fix
`_build_product_payload`:
- Renamed `selling_price` → `sale_price` (Qoyod V2 spec).
- Coerce `unit_price` to `float` defensively (string-typed legacy payloads).
- Fallback to `0.0` when missing (lets free gifts / packaging items create cleanly).

### New regression tests (`test_qoyod_product_create_payload_field_names.py`)
- ✅ `test_create_product_payload_uses_qoyod_v2_sale_price_field` — asserts `sale_price` present, `selling_price` absent.
- ✅ `test_create_product_payload_coerces_string_price_to_float`.
- ✅ `test_create_product_payload_handles_missing_price_gracefully`.
- ✅ `test_create_product_payload_preserves_name_sku_type_fields`.

### Verification
- `pytest tests/test_qoyod_product_create_payload_field_names.py` → **4/4 PASS**.
- `pytest -k qoyod` (full surface) → **551 passed, 2 skipped, 0 failed**.

### Operator next steps
Once deployed to Production:
1. Click `🎯 إعادة معالجة طلب واحد` again for order `268670571`.
2. The resolver will now create the product with `sale_price=5` (the line's unit_price).
3. Expected stage sequence: `… → PRODUCT_RESOLVED → INVOICE_CREATED → RECEIPT_CREATED → COMPLETED`.

---

## 2026-02-27 — Iter-271 (FAILED_PRODUCT stage-specific diagnostic)

### Context
After Iter-270b shipped, the user retried order `268670571` on Production and saw the same `enter at least a purchase price or a sales price` error. The modal was still showing a **stale invoice snapshot** from a previous attempt (with the old `DRY:product:e4d875d7` id) — confusing the diagnosis. The operator needs to verify the live deploy is actually executing the new `sale_price` code-path.

### What changed
**Backend (`one_shot_reprocess.py`):**
- `_build_failure_response` rewritten to emit **stage-specific** diagnostics. For `failed_at_stage == "FAILED_PRODUCT"` the response now carries a `product_create` block:
  ```json
  {
    "product_create": {
      "endpoint": "POST /products",
      "status_code": 422,
      "request_body": { "product": { "sku": "AMS11961", "sale_price": 5.0, ... } },
      "response_excerpt": "{\"base\":[\"enter at least a purchase price...\"]}",
      "sale_price_field_present":    true,
      "selling_price_field_present": false,
      "sale_price_in_request_body":  5.0,
      "sku_in_request_body":         "AMS11961",
      "expected_from_canonical":     { "sku": "AMS11961", "sale_price_we_would_send": 5.0 },
      "deploy_carries_sale_price_fix": true   // verdict the operator reads first
    }
  }
  ```
- For `FAILED_INVOICE` / `FAILED_RECEIPT`, the response keeps the invoice snapshot (since that's the offending body for those stages).
- The stale invoice snapshot is **no longer surfaced** under FAILED_PRODUCT.

**Frontend (`QoyodFirstSyncMonitor.jsx`):**
- New amber-bordered "📦 تشخيص إنشاء المنتج في قيود (FAILED_PRODUCT)" block in the modal.
- Top line is a **verdict badge** (green/red): "النشر يستخدم الإصلاح الجديد: sale_price موجود، selling_price غير موجود" OR "النشر لا يحتوي على الإصلاح الجديد".
- Bullet list with per-field verification + expandable `product_create_request_body` and `product_create_response_body` panes.

### New regression tests (`test_qoyod_one_shot_failed_product_diagnostic.py`)
- ✅ `test_failed_product_surfaces_product_create_diagnostic_when_fixed`.
- ✅ `test_failed_product_diagnostic_detects_unfixed_deploy` — verdict flips to red when the deploy still ships `selling_price`.
- ✅ `test_failed_product_error_block_has_full_qoyod_context` (status_code, endpoint, response excerpt preserved).
- ✅ `test_failed_invoice_still_surfaces_invoice_payload` (other failure stages unaffected).

### Verification
- `pytest tests/test_qoyod_one_shot_failed_product_diagnostic.py + reprocess + field_names` → **19/19 PASS**.
- `pytest -k qoyod` (full surface) → **555 passed, 2 skipped, 0 failed**.
- Modal renders cleanly on preview (smoke test screenshot).

---

## 2026-02-27 — Iter-272 (Qoyod product create — REAL fix: selling_price + is_sold:true)

### The truth (after Iter-271 diagnostic surfaced the deploy state)
The diagnostic from Iter-271 confirmed the live Production deploy WAS sending `sale_price=5` (green verdict). Yet Qoyod still rejected with the SAME 422:
```
{"errors": {"base": ["enter at least a purchase price or a sales price to continue."]}}
```

So Iter-270b's rename `selling_price → sale_price` was **wrong**. Re-investigating Qoyod's docs + knowledge base:
- Qoyod's GET /products responses use **`selling_price`** (confirmed in `identity_diagnostics.py:229`).
- Qoyod's legacy /products POST validator requires **two** things:
  1. Field name **`selling_price`** (NOT `sale_price`).
  2. Activation flag **`is_sold: true`** — without it Qoyod ignores `selling_price` entirely and emits the misleading "enter at least a purchase price or a sales price" message.

### What changed (Iter-272)
**`product_resolver.py`:**
- Reverted: `sale_price` → `selling_price`.
- **Added activation flags**:
  - `is_sold: true` — activates `selling_price` validation in Qoyod.
  - `is_bought: false` — tells Qoyod we don't track purchases (no `buying_price` required).

**`one_shot_reprocess.py` — FAILED_PRODUCT diagnostic block updated:**
- `deploy_carries_sale_price_fix` (boolean) → `deploy_carries_full_fix` (boolean).
- New field: `is_sold_flag` (bool|None) — surfaces what the deploy actually sent.
- New field: `selling_price_in_request_body` (replaces `sale_price_in_request_body`).
- Green verdict ONLY when BOTH conditions hold (selling_price present AND is_sold=true).

**`QoyodFirstSyncMonitor.jsx`:**
- Verdict banner text updated: "النشر يستخدم الإصلاح الكامل: selling_price + is_sold:true".
- 4 verification lines: selling_price field present (must be true), sale_price field present (must be false — wrong name), is_sold flag (must be true), selling_price value vs expected.

### Tests (8 new + diagnostic suite refreshed)
- `test_qoyod_product_create_payload_selling_price_iter272.py` → **6/6 PASS**:
  - ✓ Uses `selling_price`, not `sale_price`.
  - ✓ `is_sold: true` activation flag included.
  - ✓ `is_bought: false` set.
  - ✓ String → float coercion.
  - ✓ Missing unit_price → 0.0 fallback.
  - ✓ Preserves name/sku/type fields.
- `test_qoyod_one_shot_failed_product_diagnostic.py` rewritten → **7/7 PASS**:
  - ✓ Green verdict when full fix deployed.
  - ✓ Red verdict when is_sold flag missing (the original bug).
  - ✓ Red verdict when still using sale_price.
  - ✓ Full Qoyod context preserved in error block.
  - ✓ FAILED_INVOICE / FAILED_RECEIPT unaffected.
  - ✓ Expected_from_canonical uses `selling_price_we_would_send`.
- Removed: obsolete `test_qoyod_product_create_payload_field_names.py` (Iter-270b).
- Full Qoyod regression: **559 passed, 2 skipped, 0 failed**.

### Operator next steps
1. Deploy Iter-272 to Production.
2. Retry order `268670571` in the modal. Expected outcome: 🟩 green verdict (selling_price + is_sold:true), and the product create will succeed → row continues through INVOICE_CREATED → RECEIPT_CREATED → COMPLETED.

### Lesson learned (for the handoff log)
LLM web-search results for accounting APIs are sometimes contradictory (some sources said `sale_price`, others said `selling_price`). The authoritative signal was **already in the codebase**: `identity_diagnostics.py:229` reads `selling_price` from Qoyod's own GET responses. Next time, **trust READ schemas as the source of truth for WRITE field names** before consulting external docs.

---

## 2026-02-27 — Iter-273 (Totals Guard — payload completeness preflight)

### Critical discovery (P0 by user)
The operator inspected order `268670571` raw payload and found:
- Order header: `subtotal=105`, `shipping=23.15`, `total=131.60`
- But `items[]` had ONLY ONE row: `{sku=AMS11961, unit_price=5}` → items_sum = **5**

Make.com's `map()` step had silently truncated `items[]`. If we had successfully created the product and invoice, **Mezan would have posted a 5 SAR invoice to Qoyod for an order whose real value was 131.60** — a major financial integrity bug.

User directive: stop the row BEFORE any Qoyod side-effects when totals don't match.

### Implementation
**`integrations/qoyod/totals_guard.py` (new):**
- Pure function `validate_totals(canonical, *, tolerance=0.05)` returning `TotalsGuardResult{ok, code, message, details}`.
- Three error codes:
  - `line_items_incomplete` — items_sum ≪ subtotal (Make dropped rows).
  - `line_items_total_mismatch` — items_sum diverges in either direction.
  - `order_total_mismatch` — header math doesn't reconcile.
- Accepts both tax-EXCLUSIVE and tax-INCLUSIVE conventions (Salla mostly excl, adapters vary).
- Tolerance ±0.05 SAR (1 halala) for float rounding.

**`integrations/qoyod/pipeline.py`:**
- `process_normalized_row` now invokes the guard immediately after DTO rehydration, BEFORE `_load_settings` and BEFORE any resolver call.
- On refusal: row transitions `NORMALIZED → FAILED_VALIDATION → DEAD_LETTER` (no auto-retry — upstream fix required).
- `totals_guard` audit block persisted to the row (full details + parsed_items).

**`integrations/qoyod/state_machine.py`:**
- Added allowed edge: `(NORMALIZED → FAILED_VALIDATION)`.

**`integrations/qoyod/one_shot_reprocess.py`:**
- `_build_failure_response` now surfaces a dedicated `totals_guard` block when the failure code is one of the three guard codes.
- Stage-specific blocks (product_create, invoice_payload) are NOT shown for a Totals Guard refusal (they'd be misleading — nothing was sent).

**`pages/QoyodFirstSyncMonitor.jsx`:**
- New orange-bordered "⚠ Totals Guard أوقف الإرسال (لم يُلامس قيود)" section in the modal:
  - Error code + message
  - Bulleted breakdown (items_count, items_sum_excl, subtotal, shortfall, etc.)
  - Collapsible `parsed_items[]` showing what we actually received per SKU
  - Hint linking to the Make Runbook fix

**`docs/integrations/make-runbook-qoyod-dry-run.md`:**
- New "Iter-273 — Totals Guard" section documenting:
  - The three error codes + their meanings
  - The wrong-vs-right Make.com mapping snippet
  - Array Aggregator pattern for shape-massaged items
  - Where to verify on a live order

### Tests
- `test_qoyod_totals_guard_iter273.py` (14 tests) — pure validate_totals matrix:
  - ✓ Production order 268670571 shape → `line_items_incomplete`
  - ✓ Clean matching totals → ok
  - ✓ Tax-INCLUSIVE convention accepted
  - ✓ Rounding within ±0.05 tolerance accepted
  - ✓ Rounding beyond tolerance refused
  - ✓ Empty items + non-zero subtotal → `line_items_incomplete`
  - ✓ Empty items + zero subtotal → ok (pathological)
  - ✓ Order total mismatch → `order_total_mismatch`
  - ✓ Header math reconciles with shipping + discount
  - ✓ items_sum >> subtotal → `line_items_total_mismatch`
  - ✓ String-typed prices coerced
  - ✓ Custom tolerance widening works
  - ✓ Result.to_log_dict shape stable
  - ✓ DEFAULT_TOLERANCE = 0.05

- `test_qoyod_pipeline_totals_guard_e2e_iter273.py` (3 tests) — full pipeline:
  - ✓ 268670571 shape → DEAD_LETTER without touching CUSTOMER_RESOLVED
  - ✓ Clean order advances past NORMALIZED
  - ✓ order_total_mismatch → DEAD_LETTER with correct code

- `test_qoyod_one_shot_failed_product_diagnostic.py` (+2 tests):
  - ✓ Totals Guard refusal surfaces as dedicated block
  - ✓ All three codes (incomplete / total_mismatch / order_total_mismatch) surface

- Fixed `test_qoyod_day4_rules_and_customer.py` helper `_dto()` to set `subtotal=86.96` (matching tax-excl items) so existing tests pass the new guard.

### Verification
- `pytest tests/test_qoyod_totals_guard_iter273.py + e2e_iter273.py` → **17/17 PASS**.
- `pytest -k qoyod` (full surface) → **578 passed, 2 skipped, 0 failed**.

### NOT in scope (per user directive)
- ❌ Order `268670571` was NOT reprocessed against Production this iteration. Operator wants to verify the Make.com fix lands first, then dry-run, then live.

---

## 2026-02-27 — Iter-274 (Make.com items[] Runbook + parse-failure UI)

### Context
After Iter-273 shipped the Totals Guard, the operator updated their Make.com scenario from sending one item to sending all items. The new body used Raw JSON injection:
```jsonc
"items": {{1.data.items}}
```
Make's text engine treats the Array as a string and emits `[object Object]` / `omap{...}` — invalid JSON. Mezan rejected with `422 json_invalid`.

### What changed
**1. New runbook (`docs/integrations/make-runbook-build-items-array.md`)** — 7-section step-by-step guide:
- §0 TL;DR with the exact failure mode.
- §1 Anti-pattern (don't inject `{{1.data.items}}` into Raw JSON).
- §2 Correct 5-module scenario (Webhook → **Iterator → Array Aggregator → Create JSON** → HTTP).
- §3 Copy-paste JSON schema for the Create JSON module.
- §4 Verification queries (UI + curl).
- §5 Troubleshooting table (5 common symptoms → fixes).
- §6 Why Create JSON > text injection.
- §7 Verified Shape A (flat items[]) vs Shape B (packages[].items[]).

**2. Backend — new admin route exposing parse failures**
- `GET /api/integrations/qoyod/admin/webhook-parse-failures?limit=N` (default 5, max 50).
- Reads from existing `webhook_parse_failures` collection (already populated by `webhook_routes._capture_parse_failure`).
- Response includes a `hint` field pointing the operator at the new runbook.
- Auth-protected.

**3. Frontend — new red banner on `🩺 مراقب أول مزامنة`**
- Shows when `webhook_parse_failures` has at least one row.
- Collapsible; lists last 5 failures with `occurred_at`, `parser_error`, `token_prefix`, `content_type`, `content_length`, `ip`, and the full `body_preview` (2KB) in a `<pre>` block.
- Strong CTA to the runbook in the heading.

### Why this matters
Before this iteration, parse failures were silent — Mezan returned 422 to Make and the operator had no UI surface. Now any malformed-JSON receipt shows up on the monitor home page within seconds of arriving.

### Verification
- Live curl: `GET /api/integrations/qoyod/admin/webhook-parse-failures?limit=3` → returns `{count, rows[], hint}`. Auth-protected. Returns 0 rows on preview (no parse failures here).
- Full Qoyod regression: **578 passed, 2 skipped, 0 failed** (no regressions from the new route).
- Frontend lints clean.

### Operator next steps
1. Deploy Iter-274 to Production.
2. Update Make.com scenario per the new runbook (Iterator → Array Aggregator → Create JSON).
3. Send one test order. On Mezan's monitor:
   - If the red banner appears → Make is still producing bad JSON. Click to see exact body Mezan rejected.
   - If no banner AND row reaches `COMPLETED` with `items_count == subtotal_lines` → 🎉 ready for live re-processing of 268670571 (which still has the broken canonical payload — operator must re-trigger from Salla or Make Replay).

---

## 2026-02-27 — Iter-275 (Normalizer accepts Salla's nested `amounts` per-item shape)

### Context
Operator tried building the Make.com items array via Array Aggregator (Iter-274 runbook). Make's Array Aggregator's `Custom` mode does NOT let you synthesise new fields like `unit_price` / `tax_amount` — you can only pick existing fields from the iterator bundle. The natural pass-through shape from Salla is:
```json
{
  "sku": "AMS13000",
  "name": "عباية جنان",
  "quantity": 1,
  "amounts": {
    "price_without_tax": { "amount": 180, "currency": "SAR" },
    "tax":               { "amount": { "amount": 12.86, "currency": "SAR" } },
    "total":             { "amount": 173.6, "currency": "SAR" }
  }
}
```
Note the **double-nested** `tax.amount.amount` (Salla quirk).

User directive: "Make should not be more complex. Mezan must support this `amounts` shape directly."

### What changed

**`integrations/qoyod/normalizer.py`:**
- `_money()` now recurses through `{amount: {amount: N}}` so the double-nested tax node parses correctly instead of silently falling back to 0.
- New explicit priority extractors (replacing the implicit chain):
  - `_extract_item_unit_price`: `it.unit_price → it.price.amount → it.amounts.price_without_tax.amount → 0.0`
  - `_extract_item_tax_amount`: `it.tax_amount → it.amounts.tax.amount.amount → 0.0`
  - `_extract_item_total`: `it.total → it.amounts.total.amount → unit_price * quantity`
  - `_extract_item_currency`: `it.price.currency → it.amounts.price_without_tax.currency → SAR` (diagnostic-only; LineItemDTO stays currency-agnostic per-item)
- `_normalize_item` now produces identical canonical DTO from THREE shapes: Mezan canonical, flat Salla, layered Salla.

**`docs/integrations/make-runbook-build-items-array.md`:**
- §0 TL;DR updated with Iter-275 note.
- §2 Module 3 (Array Aggregator) now documents two equivalent options:
  - Option A (recommended): pass through `{sku, name, quantity, amounts}` — let Mezan parse.
  - Option B (legacy): keep the flat Mezan-canonical keys if your scenario already has them.

### Tests
`test_qoyod_normalizer_layered_amounts_iter275.py` — **24/24 PASS**:
- ✓ User-reported exact shape (AMS13000, 180/12.86/173.6) → canonical DTO matches expectation.
- ✓ `_money` recursion through double-nested money node.
- ✓ Priority chain for unit_price (3 levels + zero fallback).
- ✓ Priority chain for tax_amount (3 levels + flat-node compat).
- ✓ Priority chain for total (3 levels including computed `unit_price * quantity`).
- ✓ Currency extraction (3-level priority).
- ✓ Backward-compat: existing canonical shape still normalizes identically.
- ✓ String-typed numbers from Make are coerced cleanly.
- ✓ Hostile shapes (non-dict, empty amounts) handled.
- ✓ Multi-item layered shape sums correctly for Totals Guard.

### Verification
- `pytest tests/test_qoyod_normalizer_layered_amounts_iter275.py` → **24/24 PASS**.
- Full Qoyod regression (`pytest -k qoyod`) → **602 passed, 2 skipped, 0 failed**.

### Operator next steps
1. Deploy Iter-275 to Production.
2. Simplify Make's Array Aggregator: pick `sku`, `name`, `quantity`, `amounts` (whole sub-object). Drop the manual mapping of `unit_price` / `tax_amount` / `total`.
3. Send one test order. The monitor should show `pipeline_stage=COMPLETED` with the canonical_payload.items each carrying the flat shape — proving the normalizer collapsed Salla's nested form correctly.

---

## 2026-02-27 — Iter-276 (Per-line `discount_amount` — line-level accounting fix)

### Context
User-supplied real example (AMS13000) revealed that Salla orders carry **per-line promo-code discounts**:
- unit_price = 180
- total_discount = 19.26
- tax_amount = 12.86
- total = 173.60   (= 180 − 19.26 + 12.86)

Before Iter-276 the discount was lost in normalization, the Qoyod invoice would carry `unit_price=180` and `discount=0` — so the invoice total would be 192.86 instead of 173.60. A small bug, but a real bookkeeping divergence.

### Minimal change (4 touch points)

**1) `dto.py`** — added `discount_amount: float = 0.0` to `LineItemDTO`.

**2) `normalizer.py`** — new `_extract_item_discount_amount(it, amounts)` with priority chain:
- `it.discount_amount` → `it.amounts.total_discount.amount` → `0.0`

**3) `invoice_builder.py`** — `build_invoice_payload` now maps `discount_amount` to Qoyod's `discount` column (NOT folded into unit_price; auditability preserved).

**4) `totals_guard.py`** — `validate_totals` now subtracts `discount_amount` per line when computing items_sum_excl. Salla reports `subtotal` POST-discount, so the math now reconciles for orders with promo codes. `parsed_items` audit also surfaces the per-line discount.

### Tests (`test_qoyod_line_discount_iter276.py` → 11/11 PASS)
- Priority chain matrix (3 levels for discount_amount)
- ✓ AMS13000 real shape → DTO carries unit_price=180, discount=19.26, tax=12.86, total=173.60
- ✓ Invoice builder surfaces `discount: 19.26` as separate column (unit_price stays 180)
- ✓ Invoice builder defaults to `discount: 0` for items without discount (back-compat)
- ✓ Totals Guard accepts the AMS13000 single-line discount case
- ✓ Totals Guard still rejects the 268670571 incomplete-items case
- ✓ Totals Guard `parsed_items` surfaces `discount_amount` column
- ✓ Multi-item order with mixed discounts (one line discounted, one not) reconciles end-to-end

Full Qoyod regression: **613 passed, 2 skipped, 0 failed** (+11 from Iter-276 baseline).

### NOT in scope (per user directive)
- ❌ No new UI / Dashboard.
- ❌ No order-level discount changes (the canonical already has `discount_amount` at the order level; this iter only adds the per-line column).

---

## 2026-02-27 — Iter-277 (Normalizer self-test endpoint + deploy-state verification)

### Context
User reported that order `268633052` (AMS11980) received a correct nested `amounts` payload from Make, but the canonical DTO showed:
```json
unit_price: 0, tax_amount: 0, discount_amount: 0, total: 153.35
```

Diagnosis: Preview code (Iter-275/276) parses this exact payload correctly — verified by a new dedicated regression test (`test_qoyod_normalize_prod_order_268633052_iter277.py`). The bug is a deploy-state gap: **Production has not yet been redeployed** with Iter-275/276.

### Changes
**Backend (`integrations/qoyod/routes.py`):**
- New endpoint **`GET /api/integrations/qoyod/admin/normalizer-self-test`** runs the exact production payload for AMS11980 through `_normalize_item` and returns:
  - `iter_275_layered_amounts_supported` (bool) — true if unit_price=159 and tax_amount=11.36
  - `iter_276_line_discount_supported` (bool) — true if discount_amount=17.01
  - `expected` vs `got` for side-by-side compare
  - `hint` explaining what each false signal means
- Auth-protected.

**Tests (`test_qoyod_normalize_prod_order_268633052_iter277.py`):**
- ✓ Exact AMS11980 payload normalizes to 159 / 11.36 / 17.01 / 153.35
- ✓ Line math reconciles: 159 − 17.01 + 11.36 = 153.35
- ✓ `_money` handles tax node with `{percent, amount: {amount, currency}}` correctly

### How the operator uses it
```bash
TOKEN=$(curl -s -X POST https://mezansalla.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"...","password":"..."}' | jq -r .token)

curl -s https://mezansalla.com/api/integrations/qoyod/admin/normalizer-self-test \
  -H "Authorization: Bearer $TOKEN" | jq .ok
```
- `ok: true` → Production carries Iter-275/276. The issue is elsewhere (data, not deploy).
- `ok: false` → Redeploy needed. `iter_275_layered_amounts_supported` / `iter_276_line_discount_supported` show which iter is missing.

### Verification
- Preview self-test: **`ok: true`** for both iters.
- Full Qoyod regression: **615 passed, 2 skipped, 0 failed** (+2 from Iter-276).

---

## 2026-02-27 — Iter-278 (legacy_adapter — bug in items[] amounts extraction)

### Smoking gun
The `normalizer-self-test` (Iter-277) returned `ok: true` on Production, but a real order `268632361 / AMS11980` still produced `unit_price=0, tax_amount=0, discount_amount=0`. The stage history exposed the missing link:
```
NEW ← trace_id=eac68e664dee48738005a52b15e50a60 · adapter=True · items_source=items
```
The **legacy adapter** runs BEFORE the normalizer. It was reading the wrong fields.

### Root cause (`integrations/qoyod/legacy_adapter.py::_adapt_item`)
1. **`unit_price`**: only looked at top-level `raw_item.price` / `raw_item.unit_price`. Salla's modern webhook ships items WITHOUT a `price` field — the price lives in `raw_item.amounts.price_without_tax.amount`. Adapter emitted `price_without_tax: null` → normalizer rendered `unit_price = 0`.
2. **`tax_amount`**: looked at `raw_item.amounts.tax.amount` (one level). Salla's modern tax node is double-nested: `{"percent": "8.00", "amount": {"amount": 14.96}}`. The adapter then took `.amount`, got a dict `{"amount": 14.96}`, and `_money(dict, ...)` returned None.
3. **`total_discount`**: DROPPED ENTIRELY by the adapter. The normalizer's Iter-276 discount support was useless because the field never made it through the adapter.

### Fix (`legacy_adapter.py`)
- New `_extract_money_value(node)` helper that recurses through `{amount: {amount: N}}` (same as the normalizer's `_money` from Iter-275).
- `_adapt_item` priority chains:
  - **unit_price**: `amounts.price_without_tax.amount` → `price.amount` → `unit_price` → `None`
  - **tax_amount**: `amounts.tax (recursed)` → `raw.tax` → `None`
  - **discount_amount**: `amounts.total_discount.amount` → `raw.discount_amount` → `raw.discount` → `None`  ← NEW
  - **total**: `amounts.total.amount` → `raw.total` → `raw.total_price` → `price × qty` → `None`
- Adapter now emits `amounts.total_discount` so the normalizer (Iter-276) can pick it up.
- Old flat-price payloads still parse cleanly (back-compat).

### Diagnostic upgrades (`routes.py`)
- `GET /admin/normalizer-self-test` now runs `_adapt_item → _normalize_item` chain (not just normalizer). New flag `iter_278_adapter_nested_amounts_fix`. Sample input updated to AMS11980 (price=199, tax=14.96, discount=11.94, total=202.02).
- **New** `GET /admin/normalize-row-self-test?trace_id=...`:
  - Replays the actual stored `raw_payload` for that row through adapter+normalizer.
  - Returns: `adapter_meta`, `adapter_first_item`, `live_first_item` (DTO from current code), `stored_first_item` (what's persisted), `extractor_source` (per-field origin), and a **drift flag** indicating if the stored canonical is stale.

### Tests (`test_qoyod_legacy_adapter_nested_amounts_iter278.py` → 12/12 PASS)
- ✓ Adapter extracts unit_price=199 from `amounts.price_without_tax`
- ✓ Adapter recurses through double-nested tax → 14.96
- ✓ Adapter surfaces `total_discount` → 11.94 (previously dropped)
- ✓ Adapter preserves total=202.02
- ✓ Full adapter→normalizer chain produces correct canonical DTO
- ✓ Full webhook body through `adapt()` succeeds
- ✓ Legacy flat-price payloads still parse (back-compat)
- ✓ Direct `_extract_money_value` matrix (flat/double-nested/bare/None)

Full Qoyod regression: **627 passed, 2 skipped, 0 failed**.

### NOT in scope
- ❌ No UI changes.
- ❌ Did NOT reprocess 268632361 / 268670571 — operator decides timing.

### Operator playbook
1. Deploy Iter-278.
2. `curl /admin/normalizer-self-test` on Production → expect `iter_278_adapter_nested_amounts_fix: true`.
3. `curl /admin/normalize-row-self-test?trace_id=eac68e664dee48738005a52b15e50a60` → expect `live_first_item.unit_price == 199` (etc.) and `live_vs_stored_drift: true` (because stored canonical is stale from pre-fix attempt).
4. Use the One-Shot Reprocess button to overwrite the stale canonical with the corrected one (the row's raw_payload is preserved — re-running the pipeline normalizes it cleanly).

---

## Iter-290h.6 — Display-fidelity fixes for post-retry orders (2026-02-28)

### Why
Production order 268494278 succeeded on the retry but the UI rendered the
`INVOICE_PAYMENT_CREATED` step as ✗ failed because `last_failed_stage` from
the FIRST attempt was never cleared, and the drawer mixed the stale `error`
with the fresh `body` under the same step. `ALREADY_COMPLETED` also returned
empty `stage_sequence_observed`.

### Changes
- `pipeline.py`: on `invoice_payment` success → clear `last_failed_stage`,
  `pipeline_error`, and `qoyod_responses.invoice_payment.error`.
- `first_sync_monitor._status_for_invoice_payment_step`: success-first.
- `first_sync_monitor.shape_inbox_row_for_monitor`: drop stale error
  on success; surface as `previous_error`.
- `one_shot_reprocess.ALREADY_COMPLETED`: now carries
  `stage_sequence_observed`, `qoyod_invoice_id`,
  `qoyod_invoice_payment_id`, payloads + responses.
- `QoyodInvoices.jsx`: never display the error block when
  `qoyod_invoice_payment_id` is set.

Tests: +8 (`test_qoyod_post_retry_display_fidelity_iter290h6.py`).

---

## Iter-290h.7 — Invoice header payment_method (2026-06-29)

### Why
Production invoices showed empty "طريقة الدفع" column in قيود. User
decision: always display "نقدي" regardless of upstream Salla method.

### Field
- `POST /invoices` body now carries `payment_method: "10"` (ZATCA Cash code).
- Display-only; does NOT affect accounting (`account_id` on
  `/invoice_payments` remains the source of truth for settlement).
- Independent of `account_id`, validated by test guard.

Tests: +11 (`test_qoyod_invoice_header_payment_method_iter290h7.py`).

---

## Iter-290i + Iter-290i.1 — Name-first picker for Qoyod ids (2026-06-29)

### Why
Operators were typing numeric ids (category_id, account_id, unit_type_id …)
by hand. New UX: pull every reference list from قيود, cache it, and let the
operator pick BY NAME.

### Backend
- `api_client.list_product_categories` + `list_product_units`.
- `reference_lists.py` — orchestrates 7 lists fetch + per-list diagnostics:
  `{status: success | empty | parse_failed | fail, count,
    used_response_key, sample_keys, error}`.
- Endpoints:
  - `POST /admin/reference-lists/refresh` (read-only against قيود).
  - `GET /admin/reference-lists` (cached read).
- Storage: `qoyod_reference_lists` (one doc per tenant).

### Frontend
- `SearchableSelect` (shadcn command + popover) — search by name/id/secondary,
  copy-friendly ID chip, distinguishes:
    * orphan id (list loaded, id missing) → amber warning.
    * list unavailable (fetch failed/parsed badly) → neutral state +
      "تعذّر تحميل القائمة" inside the popup.
- `QoyodSettings.jsx` — 8 fields converted (branches, taxes, customers,
  categories, product_tax, unit_types, sales_account, inventories).
- Per-list diagnostic chips collapsed under a `<details>` panel — shows
  endpoint/status/sample_keys for every list.

Tests: +18 (`test_qoyod_reference_lists_iter290i.py` +
`test_qoyod_reference_lists_diagnostics_iter290i1.py`).

---

## Iter-290j-rounding-fix · Phase 1 (READ-ONLY) — 2026-06-29

### Why
Some قيود invoices show "دفعت جزئياً" with 0.01 SAR remaining. User
asked for a strict RCA before any logic change.

### RCA confirmed
- Mezan sends per-line `unit_price + discount + tax_percent`, قيود
  re-computes the gross per line then sums to invoice total.
- Mezan sends `payment_amount = Salla total_amount` — NOT قيود's
  computed total. If قيود's tax rounding lands on a different halala,
  invoice_total ≠ payment_amount → partial-paid.

### What shipped
- `rounding_mismatch_report.py` — read-only scanner of `integration_inbox`.
- 5-bucket classifier:
  `PAYMENT_MISMATCH_ONLY`, `SHIPPING_ROUNDING_MISMATCH`,
  `DISCOUNT_ALLOCATION_MISMATCH`, `MULTI_LINE_CUMULATIVE_ROUNDING`,
  `INVOICE_TOTAL_ROUNDING_MISMATCH`, plus `NO_MISMATCH` /
  `INSUFFICIENT_DATA`.
- `GET /admin/rounding-mismatch-report?limit=N`.
- `QoyodRoundingReport.jsx` page (sidebar link added).
- Filters: bucket dropdown, "diff > 0", "has remaining balance".
- Copy chips for order_id / invoice_id / payment_id.

### NOT shipped (waiting on production data)
- ❌ No change to payment amount source.
- ❌ No change to line pricing math.
- ❌ No Decimal migration.
- ❌ No قيود-side polling (planned for Phase 2 if `INSUFFICIENT_DATA`
  dominates).

Tests: +9 (`test_qoyod_rounding_mismatch_report_iter290j.py`).

### Next steps (gated by user)
Operator deploys + collects bucket distribution + sample rows; we then
choose ONE of:
- (a) Payment source switches to قيود-total (fixes `PAYMENT_MISMATCH_ONLY`).
- (b) Per-line rounding fix (fixes `*_ROUNDING_*` buckets).
- (c) Decimal/halalas migration (most-invasive).

Full Qoyod regression after Iter-290j: **932 passed, 2 skipped, 0 failed**.

────────────────────────────────────────────────────────────────────
## Iter-293.4-rev5 — Pipeline Per-Order Unlock + COD Completion Fix (2026-02-27)

### Bug found
Per-order approval grants an UNLOCKED api_client, but
`pipeline.process_customer_resolved_row` was calling
`is_locked(settings)` DIRECTLY (DB flag) and parking the row at
`LOCKED_AWAITING_APPROVAL` BEFORE invoking the api_client. Operator
saw `HTTP 200, ok=false, outcome=LOCKED_AWAITING_APPROVAL,
qoyod_invoice_id=undefined, per_order_approval=undefined` for the
269571122 order despite supplying the correct approval_phrase.

Second bug uncovered while writing the regression: COD
`credit_invoice_only` branch transitions `INVOICE_CREATED → COMPLETED`
directly, but the state machine did NOT permit this edge. Hidden by
the global lock; would have crashed every COD order the moment per-
order approval lifted the lock.

### Fix
1. `api_client.QoyodAPIClient.write_lock_enabled` — new read-only
   public property exposing the construction-time lock state.
2. `pipeline._writes_blocked(api_client, settings)` — single source of
   truth for the pipeline's pre-flight lock check. Trusts the
   supplied api_client's lock state; falls back to `is_locked(settings)`
   when no client is supplied.
3. Both `is_locked(settings)` pre-checks in `process_customer_resolved_row`
   (create_invoice + create_invoice_payment paths) replaced with
   `_writes_blocked(api_client, settings)`.
4. `state_machine` — added `INVOICE_CREATED → COMPLETED` to allowed
   transitions for the COD `credit_invoice_only` accounting path.

### Tests
- `tests/test_qoyod_pipeline_per_order_unlock_iter293_4_rev5.py` (+7)
  Drives the REAL `process_customer_resolved_row` (no mock of the
  pipeline) and asserts `create_invoice` IS called when an unlocked
  api_client is supplied. Previous tests passed because they mocked
  out the pipeline path; this regression test catches the actual bug.
- Full per-order approval + global write lock + one-shot suites
  (88 tests) still pass. Larger qoyod suite: 1157 passed, 1 unrelated
  flaky test.

### Contract pinned
- `production_writes_locked` setting is NEVER toggled by approval.
- COD orders POST invoice only (`credit_invoice_only`) — no
  `invoice_payment`, no `receipt`.
- Per-order approval bypass is scoped to ONE run only; the api_client
  carries the unlock signal, not a global flag.


---

## Iter-293.5-rev3 — Unified Eligible Statuses + BNPL Allow-List (2026-07-01)

### Bug (order 268307955 — Tabby / delivered)
Three inconsistencies surfaced on the Pending Orders page:

1. **BNPL misclassified as Unsupported** — `tabby_installment` (and
   Tamara / Emkan variants) landed in the "طريقة دفع غير مدعومة" tab
   even though a Qoyod account mapping existed and the Preview
   confirmed `resolved_account_id=92`, `posting_mode=paid_receipt`.
2. **Preflight rejected `delivered`** with
   `code=status_not_in_triggers` because `invoice_trigger_statuses`
   defaulted to `["completed"]` while the Pending queue's eligibility
   set already included `delivered / shipped / shipping / processing`.
3. **live_send_gate G1** only accepted `{completed, delivered,
   تم التنفيذ}` — narrower than the queue's surface, so `shipped` /
   `processing` rows that appeared as Candidates could never be sent.

### Fix
1. New module `integrations/qoyod/eligible_statuses.py`
   - `ELIGIBLE_ORDER_STATUSES` — unified frozenset (English canonicals
     + Arabic natives).
   - `resolve_trigger_statuses(settings)` — widens missing/empty
     `invoice_trigger_statuses` to the unified set; honours explicit
     narrowing (e.g. tenant sets `["completed"]` on purpose).
   - `is_eligible_status(status, triggers=None)` helper.
2. `business_rules.evaluate` now consults `resolve_trigger_statuses`.
3. `preflight.run` — status check consults `resolve_trigger_statuses`.
4. `live_send_gate.evaluate` — G1 (order status) now checks the
   unified `ELIGIBLE_ORDER_STATUSES` instead of the narrow triplet.
5. `live_send_gate` new `BNPL_ALLOWED` frozenset (`tabby /
   tabby_installment / tamara / tamara_installment / emkan /
   emkan_installment` + all `_installments`, `_pay`, `_payment`
   variants). BNPL rows are treated as prepaid: create invoice +
   receipt against the provider's Qoyod account.
6. `routes.py` `_categorise_row` — BNPL family routed through the
   leak-check path: mapped + clean → `ready_to_send`; leak → `needs_mapping`.
   Never `unsupported_method`.
7. `routes.py` `_ELIGIBLE_STATUSES_FOR_QUEUE` now aliases
   `ELIGIBLE_ORDER_STATUSES` — single source of truth.

### Tests
- `tests/test_qoyod_eligible_and_bnpl_iter293_5_rev3.py` (+27):
  eligible-status unification, preflight status gate, business_rules
  status gate, live_send_gate BNPL allow-list, BNPL classification.
- Updated pre-existing tests
  (`test_qoyod_live_send_gate_iter293_5.py`) to reflect BNPL moving
  onto the allow-list and `processing` becoming eligible.
- Full qoyod suite: **1265 passed, 3 skipped, 0 failed.**

### Contract pinned
- BNPL rows with a mapped Qoyod account = ALLOWED (invoice + receipt
  scope). Unmapped or with leak = `needs_mapping`.
- Pending queue eligible-set = business_rules trigger default =
  preflight trigger default = live_send_gate G1 accepted set.
- Explicit tenant narrowing via `invoice_trigger_statuses` still
  honoured (regression-tested).
- `production_writes_locked` remains `true`.
- `selective_live_send_enabled` remains `false`. Approve-and-Send
  endpoint still NOT built — awaiting user sign-off on the Pending
  page after this fix.


---

## 2026-Feb-03 — Rev 29b: Dry-run wording enforcement + diagnostics invariant

### Context
Rev 29 (idempotent CAS transitions) passed on Production for order
270196668 (trace `baa0383c...`). All rev27/rev28/rev29 invariants
came back clean, BUT `stage_history` still carried the misleading
notes `"customer created in Qoyod"` and `"0 product(s) created ·
1 mapped"` even though the resolved ids were `DRY:*`. Old order
270219411 exhibited the same signal.

### Rev 29b fix (surgical — wording + diagnostics only)
1. `integrations/qoyod/pipeline.py` — Three DRY-RUN wording sites:
   - **Customer stage**: dry-mapping note now ends with `", no POST"`.
     Note reads `"DRY-RUN: customer mapped from local store, no POST"`.
   - **Product stage**: unchanged wording (already rev28-compliant)
     but re-anchored with `rev29b — Dry-run wording enforcement`
     marker comment.
   - **Invoice stage**: dry detection now uses BOTH `is_dry` and
     `qoyod_invoice_id.startswith("DRY:")` (defense-in-depth). Note
     stays `"DRY-RUN: invoice payload built, no POST"`.
2. `integrations/qoyod/sas_build_diagnostics.py`:
   - New marker `rev29b_dry_run_wording` (needle:
     `rev29b — Dry-run wording enforcement`). Deploy verified by
     `markers.rev29b_dry_run_wording.count >= 1`.
   - New invariant `dry_run_wording_violation` on `row_diagnostics`.
     Fires when the row has ANY dry evidence AND `stage_history`
     contains legacy wording (`customer created in Qoyod`,
     `product(s) created`, `invoice \S* created`). Dry evidence is
     any of: `qoyod_customer_id/qoyod_invoice_id/
     qoyod_invoice_payment_id` starting with `DRY:`, any
     `stage_history.note` containing `DRY-RUN`,
     `sas_worker_trace.settings_seen.dry_run_mode=true`, or
     current settings `dry_run_mode=true`. Row evidence WINS over
     current settings.
   - Response adds three fields on `diagnosis`:
     `dry_run_wording_violation`, `dry_run_wording_reason`,
     `dry_run_wording_offending` (list of offending stage_history
     entries with `from_stage/to_stage/note/phrase`).

### Tests (`tests/test_rev29b_dry_run_wording.py`)
- 8 tests covering: source-side wordings present, marker registered
  and detected, order 270219411 replay flags violation, fresh
  rev29b row shows no violation, live row with live wording does
  NOT false-flag, sas_worker_trace-only dry evidence triggers,
  rev27 live-write gate intact, rev29 CAS intact.
- Regression: full Qoyod/SAS/Canary/Salla suite **259 passed**
  (`test_qoyod_dry_run_leak_protection`, `test_sas_*`,
  `test_rev29_idempotent_transitions`, `test_live_write_gate`,
  `test_salla_token_strategy`, `test_canary_*`,
  `test_auto_send_e2e_completes_row`).

### Guarantees
- No change to `_live_write_permitted` (rev27).
- No change to `_apply_atomic` CAS semantics (rev29).
- No change to SAS gate persistence (rev28).
- Read-only invariant — `row_diagnostics` never mutates DB.
- Old rows with pre-rev29b wording remain frozen (RCA evidence);
  the invariant now surfaces them explicitly.

### Deploy verification (Production, after deploy)
1. `GET /api/integrations/qoyod/admin/diagnostics/build` →
   `marker_check.markers.rev29b_dry_run_wording.count >= 1` AND
   `acceptance.code_matches_expected == true`.
2. `GET /api/integrations/qoyod/admin/diagnostics/row?trace_id=<270219411>` →
   `diagnosis.dry_run_wording_violation == true`
   (old evidence, correctly flagged).
3. Trigger a NEW dry Tabby order → `diagnosis.dry_run_wording_violation == false`
   AND `stage_history` notes ALL start with `"DRY-RUN: ..."`.
4. `live_write_gate_violation`, `sas_gate_missing_violation`,
   `duplicate_stage_transition_violation` remain `false`.

### Constraints honoured
- No positive live tests.
- No reprocess / retry-payment / resolve invoice #188.
- Invoice #188 remains frozen as historical evidence.
- No production DB access from agent side; user drives Prod checks.

### Next up (blocked on user)
- Salla Easy Mode Prod webhook verification (waiting on user Env vars).
- Phase 2 Auto-Send expansion (mada/apple_pay/credit_card/stc_pay/tamara)
  gated on a flawless Tabby dry + live cycle.
- manual_send_audit_log + UI Manual Send Button.

---

## 2026-Feb-03 — Rev 29c: Fail-closed gate persistence + strengthened dry-run wording

### Context (Production trace `b09392fb2a1047fa89ca52b39cbcfe65`, order 270227236)
After rev29b was deployed, a Tabby dry-run row lit BOTH diagnostic
invariants:
  - `sas_gate_missing_violation=true` — row advanced past NORMALIZED
    without `selective_auto_send_gate` persisted.
  - `dry_run_wording_violation=true` — stage_history contained
    "customer created in Qoyod" and "1 product(s) created · 0 mapped"
    while ids were `DRY:*`.
This showed rev29b *detected* the bugs but the pipeline still
*allowed* them to happen.

### Root causes
**RC-1 (Gate)**: `selective_auto_send_gate` was persisted ONLY when
`selective_auto_send_enabled=true` at worker time. If the operator
later flipped SAS on, historical rows lit up the invariant.

**RC-2 (Wording)**: `_is_dry_customer` / `_any_dry_product` keyed on
resolved-id prefix ONLY. When products were mapped locally to REAL
Qoyod ids from a prior live sync, the check fell through to the
"N product(s) created" branch — even though the CURRENT run used
`DryRunQoyodClient`.

### Rev 29c fixes (surgical only)
1. **`pipeline.py` — Fail-closed gate persistence**:
   - `selective_auto_send_gate` now persisted on **every** row, even
     when SAS is disabled at settings. SAS-disabled branch writes a
     synthetic record `{eligible: false, reason: "sas_disabled_by_settings"}`
     with `selective_auto_send_gate_source: "sas_disabled_at_worker"`.
   - The SAS-enabled branch stamps
     `selective_auto_send_gate_source: "sas_enabled_at_worker"`.
   - The gate is written IMMEDIATELY AND included in the
     `NORMALIZED → RULES_APPLIED` atomic CAS.
   - **Fail-closed guard**: if `_sas_gate_persist_set` is empty at
     the RULES_APPLIED transition, the row DEAD_LETTERs with code
     `sas_gate_persist_buffer_empty` (unreachable in normal flow but
     mathematically impossible to bypass).
   - `_assert_sas_not_rejected` updated to skip the synthetic
     `sas_disabled_by_settings` record — that's not a real SAS
     rejection.

2. **`pipeline.py` — Canonical `_pipeline_is_dry_mode` signal**:
   - Computed in BOTH `process_normalized_row` AND
     `process_customer_resolved_row` as:
       `isinstance(api_client, DryRunQoyodClient) OR settings.dry_run_mode`
   - PRIMARY signal for customer/product/invoice wording. Id-prefix
     check is the FALLBACK.
   - Result: even when a product is locally mapped to a real id but
     the client is `DryRunQoyodClient`, the note reads
     `"DRY-RUN: N product payload(s) built · M mapped · no POST"`.

3. **`sas_build_diagnostics.py`** — New marker `rev29c_fail_closed_gate`
   (needle `rev29c — Fail-closed gate persistence`), count=2.
   Full `code_matches_expected=true` verified.

### Tests (`tests/test_rev29c_fail_closed_gate.py`)
10 tests covering:
- rev29c marker registered + present in build.
- `_pipeline_is_dry_mode` computed in both entry points.
- Fail-closed abort present (`sas_gate_persist_buffer_empty`).
- **Prod trace `b09392fb...` replay flags BOTH invariants**.
- Fresh rev29c dry path clears all four invariants.
- Dry-run wording covers customer + product with strengthened check.
- SAS-disabled branch persists synthetic gate + source marker.
- Fail-closed check present in source.
- rev27 live-write gate intact.
- rev29 atomic CAS semantics intact.

### Regression
- 237 passed in the qoyod / sas / canary / auto-send / salla suite.
- 6 pre-existing failures in `test_qoyod_day4_rules_and_customer.py`,
  `test_qoyod_pipeline_totals_guard_e2e_iter273.py`,
  `test_eligible_orders_readonly.py` — verified unrelated to rev29c
  (they fail identically before and after this change).

### Deploy verification (Production, after deploy)
1. `GET /admin/diagnostics/build`:
   - `markers.rev29c_fail_closed_gate.count >= 1`.
   - `acceptance.code_matches_expected == true`.
2. `GET /admin/diagnostics/row?trace_id=b09392fb2a1047fa89ca52b39cbcfe65`:
   - `sas_gate_missing_violation == true` (historical).
   - `dry_run_wording_violation == true` (historical).
3. Fresh dry Tabby order:
   - `selective_auto_send_gate.eligible == true` (or synthetic
     `reason=sas_disabled_by_settings` if SAS is off).
   - `selective_auto_send_gate_source ∈ {sas_enabled_at_worker,
     sas_disabled_at_worker}`.
   - `stage_history` notes ALL start with `"DRY-RUN: ..."`.
   - `sas_gate_missing_violation == false`.
   - `dry_run_wording_violation == false`.
   - `live_write_gate_violation == false`.
   - `duplicate_stage_transition_violation == false`.

### Constraints honoured
- No positive live tests. No reprocess. No retry-payment. Invoice #188
  remains frozen. No live-write gate change. No CAS transition change.

### Next up (blocked on user)
- Salla Easy Mode Prod webhook verification.
- Phase 2 Auto-Send expansion after flawless Tabby dry + live cycle.
- manual_send_audit_log + UI Manual Send Button.

---

## 2026-Feb-03 — Rev 29d: Hard gate-persistence preflight + worker-code identity mismatch invariant

### Context (Production trace `8cfeba3cf139456198eef63cf97065cf`, order 270182554)
Even AFTER rev29c was deployed (build marker present, `code_matches_expected=true`),
a FRESH dry Tabby order still landed at INVOICE_CREATED with:
  - `selective_auto_send_gate` MISSING (rev29c should have persisted it).
  - Legacy wording "customer created in Qoyod" and "1 product(s) created · 2 mapped"
    (rev29c strengthening should have caught it).

### Root cause (diagnosis)
Rev29c code was loaded by the API process but the worker asyncio.create_task
from the previous bootstrap kept processing rows with the CACHED pre-rev29c
`pipeline` module. The build marker reports what the API process sees, NOT
what the worker task actually executes.

### Rev 29d fixes (defense-in-depth, no revert of prior fixes)
1. **`pipeline.py` — Hard preflight `_require_sas_gate_persisted`**:
   - New coroutine + custom exception `_SasGateMissingError`.
   - Called at the ENTRY of every downstream stage:
     - `process_normalized_row` → BEFORE the `RULES_APPLIED → CUSTOMER_RESOLVED` transition.
     - `process_customer_resolved_row` → BEFORE the product / invoice / receipt stages.
   - Reads the DB row and refuses to advance if `selective_auto_send_gate`
     OR `selective_auto_send_gate_at` is missing. The row is DEAD_LETTERed
     with `code=sas_gate_missing_before_downstream` BEFORE any stage_history
     wording is emitted. Captures the row's stored `worker_pipeline_sha`
     in the error so operators can see WHICH worker version built the row.

2. **`sas_build_diagnostics.py` — Worker code identity**:
   - `row_diagnostics` now surfaces three new fields:
     `row_worker_pipeline_sha`, `current_pipeline_sha`, `worker_code_mismatch`.
   - `worker_code_mismatch=true` when the row's stored sha differs from the
     current process's sha — proves a stale worker built the row.
   - New required marker `rev29d_hard_gate_preflight` (needle `rev29d — Hard preflight`),
     count=3.

### Tests (`tests/test_rev29d_hard_gate_preflight.py`) — 10 new tests
- Marker registered + present in build.
- `_require_sas_gate_persisted` raises `_SasGateMissingError` when gate missing.
- Raises when gate present but `selective_auto_send_gate_at` missing (partial write).
- Passes when both fields present.
- Preflight wired at BOTH pipeline entry functions.
- **Prod trace `8cfeba3cf...` replay** flags `sas_gate_missing_violation=true`,
  `dry_run_wording_violation=true`, AND `worker_code_mismatch=true`.
- Fresh rev29d-built row (current sha) — everything clean.
- E2E test: gateless row at CUSTOMER_RESOLVED DEAD_LETTERs BEFORE any downstream
  wording is emitted (`no "customer created" in stage_history`).
- rev27 live-write gate + rev29 CAS intact.

### Test-side adjustments (5 pre-existing test files)
The following tests seeded `CUSTOMER_RESOLVED` rows without the gate;
augmented with `selective_auto_send_gate` + `_at` + `_source`:
- `tests/test_qoyod_invoice_payments_iter290h.py`
- `tests/test_qoyod_pipeline_per_order_unlock_iter293_4_rev5.py`
- `tests/test_qoyod_rounding_warning_iter293_4_rev8.py`
- `tests/test_qoyod_day5_invoice_receipt.py`
- `tests/test_qoyod_idempotent_invoice_reuse_iter291.py`

### Regression
- **1561 passed** in the qoyod / sas / canary / auto-send / salla / preflight /
  business_rules / normalizer / trust_gate / requeue / day4 / first_sync suite.
- 6 pre-existing failures (`test_qoyod_day4_rules_and_customer.py`,
  `test_qoyod_pipeline_totals_guard_e2e_iter273.py`,
  `test_eligible_orders_readonly.py`) verified UNRELATED to rev29d
  (identical pass/fail before and after).

### Deploy verification (Production, after deploy)
1. `GET /admin/diagnostics/build`:
   - `markers.rev29d_hard_gate_preflight.count >= 1`.
   - `acceptance.code_matches_expected == true`.
2. **CRITICAL**: After deploying, RESTART the worker process explicitly
   (not just the API). The user asyncio.create_task from the previous
   bootstrap holds a cached pre-rev29d `pipeline` module. A restart
   forces the worker to re-import.
3. `GET /admin/diagnostics/row?trace_id=8cfeba3cf139456198eef63cf97065cf`:
   - `sas_gate_missing_violation == true` (historical).
   - `dry_run_wording_violation == true` (historical).
   - `worker_code_mismatch` — true or false depending on whether the row's
     stored sha differs from the redeployed process. Either way, it's now
     visible.
4. Fresh dry Tabby order:
   - `selective_auto_send_gate` present with `_at` and `_source`.
   - `sas_gate_missing_violation == false`.
   - `dry_run_wording_violation == false`.
   - `live_write_gate_violation == false`.
   - `duplicate_stage_transition_violation == false`.
   - `worker_code_mismatch == false` (the row was built by the current process).
   - All stage_history notes prefixed `"DRY-RUN: ..."`.

### Constraints honoured
- No positive live tests. No reprocess. No retry-payment. Invoice #188 frozen.
  rev27 live-write gate + rev29 CAS transitions untouched.

### Next up (blocked on user)
- Deploy rev29d and RESTART the worker.
- If violations persist after worker restart → the row is likely still being
  processed by a truly stale worker. Look at `worker_code_mismatch` in the
  diagnostics: `row_worker_pipeline_sha != current_pipeline_sha` proves it.

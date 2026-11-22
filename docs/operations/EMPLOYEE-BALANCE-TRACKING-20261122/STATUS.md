# Employee self balance and compact mobile tracking

Date: 2026-11-22 (Asia/Riyadh). Branch: `codex/assembly-queues-fast-read-20261121`. Backend [PR2050](https://github.com/AMASI-SA/AMASI-SA/pull/2050); employee app [PR373](https://github.com/AMASI-SA/amasi-mobile/pull/373).

## Scope and placement

The user explicitly requires both previously missing features in the new employee app release and confirmed that both screens belong in the **employee app**. Personal balance is a new side-menu entry, `رصيدي`, titled with the signed-in employee's name. Compact tracking replaces the existing global order-search result; it is not another Mezan web page. The courier delivery-count-definition discrepancy (315 vs 254) is explicitly deferred.

Implementation/publication remain authorized. App-first publication is allowed while Mezan waits. This extends the existing queue, monitoring, notification, shipment matching and customer-refund sources; no financial writer is replaced. Starting backend checkpoint: `3cda32257293dab9681af79fd4508ef0f3a590f3`. The other conversation's checkout and release lease remain untouched.

## Personal financial read contract

- GET `/employee-finance/me/statement?page=1&page_size=10` and GET `/employee-finance/me/movements/{em_64hex}?snapshot={64hex}`. Later pages require the same snapshot; revisions require an explicit first-page reload.
- Literal derived `app.page.employee_balance` advertises server support without adding an `/auth/me` lookup. It is not an assignable broad report grant. Disabled/archived/deleted accounts, drivers and borrowed native actors cannot obtain it.
- Every read reloads the real account and unique active merchant-scoped employee link; final authorization is rechecked after projection. No employee selector or name-based account matching exists. An unavailable link/ledger is an error, never a fabricated zero.
- Current balance, running balance and details reuse canonical operational report/statement projection. Positive means employee credit, negative means employee debt. Actual employee custody is displayed independently. No sums from notifications or one page of rows.
- Decimal string amounts, scoped opaque movement IDs, canonical Riyadh-aware timestamps and explicit null history. Legacy aware timestamps using a space are normalized to ISO `T`. Unreconciled running balance remains unavailable.
- Saved aggregate read only, no provider refresh/write or order/inbox/audit/directory loading. Eight-second deadline, cancellation and no-store. Ten UI rows do **not** imply ten database rows: the existing canonical saved financial projection can require processing the aggregate. No production latency benchmark is claimed.

## Global tracking read contract

- Static GET `/mobile/order-tracking/lookup?query=...` precedes the numeric route. It requires the existing native `app.action.orders.track` grant and resolves the printed opaque piece ID with a tenant-scoped exact query, archived exclusion, two-candidate ceiling and bounded database time.
- Returns the existing whole-order projection plus the matched piece ID. Invalid, absent and ambiguous identities remain distinct. No SKU guessing, broad order scan, assembly-permission fallback or new writer.
- Additive optional event metadata names the employee/product assignment, product supplier receipt and named service receipt separately. There is no invented service-assignment event. The first committed receipt owns the physical product; later named service evidence does not fabricate another product receipt.
- Original supplier names come from scoped dispatch membership or proven same-supplier evidence. Later service suppliers cannot relabel the original dispatch. Direct-at-receipt consent writes two timestamps for one assignment; equal proven timestamps render one named event while a genuinely earlier dispatch remains visible.
- Explicit saved `assembly_ready` requires valid current piece/source/unit identity and excludes held, blocked, cancelled and conflicting records. Location text alone is not approval.
- Bounded assignment/service history, at most 100 service receipt events, preserved original physical receipt, and explicit truncation notice. Existing order status, physical identity and action policy remain authoritative.

## Verification

- Root real isolated-Mongo integrated run: **115 passed, exit0, 22.63s**, covering balance, financial notices, runtime auth, permissions, mobile request context, tracking and barcode stability.
- Independent review reproduced a duplicate direct supplier-assignment event using the actual `direct_assignment_patch` producer. New regression failed before the fix. Final affected tracking/barcode run after the fix: **33 passed, zero skips, exit0, 7.96s**. This supersedes that portion of the earlier integrated run.
- Adjacent manager/owner/operational permission run: **27 passed and seven subtests**, exit0. Three old exact permission expectations were updated to include only the new derived self key; owner no-extra-lookup and negative authority tests remain intact.
- Personal balance suite: **13 passed**. Independent backend-to-mobile synthetic contract check exercised actual `_project`/`_public` outputs through the mobile parser/screen, preserving separate balance/custody and source/registrar/full note.
- Paired mobile behavior: 12 balance groups, 18 compact tracking groups and 19 existing tracking groups passed. Mobile reviewer confirmed exact requested barcode ID, returned order membership, cancellation and fresh identity fences.
- CI adds the new balance suite with a 13-test no-skip floor. Existing gates are retained. All database execution used isolated synthetic loopback Mongo, never production financial records.

## Publication and continuation

Source implementation is complete; this task branch is an integration checkpoint, **not** a governed release source/intent pair. No production merge, release intent, lease, shared `/app` change or deployment occurred. No real refund, financial adjustment, bank/provider action, permission grant or external notification was made.

After the paired source checkpoint, recheck current CI and publication state. The new personal balance capability and barcode lookup require this backend release; old-server mobile behavior remains explicit and safe. The user allows the app to publish first. Existing Mezan production was last observed at `9b4fb716b430c32073b5ea7abaeaff62646d9633`; verify again before preparing release work.

Later Mezan publication must obey `AGENTS.md` v5: inspect the then-current other release and actual shared deployment guard, never clear another lease, prepare reviewed source A plus intent-only B, satisfy fresh checks and clean-clone reproducibility, then obtain explicit deployment success and three identity probes. The inherited integration intent does not satisfy this requirement. See the paired mobile status for Android bundle/build blockers and exact source checkpoint.

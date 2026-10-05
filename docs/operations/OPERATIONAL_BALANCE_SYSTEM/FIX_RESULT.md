# OPERATIONAL_BALANCE_FIX_RESULT — WIP

Automated fixes verified; **not FINAL_REVIEW_PASS**. Device UAT remains pending and four general native verifiers still fail. This checkpoint authorizes neither release nor production use.

| Problem | Proven before | Change / after | Automated result |
|---|---|---|---|
| COD previous payment | Gross300/paid80 incorrectly recognized300 | Native paid/remaining evidence yields220; gross/collected/customer remaining/custody/remittance separate | PASS |
| Driver non-cash COD | Bank/card amount became driver custody | Native cash custody only; bank/card0; no implicit bank movement | PASS |
| Supplier tax | Invoice net80/tax12 confirmed80 | Net80/tax12/gross92; expected100-net80=20; confirmed92 | PASS |
| Shipping rich/partial contract | Missing flat fee discarded whole order | Approved native pure calculator; independent base/commission facts; incomplete commission retains verified base, COD and product estimates | PASS |
| Advertising timezone | UTC-account140 disappeared at Riyadh startup boundary | Account timezone for day/start/02:00close; cumulative snapshots; boundary regression tests | PASS |
| Ownership retry | Same staff request saved once under A and again under B | Actor/request immutable claim, required client scope, CAS replay; A→B/retry/concurrent gives one movement | PASS |
| Recurring invoice | Invoice promoted unpaid estimate | Fullperiod5000 Expected/Actual0; only explicit payment moves allocated amount to settled | PASS |
| Definitive rejection UI | Business409 permanently locked form | Persist rejection in same CAS as acceptance; committed rejection unlocks corrected new intent; unknown outcome stays pending | PASS |
| Late COD evidence | First delivered observation could freeze missing collection at0 | Freeze executor independently; confirm late evidenced200 once | PASS |

Supplier Expected20/Confirmed80 for net-only invoice80 is the user's final rule, not a defect. No new receipt-confirmation workflow is added.

## Evidence and parity

- All operational Backend tests: **136 PASS, 0 FAIL**; UUID local Mongo only, no production server/config imports, `--noconftest`.
- Web: **38 PASS, 0 FAIL**, three affected suites. Native TypeScript passes. Native full verifier sweep: **11 PASS / 4 FAIL** (supplier-invoice-services, rtl, product-cost-setup, component-soft-stop), previously reproduced on the untouched native base. They are not hidden or counted as passes.
- MZ2-only: `test_runtime_collection_gate_exercises_every_reader_and_traps_legacy`, `test_legacy_only_identity_is_not_visible`, HTTP legacy identity rejection, and native invoice integration collection allowlist pass. The only added persistence is independent operation claims; MZ2 Accounting files are unchanged.
- Native rich shipping calculation imports only the existing pure contract calculator, not an accounting service/writer. Native invoice tax is read as evidence; its producer is never invoked.
- Idempotency: five real Mongo ownership/rejection regressions include A→B with8 concurrent retries, sameowner8 retries, oldWIP preservation, stale rejection racing successful commit, and committed rejection after later funding. Existing invoice/state/carrier/settlement/salary/ad/manual duplicate tests pass.
- COD settlement150 adds bank150 once; bank/card collections never generate a second bank effect. Supplier92gross, partial recurring payment2000, provider net settlement/refund, custody and owner-withdrawal separation pass integration parity assertions.

## Remaining work

Build and inspect the isolated Android package, attach it only to isolated fixtures, then execute Device UAT for opening, daily movements, bank, cash, supplier, shipping, courier, COD, platform, commission, refund, employee, custody, advertising, operating expense, owner withdrawal and recurring obligations. Native currently contains daily movements; opening and domain reports are web surfaces and must be identified as such in device evidence, never claimed as native screens.

Build artifacts use a separate application ID, disabled OTA, localhost API and general native writers disabled. Existing installed production package is not a test candidate. Browser/unit/type checks do not satisfy Device UAT.

Production data unchanged by this task. Production financial writes=0. No Merge, Prepare, Prepublish, Deploy, Accounting writer, or MZ2 Accounting modification.

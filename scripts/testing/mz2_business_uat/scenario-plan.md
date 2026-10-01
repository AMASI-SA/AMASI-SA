# Declared C5 execution plan

The acceptance actor uses a declared synthetic signed owner session validated by
the existing JWT/database verifier. Fixture authority supplies existing canonical
accounts/entities/catalogue and a native paid recurring invoice, not balances in
the onboarding session. The browser creates that session and all of its balances.

| Stage | Actual operation, from the existing UI | Independent expected result |
|---|---|---|
| 1 | Create session; upload original JSON source through financial evidence API; save Riyadh cutoff; reload | 2026-10-01T00:00+03:00, exact retained source hash |
| 2 | Select bank/cash/overdraft; enter each amount; upload/save/reload | Debit1000, explicit zero cash, credit40 overdraft; no negative cash |
| 3 | Select Tabby and exact bank; enter17; upload/save/reload | Provider receivable17, bank bindingc5-bank; other providers explicitly inapplicable in source register |
| 4 | Select native employee; enter three balances; upload/save/reload | Salary30Cr, advance10Dr, custody5Dr |
| 5 | Select supplier; enter both balances; upload/save/reload | Payable25Cr and advance10Dr remain separate |
| 6 | Create person and select returned ID; enter12; save/reload | Exact new person12Dr plus unchanged supplier siblings |
| 7 | Upload actual rich source, input terms, download bytes, explicitly review3purposes, approve/reload | Existing immutable rich contract; no journal; independent shipping20/VAT15%, commissionfraction.01+2inclusiveVAT15% |
| 8 | Actual Stage7 opening fields then Stage8 upload/save/reload | CourierCOD150Dr and payable20Cr; rich terms exclude opening balances |
| 9 | Select driver; enter separate balances; save/reload | DriverCOD100Dr and fee15Cr; courier siblings unchanged |
| 10 | Pick product variants/component; quantity×cost; persist/reload; input account valuation and original evidence | 100+180+15=295, productaccount280/componentaccount15; planned metadata only |
| 11 | Create fee policy through actual UI; select/save/reload | Tabby2.5%+1exclusive VAT, effective2026-01-01; provider17 retained |
| 12 | Select confirmed TrackE identity and explicit two financial IDs; save/reload | Wallet40Dr and adpayable15Cr, no netting; provider/fee siblings retained |
| 13 | Upload/save equity evidence; select native paid invoice; save/reload | 366inclusive coverage days,41consumed,325remaining; prepaid3250Dr |
| 14 | Create five supported typed facts via UI; save/reload; unsupported deposit negative case | Accrued10Cr, otherpay20Cr, otherrecv30Dr, salesVAT25Cr,inputVAT10Dr; no deposit fallback |
| 15 | Server preview and actual review from UI; reload; rejected edit | Exact24entries; preequity4929Dr/200Cr, existingequity4729Cr; final4929each; one explicit zero; reviewed immutable |
| 16 | Actual locked final screen/readiness; separate initiallypausedowner404/423 probe | Setup source ready, live readinessfalse, physicalapprovalfalse; no OpeningPost/Activation; pausedowner entirely unchanged |

Every stage records response/session identity, version, successful reload and
its explicit assertions. Original files all contain the complete frozen source
register so shared financial section evidence covers sibling screens. No
unmentioned real business facts are inferred. The synthetic register excludes a
deposit contract; adding one is not necessary to this declared scenario.

This is the complete existing **setup** business workflow. Separate operational
shipping/driver/POS, payroll, supplier and advertising scenarios retain their own
earlier executed evidence and source identity; this runner does not pretend to
execute them through opening metadata screens. C3's actual driver/H2 seven-case
browser is separately attributed. Actual physical stock approval, live financial
opening/activation and Production proof remain explicitly NOT EXECUTED.

The setup owner's initial unpaused state is necessary for the shipped source
upload API. It is never toggled. All financial/control fields except the existing
atomic serialization revision must remain equal; no journal may exist. The
separate paused owner exists solely for the barrier assertion, with distinct
owner IDs and control evidence. No handoff attempt targets the setup owner.

# Final business acceptance matrix

Frozen source: `88cc9131027fd6783a46e2e00fc6aaec4788b8fa`.
Historical setup execution: `9b451cc03b4f8159483cbf58ef0fd128d24530e1`.
No row below promotes that historical setup result to full Business UAT or
claims a new setup execution on88cc. Current frontend/backend contract tests
are attributed separately in the final regression records.

| Stage | Declared setup evidence already retained | Final business acceptance on88cc |
|---|---|---|
| 1 Cutover/source | Synthetic source upload, hash, timestamp, reload | NOT EXECUTED as final business scenario; authoritative cutover/source not supplied |
| 2 Bank/cash |1000Dr, explicit zero cash,40Cr overdraft | NOT EXECUTED; no authoritative bank/cash opening business source |
| 3 Providers |17 receivable and exact bank binding | NOT EXECUTED as combined business scenario; native recognition/settlement regression separate |
| 4 Employee |30Cr salary,10Dr advance,5Dr custody | NOT EXECUTED as combined business scenario; payroll contract regression separate |
| 5 Suppliers |25Cr payable and10Dr advance kept separate | NOT EXECUTED as combined business scenario; invoice/payment regression separate |
| 6 External parties |Created typed identity12Dr | NOT EXECUTED as final business acceptance |
| 7 Shipping contract |Actual UI upload/review/approve of synthetic rich contract | NOT EXECUTED as final business acceptance; rich contract implementation accepted, regression separate |
| 8 Courier |150Dr COD and20Cr payable | NOT EXECUTED as combined business scenario; no netting regression retained |
| 9 Driver |100Dr COD and15Cr fee | NOT EXECUTED as combined business scenario; driver/POS/C3 evidence regression separate |
|10 Inventory |Quantity/cost metadata295,280+15 per account | Physical approval NOT EXECUTED; metadata is not counted inventory. Missing actual source/approver and verified approved-opening acceptance |
|11 Fee policy |2.5%+1exclusiveVAT policy and source reload | NOT EXECUTED as combined business scenario; fee tests separate |
|12 Advertising |40Dr wallet and15Cr payable, exact identities | NOT EXECUTED as combined business scenario; native spend/funding/payment tests separate |
|13 Equity/prepaid |Original source and native paid invoice,3250Dr remaining | NOT EXECUTED as final business acceptance |
|14 Other balances/tax |Five supported typed facts, unsupported deposit rejection | NOT EXECUTED as final business acceptance; no default identity introduced |
|15 Preview/review |24entries,4929Dr=4929Cr, immutable reviewed setup | NOT EXECUTED as final business acceptance with authoritative sources; native engine tests are technical evidence only |
|16 Final gate |Locked UI/readiness; no opening or activation | Positive opening/activation NOT EXECUTED; public409 guards still required. Fresh full-app Acceptance Smoke404/423 PASS on88cc is independent denial evidence |

Historical raw setup: `docs/operations/MZ2-FINAL-INTEGRATION-20261001/evidence/c5/acceptance-final-9b451/`.
Its scenario and oracle: `scripts/testing/mz2_business_uat/scenario-plan.md`
and `source-register.json`. Existing C3 browser coverage is a separate bounded
scenario and cannot fill missing final business acceptance rows.

Full Business UAT = **NOT PASS**. Setup16/16 remains **historical setup-only
PASS**. Positive opening, activation and physical-stock approval remain
**NOT EXECUTED**. See ACCEPTANCE-GAPS.md for shipped-route evidence and required
decisions/data. No test/assertion or financial contract has been changed.

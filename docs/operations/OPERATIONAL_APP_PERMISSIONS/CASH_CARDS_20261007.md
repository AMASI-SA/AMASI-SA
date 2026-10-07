# Cashboxes and matching responsive movement screens

Scope: companion Draft #1271 and native Draft #257 only. Frozen #1263,
Production, release configuration, accounting writers and dependencies unchanged.

Implemented:
- Mezan daily movements now follow the app hierarchy: three cards per row,
  entity cards, then account/amount/date/optional bank-message fields. Desktop
  uses two field columns; small screens stack the same fields. Existing additional
  Web movement operations remain available through `حركات أخرى`.
- Settlements retain incoming bank collection. Employee and supplier cards offer
  payment from bank or cash. Cashbox card offers funding from bank/cash and
  transfer out to bank/cash. Same-account transfers are excluded and rejected.
- Both transfer sides use the existing single operational movement engine.
  No transfer is counted as operating expense and no accounting entry is created.
- Owner can create an MZ2 cashbox without an opening balance. Staff cannot create
  one, including via direct native/browser API. Existing cashboxes can be assigned
  to staff independently of banks; cash-only assignment is valid. Native API
  checks both transfer accounts against the live assignment at persistence.
- Read and write grants remain independent. No extra reporting grant is inferred.

Evidence:
- Backend operational + employee-management suite: 207 PASS, then added
  cash-to-cash regression: cash assignment suite 6/6 PASS (208 aggregate).
- Web operational/navigation/report/history + owner management: 73 PASS, then
  added cash-only owner UI case: owner suite 27/27 PASS (74 aggregate).
- Native complete `yarn typecheck` verification chain PASS, including 21 permission
  cases, RTL, settlement render/model, employee/supplier bank/cash and transfer tests.
- `scripts/verify-operational-cards-browser.cjs`: desktop 1440x1000 and mobile
  390x844 PASS: three cards per row, no horizontal overflow, form save, no external
  network. API responses are synthetic. Screenshots in this document directory.
- CRA resets mock implementations between tests: requestId fixture now initializes
  in beforeEach so durable retry tests exercise real string identities. Updated
  prior owner-cash rejection test because owner creation is now explicitly allowed;
  all other native administration denials remain tested.

This is NOT new APK/device acceptance. No new APK built. Earlier device UAT remains
incomplete. Ads/expense bespoke cards remain subsequent design work; existing Web
operations are retained. Supplier return/discount and purchase flows were not
expanded in this slice.

Production writes = 0. No merge, deploy, prepare or prepublish.

# Operational balances design acceptance

Reference: the approved Mezan 2 RTL preview, version 4, source `ef95c895c6441a34ebcfa9e47856dfd806a0e272`. Integration base: `69f5a84ce372e7a3965c2b12c55e08c52931fb9c`.

## Interface

The authorized production workspace uses the reference palette, native RTL navigation, card and table geometry, icon sprite and the same Arabic font faces in WOFF2. The reference actor selector and sample-data badge have been replaced with the signed-in actor and the requested operations directory. The directory remains accessible from the finance sidebar. Its platform statement entries open the upload form or upload history with their original access checks.

Browser walkthrough on a production-mode build with synthetic records: home; search by supplier name; supplier statement; provider statement; review queue and order reason; platform upload control and saved statements. Navigation and eight-row paging were exercised in Chromium. Financial amounts, actor names and capabilities are driven by server responses. Column values without supporting evidence are withheld. An issue that cannot be placed on a reliable date is shown as of the last accounting snapshot.

## Load measurements

Median of three local Chromium runs, the same fixture of 96 movements and six parties. These are application readiness timings under test conditions. Production database and network latency were not measured.

| Screen | Before | After | Balances API requests |
| --- | ---: | ---: | --- |
| Operational home | 553 ms | 215 ms | 5 → 1 |
| Movement log | 531 ms | 245 ms | 2 → 2 |
| Restricted fulfillment | — | — | 0 → 0 |

The local legacy accounting ledger comparison was 523 ms on the baseline build. The new home transferred approximately 448 KiB of compressed assets versus 647 KiB previously. The new ledger transferred approximately 456 KiB versus 374 KiB previously, including both approved typefaces. The next ledger page made one request. Opening the directory or typing a name in the directory reused the authorized resource list. The fulfillment route loaded no finance screen, font, stylesheet or API in this walkthrough. The production-mode build and route-level tests cover navigation, returned amounts, scopes, query paging, financial proof, allocation and workbook construction. No production financial write was part of visual verification.

Visual QA of the inspected screens — passed.

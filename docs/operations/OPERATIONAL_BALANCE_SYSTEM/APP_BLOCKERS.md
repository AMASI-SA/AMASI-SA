# Employee application blocker diagnosis — 2026-10-05

Financial implementation is frozen. All four failures existed at native base f2d7ed60fb569dd226c6b60e16538064c368f822, before operational changes.

| Check | Evidence / cause | Resolution |
|---|---|---|
| supplier-invoice-services | Exact CRLF-sensitive text check and obsolete selected-boolean validation; current invoice uses explicit performedQuantity. Empty invoice_services already stays empty. | Verifier now executes actual mapper and readiness callback. Missing/empty/explicit services, null/negative/over-count/zero quantities, price and product eligibility tested. No app change. |
| rtl | Verifier required contiguous minHeight56 text; existing header64 still renders row/ltr and Arabic alignment correctly. | Execute real TSX header/RTL primitives with native boundary stubs; verify style, order and back behavior; LF/CRLF invariance. No app change. |
| product-cost-setup | Old labels/layout describe a replaced four-step screen. One valid expectation remained broken: reviewed product omitted its incomplete-cost badge. | Restore badge for the actual product ID; preserve existing category-only completion and optional base cost. Update stale assertions and execute production callbacks. |
| component-soft-stop | Inactive resources were already excluded from new manual/option/group linking; existing stopped manual links lacked their warning label. | Restore conditional stopped-link label; retain links and costs unchanged. Execute candidate filtering, category, warning and badge behavior. |

Fresh root verification: all15 application verifiers PASS; TypeScript PASS. Tests were not simply weakened: deliberate empty-service fallback, invalid-price, RTL flip/alignment/row order and inactive-link mutations are rejected; original missing badge/label implementations fail the new behavior checks.

Only two JSX display additions in application source. No financial formula, API payload, accounting behavior, operational engine, dependency or permission changed. Isolated APK / Device UAT still in progress; no final readiness claim.

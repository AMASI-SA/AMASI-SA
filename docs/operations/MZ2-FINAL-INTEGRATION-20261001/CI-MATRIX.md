# Exact source CI matrix

Source: `fb688621c99cdf413604f514beffa274e58d73ea` · tree: `5e2dc70ef0b81b7efabf1ffc6b3132cc7ce2a1aa`.
Captured 2026-10-01 03:30:58 UTC. 31 successful, 5 failed, 0 pending out of 36 returned workflows. This snapshot is not a claim about later evidence-only commit checks.

| Workflow | Conclusion | Evidence |
| --- | --- | --- |
| Production Campaign AI Subprocess Worker V1 | success | [Run 36810265283](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265283) |
| Ads Auto Sync 5 Minutes V2 | success | [Run 36810265409](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265409) |
| Qoyod Payment Freshness | success | [Run 36810265350](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265350) |
| Production Fulfillment 278 Port | success | [Run 36810265345](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265345) |
| Production Dashboard Four Platform Spend V1 | success | [Run 36810265304](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265304) |
| Supplier Invoice Service Policy | success | [Run 36810265353](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265353) |
| Mezan MCP Gateway security tests | success | [Run 36810265277](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265277) |
| Mezan Release Readiness | failure | [Run 36810265293](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265293) |
| Production Dashboard V2 Salla Ads Executive | success | [Run 36810265305](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265305) |
| Production Preparation Piece Operations | success | [Run 36810265249](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265249) |
| Components Required Category and Group Picker V2 | success | [Run 36810265419](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265419) |
| Products V2 | success | [Run 36810265371](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265371) |
| Snapchat Integrations V2 | success | [Run 36810265257](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265257) |
| Salla Orders V3 acceptance | success | [Run 36810265312](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265312) |
| Build20 supplier invoice integrity (no deployment) | success | [Run 36810265273](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265273) |
| Build20 supplier scan recovery (no deployment) | success | [Run 36810265414](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265414) |
| Runtime Stability | success | [Run 36810265426](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265426) |
| Fulfillment V2 | success | [Run 36810265318](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265318) |
| Store Delivery V1 | success | [Run 36810265418](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265418) |
| Snapchat Settings Management V2 | success | [Run 36810265631](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265631) |
| Warehouse Location Engine | success | [Run 36810265329](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265329) |
| MZ2 Track G SSOT contracts | failure | [Run 36810265366](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265366) |
| MZ2 Accounting UI Phase 2 | success | [Run 36810265446](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265446) |
| Salla Order Revision P0 contracts | success | [Run 36810265352](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265352) |
| MZ2 advertising V2 native accounting | success | [Run 36810265325](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265325) |
| MZ2 Accounting UI H2 | success | [Run 36810265349](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265349) |
| MZ2 Supplier Payments V2 | success | [Run 36810265448](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265448) |
| Snapchat Reporting V2 Shadow | success | [Run 36810265355](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265355) |
| Employees V2 Foundation | success | [Run 36810265397](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265397) |
| Security Gate | success | [Run 36810265422](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265422) |
| MZ2 A+B source integration | failure | [Run 36810265301](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265301) |
| MZ2 Supplier Native Invoice V2 | failure | [Run 36810265399](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265399) |
| MZ2 Accounting Module | failure | [Run 36810265387](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265387) |
| MZ2 Track F native shipping | success | [Run 36810265455](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265455) |
| G47 Focused Integration | success | [Run 36810265390](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265390) |
| CodeQL | success | [Run 36810265421](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36810265421) |

Failed Accounting Module, A+B, Track G and Supplier Native cases stop at the missing native sale producer (423 Legacy writer disabled). Release Readiness fails reviewed intent/source classification. See VERIFICATION.md and WRITER-BLOCKERS.md for exact jobs, tests and skipped-step limitations. Successful Employees Foundation covers setup; it does not close payroll's native writer gap. Native advertising success does not close bank funding orchestration.

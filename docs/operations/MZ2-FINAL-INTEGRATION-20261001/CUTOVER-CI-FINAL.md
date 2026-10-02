# Complete source-checkpoint CI

HEAD `b24c20c0c33974d7bcb49ca441de8a267e4a685b`; TREE `2a3c79efc2456ec1a1126f81f4799421301784a9`. All 36 workflows completed: **35 successful, 1 failed, 0 pending**. This source checkpoint includes every financial change and the corrected CI runner/import/dependency configuration. Documentation-only descendants preserve the same application/test/workflow sources; their own completion is recorded in the final Issue #1006 handoff.

The only failed workflow is Release Readiness v5 candidate/intent ancestry validation. All Accounting jobs, frontend build, Track F shipping, G47 and A+B connected browser tests succeeded. The separate whole-frontend baseline failures remain an unwaived gate; they are not hidden by these focused CI suites.

| Workflow | Result | Evidence |
|---|---|---|
| Production Campaign AI Subprocess Worker V1 | success | [Run 36856588211](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588211) |
| Ads Auto Sync 5 Minutes V2 | success | [Run 36856588200](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588200) |
| Qoyod Payment Freshness | success | [Run 36856588175](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588175) |
| Production Fulfillment 278 Port | success | [Run 36856588193](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588193) |
| Production Dashboard Four Platform Spend V1 | success | [Run 36856588269](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588269) |
| Components Required Category and Group Picker V2 | success | [Run 36856588319](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588319) |
| Products V2 | success | [Run 36856588154](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588154) |
| MZ2 Supplier Payments V2 | success | [Run 36856588173](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588173) |
| Runtime Stability | success | [Run 36856588333](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588333) |
| MZ2 advertising V2 native accounting | success | [Run 36856588287](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588287) |
| Snapchat Settings Management V2 | success | [Run 36856588228](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588228) |
| Build20 supplier invoice integrity (no deployment) | success | [Run 36856588322](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588322) |
| MZ2 Accounting UI H2 | success | [Run 36856588171](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588171) |
| Snapchat Reporting V2 Shadow | success | [Run 36856588343](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588343) |
| Salla Order Revision P0 contracts | success | [Run 36856588400](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588400) |
| Mezan Release Readiness | failure | [Run 36856588349](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588349) |
| Build20 supplier scan recovery (no deployment) | success | [Run 36856588289](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588289) |
| Supplier Invoice Service Policy | success | [Run 36856588282](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588282) |
| Mezan MCP Gateway security tests | success | [Run 36856588359](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588359) |
| Production Dashboard V2 Salla Ads Executive | success | [Run 36856588209](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588209) |
| Snapchat Integrations V2 | success | [Run 36856588198](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588198) |
| MZ2 Accounting UI Phase 2 | success | [Run 36856588360](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588360) |
| Warehouse Location Engine | success | [Run 36856588463](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588463) |
| Production Preparation Piece Operations | success | [Run 36856588233](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588233) |
| MZ2 Supplier Native Invoice V2 | success | [Run 36856588213](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588213) |
| Store Delivery V1 | success | [Run 36856588250](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588250) |
| Security Gate | success | [Run 36856588222](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588222) |
| MZ2 Track F native shipping | success | [Run 36856588357](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588357) |
| Salla Orders V3 acceptance | success | [Run 36856588340](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588340) |
| G47 Focused Integration | success | [Run 36856588176](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588176) |
| Employees V2 Foundation | success | [Run 36856588316](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588316) |
| Fulfillment V2 | success | [Run 36856588308](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588308) |
| MZ2 Track G SSOT contracts | success | [Run 36856588567](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588567) |
| MZ2 A+B source integration | success | [Run 36856588208](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588208) |
| CodeQL | success | [Run 36856588230](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588230) |
| MZ2 Accounting Module | success | [Run 36856588344](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36856588344) |

Superseded attempts, including fixture-import and shipping-runner/dependency failures, remain in CUTOVER-CI-HISTORY.json. Failed, cancelled and skipped checks are never counted as passes.

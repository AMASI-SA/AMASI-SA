# Snapchat entity amount evidence

Source candidate based on production 7da8b95c5bf79cd001d0714439dc5b7f89e18f31. Branch codex/snapchat-entity-amount-current-20261017. Not deployed. Earlier PR1407/1408 superseded as production advanced; their frozen intent must not be reused. Browser tool initialization currently fails with missing kernel assets, preventing Emergent publish and live verification.

Root cause: real entity performance rows omit amount_complete. Unified adapter maps None and campaign candidate financial_coverage rejects every row even when account gates pass. Actual-reader regression reproduced two healthy-case failures before correction.

Correction: preserve finite nonnegative native amount evidence before aggregation; expose proven TOTAL period coverage; amount_complete requires that period proof, real facts, complete row status, positive finite FX, calculator cost_coverage.complete and finite native/SAR amounts. Candidate gates unchanged. Missing/partial/unknown data never promoted to complete. Valid zero and USD/SAR supported. Latest hourly run alone does not prove historical period.

Verification: 88 tests passed, exit0, across entity amount regression (15 cases), entity performance, period source proof, phase5 evidence, unified cutover, account analysis, shadow diagnostics. Two existing Pydantic deprecation warnings. Independent review identified missing FX coverage and period proof; both addressed with regressions. No ad writes or production changes by this candidate.

Next: review final diff, source CI, freeze intent-only B under release protocol v5, final CI, merge and authorized production release, official verify3, live shadow candidate flow and Saudi period blocker. Preserve existing supplier/bank fixes. Prior PR1403 remains verified production. Do not call Snapchat complete until live evidence succeeds.


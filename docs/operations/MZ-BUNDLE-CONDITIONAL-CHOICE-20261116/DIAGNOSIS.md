# Conditional custom-text option incorrectly blocks bundle cost

## Scope and status

Read-only diagnosis of a bundle cost report that marks an unrequested conditional custom-text option as an unresolved service. No application code, test files, dependencies, live catalog settings, order selections, or provider data were changed. No new merge or deployment was performed for this diagnosis.

Repository: AMASI-SA/AMASI-SA.
Inspected production branch head: 236a428364bc8163a684795ed1be87e237476f18.
Observed live source: 1bd44b6f186de8b40877fde686b5d2d7aab52805.
The inspected head differs from the observed live source only in release/release-intent-v5.json; the inspected runtime code is identical.

## Evidence and cause

The saved order contains an explicit, valid parent answer declining customization. The custom-text field is correctly absent. The saved raw product catalogs retain its on_condition visibility, requiring the opposite parent answer. The normalized product.options row retains required=true but omits all visibility metadata.

backend/current_bundle_reporting_estimate.py::_options prefers nonempty normalized product.options and does not merge the raw visibility metadata. _choice_pairs uses catalog_options for identities but separately uses _options for required-field validation. With no visibility condition in that normalized row, the required custom-text field is treated as always visible. _build_line_cost then generates a root:choice unresolved service cell and a current_bundle_sale_selection_unavailable reason before evaluating any service cost binding. This produces an incomplete bundle total despite available evidence that customization was not requested.

The actual report, saved order decision, both raw catalog conditions, and the normalized row were inspected with bounded reads. Customer data, order/product/option identifiers, amounts, and free text are intentionally omitted here.

This case is separate from the previously completed sparse order-event ingestion repair. The relevant customer decision and condition metadata are already saved. A manual provider refresh does not supply missing evidence for this case.

## Safe correction direction

Use the merged catalog visibility evidence and the shared proof rules for required-option validation in the current bundle estimator. Suppress a required custom-text missing-choice warning only when it is proven hidden by a valid parent selection. Preserve unknown results for missing, conflicting, invalid, removed, or cyclic condition evidence. Preserve explicit normalized visibility precedence. Do not infer not-required solely from required=false or absence. Do not change customer selections, globally zero service prices, alter frozen/actual cost authorities, or remove physical recipe components.

The targeted regression should cover a normalized required text row with no visibility metadata, saved raw conditional visibility, an explicit valid declining parent selection, and no text row. The expected result is no phantom required service and a complete total when all base/component costs are otherwise complete. Missing/conflicting parent evidence must remain incomplete.

## Verification

- git status --short: exit 0, clean inspection checkout before documentation work.
- git log -1 --format='%H %s': exit 0; inspected head recorded above.
- git diff --stat 1bd44b6f186de8b40877fde686b5d2d7aab52805 HEAD: exit 0; only release intent differs.
- Direct inspection of the bundle estimator, shared option identity resolver, cost authority selection, and report UI mapping confirmed the trace above.
- A synthetic in-memory invocation of existing catalog_options and _option_visibility ran with PYTHONDONTWRITEBYTECODE=1 and PYTHONPATH=backend, exit 0. The catalog merged the missing visibility metadata; an explicit declining parent returned False; a missing parent returned None; inputs were unchanged. No test/source file was written.
- Independent review confirmed the same cause. The full estimator import was unavailable in that review because pymongo was not installed. No dependencies were installed and no full integration-suite pass is claimed.

## Release and continuity

Production changed: no. PR/review/CI/merge/release guard: not started for this diagnosis. Existing unrelated production work and the prior completed release were preserved.

The repository-wide Issue #1006 ledger had previously reached its comment limit; do not retry an append solely for this diagnostic checkpoint. This dedicated task branch and its STATUS.json are the recoverable handoff.

Next safe action: explain the confirmed cause and correct expected classification to the user. Any implementation continuation must read this status, verify the recorded source against the current production head, follow code-work guidance, implement the narrowly scoped visibility correction and meaningful regression, and separately follow the repository release protocol before claiming a new deployment.

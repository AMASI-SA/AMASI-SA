# Courier balance consistency

WIP source candidate. Production unchanged; no lease prepared.

Scope: reuse the label's pure COD balance calculation for driver collection, including provider partial-payment evidence when canonical remaining has normalized to zero. Preserve positive canonical balances and paid-order protection. Contradictory or missing provider evidence stays unavailable. Read the canonical currency in driver enrichment. Mobile companion: `AMASI-SA/amasi-mobile`, branch `codex/courier-ui-20261030`.

Evidence: reproduced baseline remaining=0 for total=300, provider paid=50; modified calculation yields 250. Twelve actual pure financial test functions passed when extracted without unrelated route imports. Full pytest collection is currently blocked by missing email-validator; no route integration result claimed. Python compilation and diff whitespace check pass.

Next: install isolated test dependency, rerun the actual payment module tests with route imports, add label regression checks, review source, and follow the complete governed v5 build/release protocol before any authorized server publish. No real order or collection changed. Phone push and Android layout remain separate acceptance checks.

# Supplier dispatch without mandatory product classification

Owner explicitly requested removing the product-classification prerequisite for supplier dispatch on 2026-10-26.

Base production: 3a23b587a218f2b190cf1913e3b0b1d22c175cbd. Task branch: codex/supplier-dispatch-no-product-category-20261026.

Changed backend/supplier_category_service_policy.py: missing, blank, unavailable or ambiguous catalog classification no longer blocks dispatch. Existing explicit category mismatch still blocks. Dispatch route ownership, availability and stage checks remain unchanged. Supplier categories and optional services remain unchanged. No migration or actual business writes.

Regression coverage: backend/tests/test_supplier_category_contract.py plus existing dispatch tests. Command: PYTHONPATH=backend python -m pytest backend/tests/test_supplier_category_contract.py backend/tests/test_preparation_supplier_dispatch.py -q using existing assembly-settings-ci-venv. Exit 0, 64 passed. System Python lacked dependencies; it was not used as successful test evidence.

WIP release: no merge, no new intent B, no publication. Source CI and protocol v5 release gates still required. Previous production publication remains verified. Preview refresh pending coordinated terminal inspection; preserve training DB, balances, sessions and all untracked files. No Production republish for Preview refresh. Before shared changes refresh /app guard status; last coordinated observation active=false.

Preview inspection: backend supervisor uses isolated /app/.worktrees/mz2-supplier-editor-fix-20261025/serve.py backend; frontend uses /app/frontend/scripts/start-governed-runtime.cjs. Git inside that runtime resolves parent /app and does not establish its bundle source identity. Determine bundle and environment contracts before a reversible update. Android task provides terminal execution only under coordinated instructions.

Issue #1006 previously reached comment limit; this task record is the continuation checkpoint. Next: source CI, reviewed release artifact and intent-only B if production publication proceeds; inspect Preview bundle separately before update. Do not deploy old evidence branches or modify training data to bypass the bug.

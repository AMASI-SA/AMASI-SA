"""Architectural safety test — the runtime Qoyod pipeline must NEVER
read from the migration snapshot collections.

Per user spec (2026-02-26):
    Post Go-Live, the SSOT for products and customers is
    **Mezan + Salla**, not the imported Qoyod data. The migration
    page (`qoyod_external_*`, `qoyod_migration_*`) is purely a
    review tool.

This test tokenises each runtime module and checks that none of the
forbidden collection names appears OUTSIDE of comments and string
literals (docstrings often mention the names to clarify why the
runtime does NOT use them). If a future change accidentally couples
the runtime to the migration data via an attribute access like
`db.qoyod_external_products`, CI catches it immediately.
"""
from __future__ import annotations

import io
import pathlib
import tokenize

import pytest


RUNTIME_MODULES = [
    "pipeline.py",
    "worker.py",
    "webhook.py",
    "normalizer.py",
    "business_rules.py",
    "eligibility.py",
    "preflight.py",
    "product_resolver.py",
    "customer_resolver.py",
    "invoice_builder.py",
    "state_machine.py",
    "first_sync_monitor.py",
]

# Collections that belong EXCLUSIVELY to the migration layer.
FORBIDDEN_COLLECTIONS = frozenset({
    "qoyod_external_products",
    "qoyod_external_customers",
    "qoyod_migration_products",
    "qoyod_migration_customers",
})


def _executable_identifier_hits(text: str, names: frozenset[str]) -> list[tuple[int, str]]:
    """Return (line_no, name) for every NAME token that matches one of
    `names`. Comments and string literals are skipped by tokenize."""
    hits: list[tuple[int, str]] = []
    tokens = tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline)
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string in names:
            hits.append((tok.start[0], tok.string))
    return hits


def test_runtime_modules_do_not_reference_migration_collections():
    root = pathlib.Path("/app/backend/integrations/qoyod")
    missing: list[str] = []
    violations: list[tuple[str, str, int]] = []
    for fname in RUNTIME_MODULES:
        path = root / fname
        if not path.exists():
            missing.append(fname)
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, name in _executable_identifier_hits(text, FORBIDDEN_COLLECTIONS):
            violations.append((fname, name, line_no))

    # We expect EVERY runtime module to exist (test is meaningless
    # otherwise — silent pass would let SSOT drift slip through).
    assert not missing, (
        "Runtime modules missing — update RUNTIME_MODULES: " + ", ".join(missing))

    assert not violations, (
        "Runtime modules must NOT reference migration collections "
        "(Mezan + Salla are SSOT post-Go-Live). Violations:\n"
        + "\n".join(f"  {fn}:{ln}  collection={c}"
                    for fn, c, ln in violations))

from pathlib import Path


def test_runtime_has_no_deprecated_expanded_orders_and_v3_items_are_canonical():
    backend = Path(__file__).resolve().parents[1]
    runtime_files = [
        path
        for path in backend.rglob("*.py")
        if "tests" not in path.parts and "research" not in path.parts
    ]
    deprecated_hits = []
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        if "expanded=true" in text or "format=expanded" in text:
            deprecated_hits.append(str(path.relative_to(backend)))

    assert deprecated_hits == []

    gateway = (backend / "salla_orders_v3" / "gateway.py").read_text(encoding="utf-8")
    assert '"/orders/items"' in gateway
    assert '"format": "light"' in gateway
    assert 'ORDERS_PER_PAGE = 30' in gateway


def test_shadow_module_cannot_import_operational_dependents_or_collections():
    backend = Path(__file__).resolve().parents[1]
    source = (backend / "salla_orders_v3" / "shadow.py").read_text(encoding="utf-8")

    for forbidden in (
        "order_review",
        "fulfillment",
        "qoyod",
        "snapchat",
        "integration_inbox",
        "unified_orders",
    ):
        assert forbidden not in source.lower()


def test_v3_package_has_no_operational_collection_mutations():
    backend = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (backend / "salla_orders_v3").glob("*.py")
    )

    for forbidden in (
        "unified_orders.update_",
        "unified_orders.insert_",
        "unified_orders.replace_",
        "integration_inbox.update_",
        "integration_inbox.insert_",
        "integration_inbox.replace_",
    ):
        assert forbidden not in source


def test_disabled_shadow_creates_neither_worker_nor_indexes_at_startup():
    backend = Path(__file__).resolve().parents[1]
    worker = (backend / "salla_orders_v3" / "worker.py").read_text(encoding="utf-8")
    server = (backend / "server.py").read_text(encoding="utf-8")

    assert 'SALLA_ORDERS_V3_SHADOW_ENABLED", "false"' in worker
    assert "if _salla_orders_v3_shadow_enabled():" in server
    guarded = server.split("if _salla_orders_v3_shadow_enabled():", 1)[1]
    assert "await _ensure_salla_orders_v3_indexes(db)" in guarded

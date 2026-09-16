import ast
from pathlib import Path

import pytest

from salla_orders_v3.config import (
    COLLECTION_ALLOWLIST,
    CUTOVER_IMPLEMENTED,
    shadow_collection,
)
from salla_orders_v3.worker import shadow_enabled


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
    config = (backend / "salla_orders_v3" / "config.py").read_text(encoding="utf-8")
    assert '"/orders/items"' in gateway
    assert '"format": "light"' in gateway
    assert 'ORDERS_PER_PAGE = 30' in config
    assert "updated_at_gt" not in gateway


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
    assert ".update_one(" not in source
    assert ".insert_one(" not in source
    assert ".replace_one(" not in source


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
    config = (backend / "salla_orders_v3" / "config.py").read_text(encoding="utf-8")
    server = (backend / "server.py").read_text(encoding="utf-8")

    assert 'SHADOW_ENABLED_ENV = "SALLA_ORDERS_V3_SHADOW_ENABLED"' in config
    assert 'os.environ.get(SHADOW_ENABLED_ENV, "false")' in worker
    assert "if _salla_orders_v3_shadow_enabled():" in server
    guarded = server.split("if _salla_orders_v3_shadow_enabled():", 1)[1]
    assert "await _ensure_salla_orders_v3_indexes(db)" in guarded
    assert "repair_event_job_outbox_once" not in server
    assert "requeue_shadow_job_manually" not in server


def test_v3_config_is_allowlisted_bounded_and_has_no_cutover_switch():
    backend = Path(__file__).resolve().parents[1]
    config = (backend / "salla_orders_v3" / "config.py").read_text(encoding="utf-8")
    diagnostics = (backend / "salla_orders_v3" / "diagnostics.py").read_text(
        encoding="utf-8"
    )

    for collection in (
        "salla_orders_v3_shadow",
        "salla_orders_v3_events",
        "salla_orders_v3_jobs",
        "salla_orders_v3_sync_state",
        "salla_orders_v3_leases",
        "salla_orders_v3_parity_audits",
        "salla_orders_v3_parity_runs",
        "salla_orders_v3_parity_evidence",
    ):
        assert collection in config
    assert "MAX_JOB_ATTEMPTS" in config
    assert "MAX_DISCOVERY_PAGES_PER_RUN" in config
    assert "MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE" in config
    assert "MAX_EVENT_OUTBOX_ATTEMPTS" in config
    assert "EVENT_OUTBOX_BACKOFF_MAX_SECONDS" in config
    assert "EVENT_OUTBOX_BACKOFF_BASE_SECONDS" in config
    assert "EVENT_OUTBOX_ROW_LEASE_SECONDS" in config
    assert "PARITY_EVIDENCE_MAX_AGE_SECONDS" in config
    assert "PARITY_RUN_TTL_SECONDS" in config
    assert "TTL_SECONDS" in config
    assert "CUTOVER_IMPLEMENTED = False" in config
    assert '"cutover_allowed": False' in diagnostics


def test_unknown_v3_environment_switch_fails_closed(monkeypatch):
    monkeypatch.setenv("SALLA_ORDERS_V3_CUTOVER_ENABLED", "true")

    with pytest.raises(RuntimeError, match="not allowlisted"):
        shadow_enabled()


def test_collection_boundary_rejects_operational_storage():
    assert COLLECTION_ALLOWLIST == {
        "salla_orders_v3_shadow",
        "salla_orders_v3_events",
        "salla_orders_v3_jobs",
        "salla_orders_v3_sync_state",
        "salla_orders_v3_leases",
        "salla_orders_v3_parity_audits",
        "salla_orders_v3_parity_runs",
        "salla_orders_v3_parity_evidence",
    }
    assert CUTOVER_IMPLEMENTED is False

    with pytest.raises(RuntimeError, match="not allowlisted"):
        shadow_collection(object(), "unified_orders")


def test_event_to_job_materialization_has_no_non_transactional_fallback():
    backend = Path(__file__).resolve().parents[1]
    ingestion = (backend / "salla_orders_v3" / "ingestion.py").read_text(
        encoding="utf-8"
    )

    assert "with_transaction(transact)" in ingestion
    assert "requires Mongo transaction support" in ingestion
    assert "mongo_session=session" in ingestion


def test_ast_guard_has_no_provider_write_or_direct_operational_mutation():
    backend = Path(__file__).resolve().parents[1]
    mutators = {
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "bulk_write",
        "find_one_and_update",
        "find_one_and_delete",
        "find_one_and_replace",
    }
    provider_write_verbs = {"POST", "PUT", "PATCH", "DELETE"}
    violations = []

    def attribute_parts(node):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return list(reversed(parts))

    for path in (backend / "salla_orders_v3").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in provider_write_verbs:
                violations.append((path.name, node.lineno, node.value))
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in mutators:
                continue
            owner = node.func.value
            parts = attribute_parts(owner)
            if "db" in parts and parts[-1] not in COLLECTION_ALLOWLIST:
                violations.append((path.name, node.lineno, ".".join(parts)))

    assert violations == []


def test_outbox_repair_is_only_inside_the_disabled_v3_worker_lifecycle():
    backend = Path(__file__).resolve().parents[1]
    worker = (backend / "salla_orders_v3" / "worker.py").read_text(
        encoding="utf-8"
    )
    server = (backend / "server.py").read_text(encoding="utf-8")

    assert "_event_outbox_repair_loop" in worker
    task_group = worker.split("async with asyncio.TaskGroup() as tasks:", 1)[1]
    assert "_event_outbox_repair_loop(db)" in task_group
    assert "repair_event_job_outbox_once" not in server

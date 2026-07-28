"""Cross-layer contract tests for the Mezan OS V2 fulfillment workspace."""
from __future__ import annotations

import inspect
from pathlib import Path

import order_review_routes


ROOT = Path(__file__).resolve().parents[2]


def test_pending_review_queue_schedules_safe_incremental_salla_ingestion():
    source = inspect.getsource(order_review_routes.make_order_review_router)

    assert "schedule_salla_auto_sync(db, merchant_id)" in source
    assert "run_orders_sync" not in source
    assert source.index("schedule_salla_auto_sync(db, merchant_id)") < source.index(
        'status_group="under_review"'
    )


def test_review_route_uses_safe_auto_sync_module_and_never_imports_qoyod():
    module_source = inspect.getsource(order_review_routes)

    assert (
        "from salla_integration.auto_sync import schedule_salla_auto_sync"
        in module_source
    )
    assert "integrations.qoyod" not in module_source
    assert "qoyod" not in "\n".join(
        line for line in module_source.splitlines() if line.startswith(("from ", "import "))
    ).casefold()


def test_new_workspace_route_sidebar_and_v2_navigation_are_wired():
    app_source = (ROOT / "frontend/src/App.js").read_text(encoding="utf-8")
    sidebar_source = (ROOT / "frontend/src/components/Sidebar.jsx").read_text(
        encoding="utf-8"
    )
    layout_source = (ROOT / "frontend/src/components/Layout.jsx").read_text(
        encoding="utf-8"
    )

    assert 'import FulfillmentV2 from "./pages/FulfillmentV2";' in app_source
    assert 'path="/fulfillment-v2"' in app_source
    assert (
        '<Navigate to="/fulfillment-v2?stage=pending_review" replace />'
        in app_source
    )
    assert 'to: "/fulfillment-v2"' in sidebar_source
    assert 'label: "إدارة التجهيز"' in sidebar_source
    assert 'testid: "nav-mezan-os-fulfillment"' in sidebar_source
    assert '{ to: "/fulfillment-v2", label: "إدارة التجهيز", Icon: Queue }' in layout_source
    assert '"/fulfillment-v2"' in layout_source


def test_owner_sees_one_preparation_entry_while_employee_review_access_is_preserved():
    sidebar_source = (ROOT / "frontend/src/components/Sidebar.jsx").read_text(
        encoding="utf-8"
    )

    # The legacy route remains in the operations definition for non-owner staff.
    assert '{ to: "/order-review", label: "بانتظار المراجعة"' in sidebar_source
    # Owners see the organized Mezan OS parent only, without a duplicate link.
    assert (
        'const items = section.items.filter((item) => item.to !== "/order-review");'
        in sidebar_source
    )

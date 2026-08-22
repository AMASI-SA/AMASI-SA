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


def test_parent_mezan_tab_stays_active_while_a_nested_stage_query_is_selected():
    layout_source = (ROOT / "frontend/src/components/Layout.jsx").read_text(
        encoding="utf-8"
    )

    assert (
        "const hasSpecificChild = V2_LINKS.some((item) => "
        "item.to.startsWith(`${pathname}?`));"
        in layout_source
    )
    assert "!hasSpecificChild || !location.search" in layout_source


def test_owner_sees_one_preparation_entry_while_employee_review_access_is_preserved():
    sidebar_source = (ROOT / "frontend/src/components/Sidebar.jsx").read_text(
        encoding="utf-8"
    )

    assert '{ to: "/order-review", label: "بانتظار المراجعة"' in sidebar_source
    assert (
        'const items = section.items.filter((item) => item.to !== "/order-review");'
        in sidebar_source
    )


def test_pending_review_table_uses_ten_row_cursor_pages_with_arrow_navigation():
    source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")

    assert "export const REVIEW_PAGE_SIZE = 10;" in source
    assert "limit: REVIEW_PAGE_SIZE" in source
    assert "const [currentCursor, setCurrentCursor]" in source
    assert "const [previousCursors, setPreviousCursors]" in source
    assert 'aria-label="الصفحة السابقة"' in source
    assert 'aria-label="الصفحة التالية"' in source
    assert "الصفحة {pageNumber}" in source
    assert "تحميل طلبات إضافية" not in source
    assert "append: true" not in source


def test_pending_review_search_is_global_exact_and_read_only():
    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(
        encoding="utf-8"
    )
    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(
        encoding="utf-8"
    )
    backend_source = (ROOT / "backend/order_review_routes.py").read_text(
        encoding="utf-8"
    )

    assert 'search: Optional[str] = Query(default=None, max_length=50)' in backend_source
    assert 'allow_auto_fulfillment=False' in backend_source
    assert 'params.search = String(search).trim()' in service_source
    assert 'search: query' in frontend_source
    assert 'requestId !== latestRequestId.current' in frontend_source
    assert 'if (searchQuery || document.hidden || !navigator.onLine) return;' in frontend_source
    assert 'ابحث برقم الطلب في جميع طلبات انتظار المراجعة' in frontend_source
    assert 'لا توجد نتائج في هذه الصفحة' not in frontend_source


def test_order_review_product_cards_keep_titles_and_specs_readable():
    source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")

    assert 'data-testid="order-review-product-card"' in source
    assert 'grid-cols-[96px_minmax(0,1fr)]' in source
    assert 'sm:grid-cols-[128px_minmax(0,1fr)]' in source
    assert 'data-testid="order-review-product-specs" className="grid gap-2"' in source
    assert 'data-testid="order-review-products-grid" className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3"' in source
    assert 'md:max-w-7xl' in source
    assert 'sm:grid-cols-[150px_minmax(0,1fr)]' not in source
    assert 'grid gap-4 xl:grid-cols-3' not in source


def test_salla_refresh_is_centralized_in_orders_v2():
    review_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")
    routes_source = (ROOT / "backend/order_engine/routes.py").read_text(encoding="utf-8")
    refresh_source = (ROOT / "backend/order_engine/salla_refresh.py").read_text(encoding="utf-8")
    service_source = (ROOT / "frontend/src/services/orderEngine.js").read_text(encoding="utf-8")
    details_source = (ROOT / "frontend/src/pages/OrderDetailsV2.jsx").read_text(encoding="utf-8")

    assert "from order_engine.salla_refresh import refresh_order_from_salla" in review_source
    assert "resync_single_order" not in review_source
    assert "_refresh_review_source_once" not in review_source
    assert '"/{order_number}/refresh-from-salla"' in routes_source
    assert 'f"/orders/{internal_id}"' in refresh_source
    assert '"/orders/items"' in refresh_source
    assert '"/shipments"' not in refresh_source
    assert "no_shipments_api_calls" in refresh_source
    assert "refreshOrderFromSalla" in service_source
    assert 'data-testid="order-v2-refresh-from-salla"' in details_source


def test_orders_v2_shipping_card_shows_complete_order_address():
    source = (ROOT / "frontend/src/pages/OrderDetailsV2.jsx").read_text(encoding="utf-8")

    assert '["العنوان", address.formatted || address.address_line' in source
    assert 'تفاصيل العنوان لم تصل من سلة' in source


def test_carrier_handoff_is_barcode_governed_and_released_by_orders_page_sync():
    backend_source = (ROOT / "backend/fulfillment_v2_routes.py").read_text(
        encoding="utf-8"
    )
    salla_sync_source = (
        ROOT / "backend/salla_integration/sync.py"
    ).read_text(encoding="utf-8")
    frontend_source = (
        ROOT / "frontend/src/components/fulfillment/CompletedFulfillmentOrders.jsx"
    ).read_text(encoding="utf-8")

    assert '"/completed/{order_number}/carrier-label/confirm-print"' in backend_source
    assert '"/carrier-handoff/scan"' in backend_source
    assert '"carrier_handoff_state": "with_handoff_employee"' in (
        ROOT / "backend/carrier_handoff.py"
    ).read_text(encoding="utf-8")
    assert "advance_carrier_handoff_from_salla_status" in salla_sync_source
    assert 'source="mezan_orders_page_status_sync"' in salla_sync_source
    assert '"order_status": order.status' in backend_source
    assert "carrier_handoff_custody_is_visible(" in backend_source
    assert 'data-testid="confirm-carrier-label-print"' in frontend_source
    assert 'data-testid="open-carrier-handoff-scanner"' in frontend_source
    assert "setInterval" in frontend_source


def test_delivery_tracking_board_reads_mezan_state_and_keeps_store_courier_separate():
    backend_source = (ROOT / "backend/fulfillment_v2_routes.py").read_text(
        encoding="utf-8"
    )
    stage_source = (ROOT / "frontend/src/pages/FulfillmentV2.jsx").read_text(
        encoding="utf-8"
    )
    tracking_source = (
        ROOT / "frontend/src/components/fulfillment/DeliveryTrackingOrders.jsx"
    ).read_text(encoding="utf-8")

    assert '@router.get("/delivery-tracking")' in backend_source
    assert '"carrier_label_type": {"$ne": "store_courier"}' in backend_source
    assert '"sync_source": "mezan_orders_page_status_sync"' in backend_source
    assert "activeStage.key === \"delivering\" || activeStage.key === \"delivered\"" in stage_source
    assert "تتغير هذه المرحلة فقط بعد مزامنة الحالة من صفحة الطلبات في ميزان" in tracking_source
    assert "طلبات مندوب المتجر لها مسار مستقل" in tracking_source


def test_review_supports_internal_operational_items_without_supplier_export():
    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")
    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")

    assert '"/{order_number}/operational-items"' in backend_source
    assert '"item_type": "internal_operational"' in backend_source
    assert '"supplier_export": False' in backend_source
    assert '"financial_item": False' in backend_source
    assert '"salla_product": False' in backend_source
    assert '"blocks_order_completion": True' in backend_source
    assert 'createOrderReviewOperationalItem' in service_source
    assert 'data-testid="order-review-operational-item"' in frontend_source
    assert 'إضافة منتج تشغيلي' in frontend_source
    assert 'function imageIdentity(value)' in frontend_source
    assert 'const sourceGallery = (item.gallery || []).filter(Boolean);' in frontend_source
    assert 'identity === selectedIdentity' in frontend_source
    assert 'seenImageIdentities.has(identity)' in frontend_source
    assert 'setVisibleSelectedImage(url)' in frontend_source


def test_reviewed_stage_and_salla_admin_link_are_active():
    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")
    review_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")
    stage_source = (ROOT / "frontend/src/pages/FulfillmentV2.jsx").read_text(encoding="utf-8")
    reviewed_source = (ROOT / "frontend/src/pages/ReviewedOrders.jsx").read_text(encoding="utf-8")
    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")

    assert '@router.get("/reviewed")' in backend_source
    assert 'raw_by_source.salla_direct.urls.admin' in backend_source
    assert 'data-testid="order-review-open-in-salla"' in review_source
    assert 'activeStage.key === "reviewed"' in stage_source
    assert 'data-testid="reviewed-orders-stage"' in reviewed_source
    assert 'listReviewedOrderReviews' in service_source


def test_operational_item_waits_for_assembly_and_moves_specs_out_of_supplier_export():
    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")

    assert '"preparation_status": "pending"' in backend_source
    assert 'operational_status_managed_in_assembly' in backend_source
    assert 'supplier_export_excluded_spec_keys' in backend_source
    assert 'moved_to_operational_item_ids' in backend_source
    assert 'existing_excluded.update(seen_specs)' in backend_source
    assert 'بانتظار التجميع والعنونة' in frontend_source
    assert '>قيد التجهيز</button>' not in frontend_source
    assert '>جاهز</button>' not in frontend_source


def test_operational_item_can_be_renamed_or_unlinked_before_review_completion():
    backend_source = (ROOT / "backend/order_review_routes.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")
    service_source = (ROOT / "frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")
    assert '@router.delete("/{order_number}/operational-items/{operational_item_id:path}")' in backend_source
    assert 'supplier_export_excluded_spec_keys' in backend_source
    assert 'operational_item_unlinked' in backend_source
    assert 'unlinkOrderReviewOperationalItem' in service_source
    assert 'order-review-operational-item-unlink' in frontend_source
    assert 'إلغاء الربط وإرجاع القيم' in frontend_source

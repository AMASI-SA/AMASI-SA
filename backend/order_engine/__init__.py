"""Mezan OS Order Engine.

This package owns the canonical order contract used by future Mezan
workspaces and engines.
It must not expose MongoDB-shaped documents to frontend consumers.
"""

# Payment-method freshness policy
import orders_db as _orders_db

_orders_db.CRITICAL_FIELDS.add("payment_method")

from .mapper import OrderMappingError, map_salla_order
from .repository import MongoOrderRepository, OrderDiscoveryRow, OrderRepository
from . import routes as _routes
from .routes import OrderListResponse
from .service import InvalidOrderCursorError, OrderNotFoundError, OrderPage, get_order, list_orders
from .models import (
    AddressDTO, CustomerDTO, MoneyTotalsDTO, OrderDTO, OrderItemDTO,
    OrderSourceDTO, PaymentDTO, ShippingDTO,
)

_original_make_order_engine_router = _routes.make_order_engine_router


def _route_keys(route):
    """Return unique (path, method) keys for an APIRoute."""
    path = getattr(route, "path", None)
    methods = getattr(route, "methods", None) or {None}
    return {(path, method) for method in methods}


def make_order_engine_router(*args, **kwargs):
    """Build Order Engine plus independent Mezan OS operations engines."""
    router = _original_make_order_engine_router(*args, **kwargs)
    db = args[0] if args else kwargs["db"]
    current_user = args[1] if len(args) > 1 else kwargs["current_user"]

    from warehouse_location_routes import make_warehouse_location_router
    from warehouse_location_v2_routes import make_warehouse_location_v2_router
    from warehouse_room_routes import make_warehouse_room_router
    from warehouse_reset_routes import make_warehouse_reset_router
    from product_v2_workspace_routes import make_product_v2_workspace_router
    from product_v2_creation_order_routes import make_product_v2_creation_order_router
    from product_creation_routes import make_product_creation_router
    from product_v2_details_routes import make_product_v2_details_router
    from product_v2_source_authority import install_product_source_authority
    from product_field_cost_support import install_product_field_cost_support
    from product_sale_schedule_support import install_product_sale_schedule_support
    from product_category_publish_support import install_product_category_publish_support
    from product_main_image_dedupe_support import install_product_main_image_dedupe_support
    from product_category_variant_support import (
        install_product_category_variant_support,
        make_product_category_catalog_router,
    )
    from product_v2_recent_sync_routes import make_product_v2_recent_sync_router
    from component_category_required_routes import (
        make_component_category_required_router,
    )
    from component_workspace_cost_compat_routes import make_component_workspace_cost_compat_router
    from component_edit_routes import make_component_edit_router
    from product_option_cost_routes import make_product_option_cost_router
    from product_group_link_routes import make_product_group_link_router
    from product_cost_setup_routes import make_product_cost_setup_router
    from product_fulfillment_routes import make_product_fulfillment_router
    from order_option_cost_snapshot_routes import make_order_option_cost_snapshot_router
    from fulfillment_v2_routes import make_fulfillment_v2_router
    from order_review_export_controls import (
        make_order_review_export_controls_router,
    )
    from order_review_spec_replacements import (
        make_order_review_spec_replacements_router,
    )
    from order_review_mezan_image_unlink import (
        make_order_review_mezan_image_unlink_router,
    )
    from order_review_customer_waiting import (
        make_order_review_customer_waiting_router,
    )
    from order_review_forward_stage_guard import (
        install_order_review_forward_stage_guard,
    )
    from reviewed_products_catalog import make_reviewed_products_catalog_router
    from reviewed_product_sorting import (
        make_reviewed_product_sorting_router,
    )
    from reviewed_preparation_batches import (
        make_reviewed_preparation_batches_router,
    )
    from mobile_reviewed_preparation_routes import (
        make_mobile_reviewed_preparation_router,
    )
    from preparation_file_registry import (
        make_preparation_file_registry_router,
    )
    from preparation_piece_operations import (
        install_preparation_piece_operations,
        make_preparation_piece_operations_router,
    )
    from preparation_supplier_dispatch import (
        make_preparation_supplier_dispatch_router,
    )
    from preparation_piece_line_services import (
        install_preparation_piece_line_services,
    )
    from preparation_piece_execution_guard import (
        install_preparation_piece_execution_guard,
    )
    from supplier_receiving_routes import make_supplier_receiving_router
    from fulfillment_experiment_routes import (
        make_fulfillment_experiment_router,
    )
    from mezan_supplier_management_routes import (
        make_mezan_supplier_management_router,
    )
    from preparation_file_failure_safety import (
        install_preparation_finalize_safety,
        make_preparation_file_failure_safety_router,
    )
    from product_inventory_receipt_routes import (
        make_product_inventory_receipt_router,
    )
    from stock_preparation_order_routes import (
        make_stock_preparation_order_router,
    )
    from salla_inventory_sync_routes import (
        make_salla_inventory_sync_router,
    )
    from product_control_center_routes import make_product_control_center_router
    from product_media_draft_routes import make_product_media_draft_router
    from product_media_upload_routes import make_product_media_upload_router
    from product_media_ai_gpt2_compat_routes import make_product_media_ai_gpt2_compat_router
    from product_media_ai_routes import make_product_media_ai_router
    from product_media_ai_draft_routes import make_product_media_ai_draft_router
    from product_media_ai_execution_support import install_product_media_ai_execution_support
    from ai_store_operations_foundation import make_ai_store_operations_router
    from ai_store_access_control import make_ai_store_access_router
    from product_google_taxonomy_ai_visual_gate import make_product_google_taxonomy_ai_pilot_router
    from product_google_taxonomy_salla_publish import make_product_google_taxonomy_salla_publish_router
    from product_google_taxonomy_merchant_feed import make_product_google_taxonomy_merchant_feed_router

    import product_v2_routes as _product_v2_routes
    from product_v2_sync_hotfix import run_product_v2_sync_fixed

    install_order_review_forward_stage_guard()
    install_preparation_piece_operations()
    install_preparation_piece_line_services()
    install_preparation_piece_execution_guard()
    install_preparation_finalize_safety()
    _product_v2_routes.run_product_v2_sync = run_product_v2_sync_fixed
    install_product_source_authority()
    install_product_field_cost_support()
    install_product_sale_schedule_support()
    install_product_category_publish_support()
    install_product_main_image_dedupe_support()
    install_product_category_variant_support()
    install_product_media_ai_execution_support()
    make_product_v2_router = _product_v2_routes.make_product_v2_router

    child_routers = [
        make_warehouse_location_router(db, current_user),
        make_warehouse_location_v2_router(db, current_user),
        make_warehouse_room_router(db, current_user),
        make_warehouse_reset_router(db, current_user),
        make_ai_store_operations_router(db, current_user),
        make_product_google_taxonomy_ai_pilot_router(db, current_user),
        make_product_google_taxonomy_salla_publish_router(db, current_user),
        make_product_google_taxonomy_merchant_feed_router(db, current_user),
        make_ai_store_access_router(db, current_user),
        make_product_media_upload_router(db, current_user),
        # Static Products V2 routes must be registered before
        # /products-v2/{product_id}; otherwise FastAPI interprets
        # "category-catalog" as a product id and the selector stays empty.
        make_product_category_catalog_router(db, current_user),
        make_product_v2_recent_sync_router(db, current_user),
        make_product_creation_router(db, current_user),
        make_product_v2_creation_order_router(db, current_user),
        make_product_v2_workspace_router(db, current_user),
        make_product_control_center_router(db, current_user),
        make_product_media_draft_router(db, current_user),
        # Register the GPT Image 2-compatible execute route before the legacy
        # route so the duplicate-key guard keeps the compatible implementation.
        make_product_media_ai_gpt2_compat_router(db, current_user),
        make_product_media_ai_router(db, current_user),
        make_product_media_ai_draft_router(db, current_user),
        make_product_v2_router(db, current_user),
        make_product_v2_details_router(db, current_user),
        # Required-category writes must be registered before the older component
        # routes so create/edit can never save an unclassified item.
        make_component_category_required_router(db, current_user),
        # This GET route must be registered before the older workspace route.
        # It exposes the current cost consistently for legacy and new rows so
        # the component edit modal is always pre-filled.
        make_component_workspace_cost_compat_router(db, current_user),
        make_component_edit_router(db, current_user),
        # Product cost setup owns the canonical operations GET so web/mobile
        # receive the product classification and explicit completion state.
        make_product_cost_setup_router(db, current_user),
        # Group-aware resource/group writes remain the canonical mutation paths.
        make_product_group_link_router(db, current_user),
        make_product_fulfillment_router(db, current_user),
        make_product_option_cost_router(db, current_user),
        make_order_option_cost_snapshot_router(db, current_user),
        make_stock_preparation_order_router(db, current_user),
        make_product_inventory_receipt_router(db, current_user),
        make_salla_inventory_sync_router(db, current_user),
        make_order_review_export_controls_router(db, current_user),
        make_order_review_spec_replacements_router(db, current_user),
        make_order_review_mezan_image_unlink_router(db, current_user),
        make_order_review_customer_waiting_router(db, current_user),
        make_reviewed_products_catalog_router(db, current_user),
        make_reviewed_product_sorting_router(db, current_user),
        make_mobile_reviewed_preparation_router(db, current_user),
        make_reviewed_preparation_batches_router(db, current_user),
        make_preparation_file_registry_router(db, current_user),
        make_preparation_file_failure_safety_router(db, current_user),
        make_preparation_piece_operations_router(db, current_user),
        make_preparation_supplier_dispatch_router(db, current_user),
        make_mezan_supplier_management_router(db, current_user),
        make_fulfillment_experiment_router(db, current_user),
        make_supplier_receiving_router(db, current_user),
        make_fulfillment_v2_router(db, current_user),
    ]
    existing_keys = set()
    for route in router.routes:
        existing_keys.update(_route_keys(route))

    for child_router in child_routers:
        for route in child_router.routes:
            route_keys = _route_keys(route)
            if route_keys.isdisjoint(existing_keys):
                router.routes.append(route)
                existing_keys.update(route_keys)
    return router


_routes.make_order_engine_router = make_order_engine_router

__all__ = [
    "AddressDTO", "CustomerDTO", "MoneyTotalsDTO", "OrderDTO", "OrderItemDTO",
    "OrderSourceDTO", "PaymentDTO", "ShippingDTO", "OrderMappingError",
    "map_salla_order", "InvalidOrderCursorError", "OrderNotFoundError",
    "OrderPage", "get_order", "list_orders", "MongoOrderRepository",
    "OrderDiscoveryRow", "OrderRepository", "OrderListResponse",
    "make_order_engine_router",
]

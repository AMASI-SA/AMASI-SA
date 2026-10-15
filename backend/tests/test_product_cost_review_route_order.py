from order_engine import make_order_engine_router


def test_cost_review_route_precedes_dynamic_product_route():
    router = make_order_engine_router(object(), lambda: None)
    routes = [getattr(route, "path", "") for route in router.routes]

    cost_review = routes.index("/products-v2/cost-review")
    dynamic_product = routes.index("/products-v2/{product_id}")

    assert cost_review < dynamic_product

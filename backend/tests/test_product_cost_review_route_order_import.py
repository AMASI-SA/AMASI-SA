def test_cost_review_route_is_registered_before_dynamic_product_route():
    from product_field_cost_support import install_product_field_cost_support
    import product_v2_routes

    install_product_field_cost_support()
    router = product_v2_routes.make_product_v2_router(object(), lambda: None)
    paths = [getattr(route, "path", "") for route in router.routes]

    assert paths.index("/products-v2/cost-review") < paths.index("/products-v2/{product_id}")

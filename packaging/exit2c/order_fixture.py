"""Two declared synthetic orders shared by the seed and HTTP simulator."""
ORDERS = ("EXIT2D-1001", "EXIT2D-1002")
ORDER_IDS = frozenset("raw-" + number for number in ORDERS)


def product_image_urls(product_id):
    allowed = {f"exit2d-product-{n}-{i}" for n in range(2) for i in range(2)}
    if product_id not in allowed:
        raise ValueError("ORDER_FIXTURE_ID_REJECTED")
    return tuple("http://127.0.0.1:8001/api/order-reviews-v1/mezan-images/" + product_id + "-" + v for v in ("a", "b"))


def order_fixture(internal_id):
    if internal_id not in ORDER_IDS:
        raise ValueError("ORDER_FIXTURE_ID_REJECTED")
    number = internal_id[4:]
    number_index = ORDERS.index(number)
    items = []
    for item_index in range(2):
        pid = f"exit2d-product-{number_index}-{item_index}"
        images = product_image_urls(pid)
        name = "منتج اصطناعي " + pid
        items.append({"id": f"exit2d-item-{number_index}-{item_index}", "quantity": 2, "name": name,
            "product": {"id": pid, "name": name, "sku": pid, "main_image": images[0],
                        "images": [{"url": u} for u in images]},
            "options": [{"name": "اللون", "value": ("ذهبي", "فضي")[item_index]},
                        {"name": "النقش", "value": ("نور", "أمل")[number_index]}],
            "amounts": {"price_without_tax": {"amount": 10, "currency": "SAR"}}})
    return {"id": internal_id, "reference_id": number, "date": "2026-09-07T00:00:00Z",
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "customer": {"full_name": "عميل اختبار", "email": "customer@example.test"},
        "items": items, "amounts": {"total": {"amount": 40, "currency": "SAR"}}}

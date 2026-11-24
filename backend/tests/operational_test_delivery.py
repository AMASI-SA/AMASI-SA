"""Explicit normalized native delivery evidence for isolated reducer fixtures."""


def native_delivery(order, at):
    """Declare the scenario's actual delivery event, never its refresh clock."""
    carrier = order["carrier"]
    carrier.setdefault("party_type", carrier.get("kind", "courier"))
    order.setdefault("order_number", "test-order-" + str(order["id"]))
    order.update(status="delivered", delivered_at=at)
    order["delivery_event"] = {"schema": "operational_delivery_event_v1", "id": "test-native:" + str(order["id"]),
        "order_id": str(order["id"]), "order_number": order["order_number"], "order_created_at": order["created_at"],
        "delivered_at": at, "currency": order["currency"], "party_id": carrier["id"], "party_type": carrier["party_type"]}
    return order

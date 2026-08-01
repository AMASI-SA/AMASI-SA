from types import SimpleNamespace

from order_review_customer_waiting import (
    PENDING_REVIEW_STAGE,
    WAITING_CUSTOMER_REVIEW_STAGE,
    _stage_transition_document,
    customer_waiting_summary,
    make_order_review_customer_waiting_router,
)


class FakeOrder:
    order_number = "275678403"
    order_id = "order-1"

    def model_dump(self, mode="json"):
        return {
            "order_number": self.order_number,
            "order_id": self.order_id,
            "customer": {"name": "عميل"},
        }


def test_waiting_transition_preserves_review_data_and_increments_revision():
    workflow = {
        "user_id": "owner-1",
        "order_number": "275678403",
        "order_id": "order-1",
        "stage": PENDING_REVIEW_STAGE,
        "revision": 4,
        "items": [{"order_item_id": "item-1", "preparation_note": "احفظها"}],
        "operational_items": [{"operational_item_id": "op-1"}],
    }
    next_doc = _stage_transition_document(
        workflow=workflow,
        user_id="owner-1",
        order=FakeOrder(),
        stage=WAITING_CUSTOMER_REVIEW_STAGE,
        actor={"id": "employee-1", "name": "موظف المراجعة"},
    )

    assert next_doc["stage"] == WAITING_CUSTOMER_REVIEW_STAGE
    assert next_doc["revision"] == 5
    assert next_doc["items"] == workflow["items"]
    assert next_doc["operational_items"] == workflow["operational_items"]
    assert next_doc["waiting_customer_review_by"] == "employee-1"
    assert next_doc["waiting_customer_review_by_name"] == "موظف المراجعة"


def test_resume_transition_returns_to_pending_without_deleting_waiting_history():
    workflow = {
        "user_id": "owner-1",
        "order_number": "275678403",
        "order_id": "order-1",
        "stage": WAITING_CUSTOMER_REVIEW_STAGE,
        "revision": 2,
        "items": [],
        "waiting_customer_review_at": "2026-08-01T20:00:00+00:00",
    }
    next_doc = _stage_transition_document(
        workflow=workflow,
        user_id="owner-1",
        order=FakeOrder(),
        stage=PENDING_REVIEW_STAGE,
        actor={"id": "employee-2", "email": "review@amasi.sa"},
    )

    assert next_doc["stage"] == PENDING_REVIEW_STAGE
    assert next_doc["revision"] == 3
    assert next_doc["waiting_customer_review_at"] == workflow[
        "waiting_customer_review_at"
    ]
    assert next_doc["customer_review_resumed_by"] == "employee-2"


def test_waiting_summary_exposes_countable_stage_and_revision():
    summary = customer_waiting_summary(
        FakeOrder(),
        {
            "revision": 7,
            "waiting_customer_review_at": "2026-08-01T20:00:00+00:00",
            "waiting_customer_review_by_name": "موظف",
        },
    )
    assert summary["order_number"] == "275678403"
    assert summary["stage"] == WAITING_CUSTOMER_REVIEW_STAGE
    assert summary["revision"] == 7


def test_customer_waiting_router_registers_list_wait_and_resume_routes():
    router = make_order_review_customer_waiting_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert ("/order-review-customer-waiting-v1", "GET") in routes
    assert (
        "/order-review-customer-waiting-v1/{order_number}/wait",
        "POST",
    ) in routes
    assert (
        "/order-review-customer-waiting-v1/{order_number}/resume",
        "POST",
    ) in routes

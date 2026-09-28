from dataclasses import replace
import pathlib
import pytest

from mezan_special_orders.mz2_v2_port import V2Event, V2Leg, V2PortError


def leg(amount=12001, **extra):
    return V2Leg(**(dict(key="cash", entity_type="bank", entity_id="bank-test", side="debit", amount_minor=amount, entry_type="payment") | extra))


def event(**extra):
    return V2Event(**(dict(owner="owner-test", order_id="order-test", event_id="event-test", purpose="replacement",
        effective_at="2026-09-28T12:00:00+03:00", evidence_digest="a"*64, policy_digest="b"*64,
        legs=(leg(), leg(key="receivable", entity_type="customer", side="credit"))) | extra))


@pytest.mark.parametrize("amount", [1, 99, 100, 12001, 10**14])
def test_exact_two_decimal_string(amount):
    p=leg(amount).payload()
    assert p["amount"] == f"{amount//100}.{amount%100:02d}"
    assert isinstance(p["amount"], str)


@pytest.mark.parametrize("amount", [True,False,0,-1,1.0,"1",None,10**14+1])
def test_reject_non_integer_positive_minor(amount):
    with pytest.raises(V2PortError): leg(amount)


@pytest.mark.parametrize("purpose", ["replacement","gift","creator","marketing"])
def test_purposes_preserved_without_fake_sale(purpose):
    p=event(purpose=purpose).payload()
    assert p["metadata"]["purpose"]==purpose
    assert p["txn_type"]=="special_order_event"
    assert "operation_id" not in p["metadata"] and "idempotency_key" not in p["metadata"]
    assert p["effective_at"]=="2026-09-28T09:00:00.000000Z"


def test_payload_copy_sort_and_same_key_different_content():
    e=event(); p=e.payload()
    assert replace(e, legs=tuple(reversed(e.legs))).payload()==p
    p["entries"][0]["amount"]="999.99"
    assert e.payload()["entries"][0]["amount"]=="120.01"
    changed=replace(e,evidence_digest="c"*64).payload()
    assert changed["idempotency_key"]==e.payload()["idempotency_key"]
    assert changed["metadata"]!=e.payload()["metadata"]


@pytest.mark.parametrize("field,value", [("owner","other"),("order_id","other"),("event_id","other")])
def test_event_key_namespace(field,value):
    assert event(**{field:value}).payload()["idempotency_key"] != event().payload()["idempotency_key"]


@pytest.mark.parametrize("extra", [dict(legs=(leg(),leg(key="bad",side="credit",amount_minor=100))),
    dict(legs=(leg(),leg(side="credit"))),dict(legs=[leg(),leg(key="second",side="credit")]),dict(purpose="sale"),
    dict(effective_at="2026-09-28"),dict(evidence_digest="fake"),dict(policy_digest=True),dict(owner={"$ne":None})])
def test_strict_plan(extra):
    with pytest.raises(V2PortError): event(**extra)


def test_no_route_registration_or_fallback_imports():
    import ast
    import mezan_special_orders.mz2_v2_port as module
    tree=ast.parse(pathlib.Path(module.__file__).read_text())
    imports={node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
    assert "ledger_core" not in imports and "accounting_ledger_v2" in imports
    assert not any(isinstance(node, ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {"APIRouter","FastAPI"} for node in ast.walk(tree))

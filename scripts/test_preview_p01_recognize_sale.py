import copy
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("recognize", Path(__file__).with_name("preview_p01_recognize_sale.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

@pytest.fixture
def evidence():
    return dict(owner="owner", provider="emkan", cutoff="2026-02-12T00:00:00Z",
        entry=dict(id="event", user_id="owner", provider="emkan", file_id="file",
            matched=True, event_type="sale", order_number="123",
            provider_order_id="11111111-1111-4111-8111-111111111111",
            actual_gross_amount=100, settlement_date="2026-09-17"),
        order=dict(user_id="owner", order_number="123", order_id="123",
            payment_method="EmkanInstallment", order_status="تم التنفيذ", currency="SAR",
            total_amount=100, order_date="2026-09-10",
            raw_by_source={"excel":{"original_currency":"SAR","original_total_amount":"100.00"}}),
        statement=dict(id="file", user_id="owner", provider="emkan", file_hash="hash", currency="SAR"))

def test_original_principal_and_stable_provider_identity(evidence):
    a=m.normalize_evidence(**evidence); b=m.normalize_evidence(**copy.deepcopy(evidence))
    assert a["amount"] == 100
    assert a["provider_id"] == evidence["entry"]["provider_order_id"]
    assert a["id"] == b["id"] and a["evidence_sha256"] == b["evidence_sha256"]
    assert a["status_source"] == "provider_statement_sale_evidence"

@pytest.mark.parametrize("target,key,value", [
    ("order","user_id","other"),("statement","user_id","other"),
    ("entry","provider","tamara"),("entry","matched",False),
    ("entry","event_type","refund"),("entry","actual_gross_amount",99.99),
    ("order","total_amount",100.01),("order","payment_method","mada"),
    ("order","order_status","ملغي"),("order","currency","USD"),
    ("entry","actual_partial_refund_amount",1),
    ("entry","provider_order_id",""),("entry","settlement_date","2026-09-01"),
    ("order","order_date","2025-12-01")])
def test_unproved_evidence_rejected(evidence,target,key,value):
    evidence[target][key]=value
    with pytest.raises(ValueError):m.normalize_evidence(**evidence)

def test_missing_cutoff_is_not_invented(evidence):
    evidence["cutoff"]=""
    with pytest.raises(ValueError):m.normalize_evidence(**evidence)

def test_partial_or_reversed_prior_group_blocks_retry(evidence):
    txn=m.normalize_evidence(**evidence)
    with pytest.raises(ValueError):m.verify_group([],txn)
    rows=[dict(txn_group_id="g",entity_type="payment_gateway",entity_id="emkan",
               sub_account="receivable",side="debit",status="posted",entry_type="bnpl_sale",amount=100),
          dict(txn_group_id="g",entity_type="revenue",entity_id="bnpl_sales",
               side="credit",status="posted",entry_type="bnpl_sale",amount=100)]
    assert m.verify_group(rows,txn)=="g"
    with pytest.raises(ValueError):m.verify_group(rows[:1],txn)
    rows[0]["status"]="reversed"
    with pytest.raises(ValueError):m.verify_group(rows,txn)

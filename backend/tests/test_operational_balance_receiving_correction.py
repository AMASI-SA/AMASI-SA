"""No real invoices: a proposal is a model of gross payable legs, never cash."""
from copy import deepcopy

import pytest
from test_supplier_native_invoice_v2 import env, OWNER, identities, mapping, invoice as native_invoice, post
from test_operational_balance_supplier_invoice_preview import contents, request
from tests.test_operational_balance_receiving_coverage import state, invoice, save
from operational_receiving_coverage import compare_receiving
from operational_receiving_correction import component_proposal


def fixture(typ='wrong_currency'):
    s=state(); s.update(openings={},movements=[],supplier_returns=[],supplier_adjustments=[],native_purchase_links={})
    i=invoice(pieces=("piece-one",)); k=None
    if typ=='wrong_currency': k=save(s,i,"piece-one",currency="QAR",link=False)
    row=compare_receiving(s,[i],owner="shop")["rows"][0]
    return s,i,k,row


def test_wrong_currency_proposal_preserves_money_in_distinct_units():
    s,i,k,row=fixture(); orig=deepcopy(s)
    p=component_proposal(s,i,row,native_clear=True,order_proofs={"piece-one":{"order_id":"order"}},cross_claim=False)
    assert p['status']=='review_only' and p['posting_allowed'] is False
    assert p['legs']==[{'piece_id':'piece-one','component':'product','currency':'QAR','before':'17.50','after':'0.00'},
                       {'piece_id':'piece-one','component':'product','currency':'SAR','before':'0.00','after':'17.50'}]
    assert p['bank_movements_created']==0 and p['existing_allocations_changed']==0 and s==orig


def test_missing_needs_frozen_scan_and_unique_canonical_proof():
    s,i,k,row=fixture('missing')
    assert component_proposal(s,i,row,native_clear=True,order_proofs={},cross_claim=False)['legs']==[]
    p=component_proposal(s,i,row,native_clear=True,order_proofs={'piece-one':{'order_id':'native','event_id':'scan'}},cross_claim=False)
    assert p['status']=='review_only' and p['legs']==[dict(piece_id='piece-one',component='product',currency='SAR',before='0.00',after='17.50')]


def test_unknown_ledger_settlement_allocation_return_or_opening_withholds_all_legs():
    s,i,k,row=fixture()
    base=dict(native_clear=True,order_proofs={"piece-one":{"order_id":"order"}},cross_claim=False)
    snapshots=[(s,{**base,'native_clear':False}),(s,{**base,'cross_claim':True})]
    for ext in ({'openings':{'prior':{'party_id':'maker','party_type':'supplier','amount':'100.00'}}},
                {'movements':[{'party_id':'maker','party_type':'supplier','amount':'1.00','allocations':[]}]},
                {'movements':[{'party_id':'other','party_type':'supplier','allocations':[{'obligation_id':k}]}]},
                {'supplier_returns':[{'receipt_id':'test-invoice:piece-one:product'}]}):
        snapshots.append(({**s,**ext},base))
    for snap,args in snapshots:
        result=component_proposal(snap,i,row,**args)
        assert result['status']=='blocked' and result['reasons'] and not result['legs'] and not result['posting_allowed']


def test_same_source_unit_or_event_cannot_support_two_pieces():
    from operational_receiving_correction import order_proofs
    s,i,_,_=fixture("missing")
    i.update(session_id="session")
    i["lines"][0]["piece_ids"] += ["second"]
    event=dict(id="ev-1",piece_id="piece-one",user_id="shop",order_number="11",session_id="session",
        event_type="supplier_piece_service_recorded",experiment_mode=False,supplier_context={"id":"maker"},
        supplier_invoice_id=i["id"],financial_invoice_created=True,finalized_at=i["approved_at"],
        order_item_id="salla:11:item",unit_index=1)
    raw=dict(id="order",reference_id="11",items=[{"id":"item","quantity":2}],created_at="2026-08-01T00:00:00Z")
    order=dict(user_id="shop",raw_by_source={"salla_direct":raw})
    for second in (dict(event,id="ev-2",piece_id="second"),dict(event,piece_id="second",unit_index=2)):
        assert order_proofs(i,[event,second],[order])=={}


def test_a_wrong_currency_cannot_hide_a_changed_gross_or_supplier_order():
    s,i,k,_=fixture()
    rid=s["engine"]["obligations"][k]["evidence_ids"][0]
    s["engine"]["facts"][rid]["amount"]="23.00"
    s["engine"]["obligations"][k].update(confirmed="23.00",gross_amount="23.00")
    row=compare_receiving(s,[i],owner="shop")["rows"][0]
    assert not component_proposal(s,i,row,native_clear=True,order_proofs={"piece-one":{"order_id":"order"}},cross_claim=False)["legs"]
    s,i,k,row=fixture()
    assert not component_proposal(s,i,row,native_clear=True,order_proofs={"piece-one":{"order_id":"other"}},cross_claim=False)["legs"]



@pytest.mark.asyncio
async def test_verified_journal_and_finalized_scan_enable_read_only_http_model(env):
    from urllib.parse import urlencode
    from operational_balance_store import STATES, initial
    db, _ = env
    await identities(db)
    await mapping(db)
    source = native_invoice()
    source.update(status="payable_posted", invoice_number="SYN-INVOICE")
    source["lines"][0]["product_price_authority"] = "mezan_v2"
    await post(db, source)
    await db.mezan_supplier_receiving_sessions_v1.insert_one(
        dict(id="session", user_id=OWNER, status="closed", supplier_invoice_id="invoice", supplier_id="supplier"))
    await db.mezan_supplier_receiving_events_v1.insert_one(dict(
        id="scan", user_id=OWNER, session_id="session", piece_id="piece", order_number="11",
        unit_index=1, order_item_id="salla:11:line", supplier_context={"id": "supplier"},
        supplier_invoice_id="invoice", finalized_at=source["approved_at"], experiment_mode=False,
        financial_invoice_created=True, event_type="supplier_piece_service_recorded"))
    raw = dict(id="order", reference_id="11", created_at="2026-08-01T00:00:00Z", items=[dict(id="line", quantity=1)])
    await db.unified_orders.insert_one(dict(user_id=OWNER,raw_by_source={"salla_direct":raw}))
    # The stored journal predates this read. The HTTP route only operates in operational mode.
    await db.mz2_atomic_owners.update_one({"_id":OWNER},{"$set":{"ledger_backend_state":"inactive"}})
    await db.settings.update_one({"user_id":OWNER},{"$set":{"mezan2_financial_cutover.status":"inactive"}})
    stamp = "2026-10-01T00:00:00+00:00"
    state = initial(OWNER)
    state.update(status="active", started_at="2026-09-01T00:00:00Z", revision=7,
                 engine={"as_of":stamp, "facts":{}, "obligations":{}})
    await db[STATES].insert_one(state)
    endpoint = "/receiving/invoice/correction-preview?" + urlencode(dict(expected_as_of=stamp,expected_revision=7))
    async def preview():
        response = await request(db, principal={"id":OWNER},suffix=endpoint)
        assert response.status_code == 200, response.text
        return response.json()
    before = await contents(db)
    result = await preview()
    assert result["status"] == "review_only", result
    assert result["legs"] == [dict(piece_id="piece", component="product", currency="SAR", before="0.00", after="100.00")]
    assert result["order_evidence"]["piece"]["event_id"] == "scan"
    assert await preview() == result
    assert await contents(db) == before
    # A stored supplier-level settlement record withholds the whole proposal.
    await db.mz2_supplier_settlements_v2.insert_one({"user_id":OWNER, "supplier_id":"supplier","kind":"payment"})
    blocked = await preview()
    assert "native_settlement_not_proven_clear" in blocked["reasons"] and blocked["legs"] == []


def test_missing_projected_history_is_not_treated_as_zero_and_detached_receipts_block():
    snap, inv, _, coverage = fixture("missing")
    args = dict(native_clear=True, order_proofs={"piece-one": {"order_id": "order"}}, cross_claim=False)
    for field in ("movements", "openings"):
        partial = dict(snap)
        del partial[field]
        assert "invoice_evidence_incomplete" in component_proposal(partial, inv, coverage, **args)["reasons"]
    snap["engine"]["facts"]["receipt:old-invoice:piece-one:product"] = dict(
        supplier_id="old-maker", item_id="piece-one", order_id="order", amount="17.50")
    assert "other_document_may_cover_receipt" in component_proposal(snap, inv, coverage, **args)["reasons"]


def test_missing_and_currency_legs_are_withheld_if_saved_tax_or_another_unit_disagrees():
    snap, inv, key, coverage = fixture()
    fact = snap["engine"]["facts"][snap["engine"]["obligations"][key]["evidence_ids"][0]]
    fact.update(net_amount="15.00", tax_amount="2.50")
    assert "saved_amount_or_currency_requires_review" in component_proposal(snap, inv, coverage,
        native_clear=True, order_proofs={"piece-one": {"order_id": "order"}}, cross_claim=False)["reasons"]

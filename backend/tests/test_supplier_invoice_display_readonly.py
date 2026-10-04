"""Real Mongo read-only projection and all-stage/PDF identity contracts."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_supplier_invoice_financial_integrity import env
import supplier_receiving_routes as routes
from supplier_invoice_pdf import generate_supplier_invoice_pdf, _display_pdf_rows

CASES=json.loads((Path(__file__).parent/"fixtures/supplier_invoice_display_cases.json").read_text(encoding="utf-8"))["cases"]


async def snapshot(db):
    return {name:await db[name].find({}).sort("_id",1).to_list(20000) for name in await db.list_collection_names()}


@pytest.mark.asyncio
@pytest.mark.parametrize("case",CASES,ids=[c["case"] for c in CASES])
async def test_all_stages_pdf_original_piece_sources_and_no_writes(env,monkeypatch,case):
    db,http,user=env
    invoice={"id":"synthetic-invoice","session_id":"synthetic-session","user_id":"merchant",
      "supplier_approved_by":"employee","invoice_number":"SYNTHETIC-1",
      "lines":deepcopy(case["lines"]),"total_halalas":case["display"]["total_halalas"]}
    await db[routes.SUPPLIER_INVOICES].insert_one(deepcopy(invoice))
    await db[routes.RECEIVING_EVENTS].insert_many([{**deepcopy(p),"id":p["piece_id"],
      "user_id":"merchant","session_id":invoice["session_id"],"supplier_invoice_id":invoice["id"],
      "event_type":"supplier_piece_service_recorded"} for p in case["pieces"]])
    # A similarly named piece from another merchant must never affect projection.
    await db[routes.RECEIVING_EVENTS].insert_one({**deepcopy(case["pieces"][0]),
      "id":"foreign","user_id":"foreign","session_id":invoice["session_id"],
      "supplier_invoice_id":invoice["id"],"event_type":"supplier_piece_service_recorded","variant_id":"foreign"})
    before=await snapshot(db)
    payload={"lines":case["lines"],"pieces":case["pieces"],"expected_total_halalas":invoice["total_halalas"]}
    for stage in ("draft","services","review"):
        response=await http.post("/supplier-receiving-v1/display-groups",json=payload)
        assert response.status_code==200,response.text
        assert response.json()["display"]==case["display"],stage
    response=await http.get("/supplier-receiving-v1/invoices/synthetic-invoice")
    assert response.status_code==200,response.text
    final=response.json()["supplier_invoice"]
    assert final["lines"]==invoice["lines"]
    assert final["display"]==case["display"]
    captured=[]
    def render(document):
        captured.append(deepcopy(document));return b"synthetic-pdf"
    monkeypatch.setattr(routes,"generate_supplier_invoice_pdf",render)
    response=await http.get("/supplier-receiving-v1/invoices/synthetic-invoice/pdf")
    assert response.status_code==200,response.text
    assert captured[0]["display"]==case["display"]
    assert captured[0]["lines"]==invoice["lines"]
    assert before==await snapshot(db)
    # Real renderer consumes those same cards (no second grouping algorithm).
    pdf=generate_supplier_invoice_pdf(captured[0]);assert pdf.startswith(b"%PDF")
    rows=list(_display_pdf_rows(case["display"]))
    assert sum(r["quantity"] for r in rows if not r["continuation"])==len(case["pieces"])
    assert sum(r["total_halalas"] for r in rows if not r["continuation"])==invoice["total_halalas"]


@pytest.mark.asyncio
async def test_projection_failure_and_missing_sources_do_not_mutate_financial_state(env):
    db,http,_=env
    invoice={"id":"legacy","user_id":"merchant","supplier_approved_by":"employee",
       "session_id":"missing","lines":deepcopy(CASES[0]["lines"]),"total_halalas":10000}
    await db[routes.SUPPLIER_INVOICES].insert_one(deepcopy(invoice))
    before=await snapshot(db)
    response=await http.post("/supplier-receiving-v1/display-groups",json={"lines":invoice["lines"],"pieces":[]})
    assert response.status_code==422
    response=await http.get("/supplier-receiving-v1/invoices/legacy")
    assert response.status_code==200
    assert response.json()["supplier_invoice"]["lines"]==invoice["lines"]
    assert response.json()["supplier_invoice"]["display"] is None
    response=await http.get("/supplier-receiving-v1/invoices/legacy/pdf")
    assert response.status_code==409
    assert before==await snapshot(db)


@pytest.mark.asyncio
async def test_real_close_event_without_options_joins_original_piece_snapshot(env):
    from test_supplier_invoice_financial_integrity import seed, close
    db,http,_=env
    session,payload=await seed(db,(5000,5000),services=True)
    before_payload=deepcopy(payload)
    pieces=await db[routes.PIECES].find({}).sort("piece_id",1).to_list(10)
    expected={}
    for index,piece in enumerate(pieces):
        options={"name":f"synthetic-{index}"}
        expected[piece["piece_id"]]=options
        await db[routes.PIECES].update_one({"piece_id":piece["piece_id"]},{"$set":{"product_options_snapshot":options}})
    response=await close(http,session,payload)
    assert response.status_code==200,response.text
    assert payload==before_payload
    invoice=response.json()["supplier_invoice"]
    before=await snapshot(db)
    response=await http.get("/supplier-receiving-v1/invoices/"+invoice["id"])
    assert response.status_code==200,response.text
    saved=response.json()["supplier_invoice"]
    for card in saved["display"]["cards"]:
        for piece in card["pieces"]:
            assert piece["source"]["product_options"]==expected[piece["piece_id"]]
            assert piece["source"]["options_source"]=="original_preparation_piece_snapshot"
            assert piece["source"]["options_snapshot_available"] is True
    assert saved["lines"]==invoice["lines"]
    assert before==await snapshot(db)


@pytest.mark.asyncio
async def test_original_snapshot_join_rejects_changed_identity_without_writes(env):
    from test_supplier_invoice_financial_integrity import seed, close
    db,http,_=env
    session,payload=await seed(db)
    response=await close(http,session,payload);assert response.status_code==200,response.text
    invoice=response.json()["supplier_invoice"]
    await db[routes.PIECES].update_many({}, {"$set":{"product_options_snapshot":{"name":"other"},"variant_id":"replacement"}})
    before=await snapshot(db)
    response=await http.get("/supplier-receiving-v1/invoices/"+invoice["id"])
    saved=response.json()["supplier_invoice"]
    assert saved["display"] is None
    assert saved["display_error"]=="supplier_display_source_snapshot_mismatch"
    assert saved["lines"]==invoice["lines"]
    assert before==await snapshot(db)

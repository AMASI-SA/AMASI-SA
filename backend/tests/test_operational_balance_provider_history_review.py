from copy import deepcopy
from hashlib import sha256

import httpx
import pytest
from fastapi import FastAPI, Header

from operational_balance_engine import reconcile
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, RECEIPTS, digest, initial, read
from operational_provider_history import BLOCKS, HISTORICAL, REVIEWS, approved_context, preview_row
from operational_provider_history_service import _candidates
from test_operational_balance_integration import run
from test_operational_balance_order_page_recognition import BORN, START, T1, T2, raw_document, normalize

OID = "1001"
KEY = "provider:" + OID


def history(*, captures=False):
    raw = raw_document("100", "0")
    if captures:
        raw["payment"] = {"method":"tamara","status":"captured","paid_at":BORN,"reference":"tx-1","paid_amount":"100"}
    p, issues = normalize({**raw, "updated_at":T1})
    assert p and not issues
    p["order_page_balance"]["owner_id"] = "owner"
    s = {**initial("owner"),"status":"active","started_at":START,"revision":1,
         "openings":{"opening-id":{"id":"opening-id","party_id":"tamara","party_type":"provider","currency":"SAR","direction":"for_us","amount":"400.00"}},
         "engine":{"as_of":"2026-10-05T12:00:00+00:00"}}
    order = {"id":OID,"order_number":"123","currency":"SAR","created_at":BORN,"updated_at":T1,"payment":p}
    frozen = reconcile(s,{"orders":[order]},T1)
    assert _candidates(frozen)[OID][1] == HISTORICAL
    return frozen,order


def authorize(state):
    row, code = _candidates(state)[OID]
    item = preview_row(state,row,code)
    assert not item["blocks"]
    state[REVIEWS] = {digest(OID):{"owner_id":"owner","confirmed_by":"owner","proof_hash":row["proof_hash"],
                                   "preview_hash":item["preview_hash"],"evidence_id":"saved-fixture"}}


@pytest.mark.parametrize("captures",[False, True])
def test_proved_new_post_start_claim_requires_owner_review_then_recognizes_once(captures):
    state,order = history(captures=captures)
    before = deepcopy(state)
    assert KEY not in state["engine"]["obligations"]
    assert KEY not in reconcile(state,{"orders":[order]},T2)["engine"]["obligations"]
    authorize(state)
    result = reconcile(state,{"orders":[order]},T2)
    assert result["engine"]["obligations"][KEY]["confirmed"] == "100.00"
    assert result["engine"]["obligations"][KEY]["fee_estimate_complete"] is False
    assert (not _candidates(result) and result["openings"] == before["openings"] and result["movements"] == [])
    assert reconcile(result,{"orders":[order]},T2) == result


@pytest.mark.parametrize("kind",["pooled","opening","target","credit","unknown","partial","refund","bank","cod","source","foreign","before_start"])
def test_approval_never_overrides_changed_source_return_or_pooled_and_opening_payout(kind):
    state,order=history()
    authorize(state)
    movement={"party_type":"provider","party_id":"tamara","currency":"SAR","kind":"settlement","direction":"incoming",
              "amount":"10.00","actual_fee_amount":"0","allocations":[]}
    if kind in {"pooled","opening","target","credit","unknown","partial"}:
        key={"opening":"opening-id","target":KEY,"credit":"credit:"+KEY,"unknown":"provider:other","partial":"provider:other"}.get(kind)
        if key:
            movement["allocations"]=[{"obligation_id":key,"amount":"9.00" if kind=="partial" else "10.00"}]
        state["movements"]=[movement]
        row,code=_candidates(state)[OID]
        block = {"pooled":"pooled","opening":"opening_settlement","partial":"pooled","target":"target","credit":"target"}.get(kind,"movement")
        assert BLOCKS[block] in preview_row(state,row,code)["blocks"]
    elif kind=="refund":
        state["customer_returns"]=[{"id":"r1","order_id":OID,"status":"refunded","shipping":{"kind":"none"}}]
    elif kind=="bank":
        state["movements"]=[{"party_type":"bank","party_id":"bank","currency":"SAR","direction":"incoming","order_number":"123","allocations":[]}]
    elif kind=="cod":
        state["engine"]["facts"]["cod_collection:"+OID]={"amount":"100.00","id":"f1","party_type":"courier","evidence_complete":True}
    elif kind=="source":
        order["updated_at"] = T2
    elif kind=="foreign":
        state[REVIEWS][digest(OID)]["confirmed_by"]="other"
    else:
        order["created_at"]=START
    assert not approved_context(state,order,order["payment"])
    result=reconcile(state,{"orders":[order]},T2)
    assert result["engine"]["obligations"].get(KEY,{}).get("confirmed","0.00") == "0.00"
    assert result["movements"] == state["movements"] and result["openings"] == state["openings"]


def test_http_review_requires_owner_scoped_evidence_and_only_writes_idempotent_metadata():
    async def scenario(db):
        s,order=history()
        await db[STATES].insert_one(s)
        app=FastAPI()
        async def user(x_test_user: str=Header(default="owner")):
            return {"id":x_test_user}
        app.include_router(make_operational_balance_router(db,user),prefix="/api")
        url="/api/operational-balances/provider-history-reviews"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://local") as c:
            assert (await c.get(url,headers={"x-test-user":"staff"})).status_code == 403
            view=(await c.get(url)).json()
            assert await read(db,"owner") == s and len(view["items"]) == 1
            item=view["items"][0]
            doc=sha256(b"provider statement fixture").hexdigest(); evidence=digest(["owner",doc])
            payload={"request_id":"one-review","expected_session_scope":view["session_scope"],"expected_preview_hash":item["preview_hash"],
                     "order_id":OID,"statement_reference":"statement 123 row 1","evidence_id":evidence,"confirmed_outstanding":True}
            assert (await c.post(url+"/approve",json=payload)).status_code == 422
            await db[RECEIPTS].insert_one({"_id":evidence,"owner_id":"owner","sha256":doc})
            assert (await c.post(url+"/approve",json={**payload,"expected_preview_hash":"f"*64})).status_code == 409
            assert (await c.post(url+"/approve",json={**payload,"confirmed_outstanding":False})).status_code == 422
            a=await c.post(url+"/approve",json=payload); assert a.status_code==200,a.text
            again=await c.post(url+"/approve",json=payload); assert again.json()==a.json()
            assert (await c.post(url+"/approve",json={**payload,"request_id":"different-request"})).status_code == 409
            updated=await read(db,"owner")
            assert updated["openings"]==s["openings"] and updated["movements"]==s["movements"] and updated["engine"]==s["engine"]
            assert len(updated["audit"])==1
            assert (await c.get(url)).json()["items"][0]["awaiting_refresh"]
            assert reconcile(updated,{"orders":[order]},T2)["engine"]["obligations"][KEY]["confirmed"]=="100.00"
    run(scenario)

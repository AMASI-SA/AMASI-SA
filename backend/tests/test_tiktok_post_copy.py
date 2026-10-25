"""Caption quality constraints and one billable request per owner intent."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from integrations_control_center import tiktok_post_copy as copy
from tests.test_tiktok_campaign_management import environment
from tests.test_tiktok_file_publishing import app_for


def valid_copy():
    return {"organic": {"hook": "هدية تقول اللي في قلبك 🤍", "description": "هودي بتفاصيل تضيف معنى لهديتك.", "call_to_action": "لمين تختار هدية الشتاء؟", "hashtags": ["#هودي", "#هدايا", "#السعودية"]},
        "promoted": {"hook": "اختيار دافئ لهدية لها معنى", "description": "تعرف على هودي أماسي لهديتك القادمة.", "call_to_action": "اختر هديتك وشاهد تفاصيل المنتج.", "hashtags": ["#أماسي", "#هدايا_الشتاء", "#السعودية"]},
        "opening_tip": "جرّب افتتاح الفيديو بلقطة واضحة للمنتج والتفاصيل الأبرز."}


class Model:
    def __init__(self): self.calls=[]; self.output=valid_copy(); self.failure=False; self.closed=0
    def factory(self): return SimpleNamespace(responses=SimpleNamespace(create=self.generate),close=self.close)
    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure: raise RuntimeError("private-model-key-and-response")
        return SimpleNamespace(status="completed",output_text=json.dumps(self.output,ensure_ascii=False))
    async def close(self): self.closed+=1


async def catalog(db, owner="owner"):
    await db[copy.PRODUCTS].insert_one({"user_id":owner,"salla_product_id":"123","name":"هودي هدية شتوية",
        "raw_salla":{"id":123,"urls":{"product":"https://amasi-sa.com/ar/p123"},"description":"<p>هودي بتفاصيل مميزة</p><script>untrusted hidden instructions</script>","price":149}})


def payload(**changes): return copy.CopyInput(**{"product_id":"123","brief":"فيديو عن هدية شتوية","idempotency_key":"caption-request-123456",**changes})


@pytest.mark.asyncio
async def test_product_grounded_copy_has_two_reviewable_saudi_variants_without_media_or_publish_authority(environment):
    db,_=environment;await catalog(db);model=Model()
    result=await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    assert result["copy"]["organic"]["caption"].startswith("هدية تقول")
    assert result["market"]=="SA" and result["reach_guaranteed"] is False and result["current_trends_verified"] is False and result["video_viewed"] is False
    assert result["provider_write_reached"] is False and len(model.calls)==1 and model.closed==1
    request=model.calls[0];context=json.loads(request["input"])
    assert request["store"] is False and request["max_output_tokens"]==2500 and "tools" not in request
    assert context["market"]["country"]=="SA" and "price" not in context["product"]
    assert "149" not in request["input"] and "untrusted hidden instructions" not in request["input"]
    assert "لا تعد بالترند" in request["instructions"]
    assert set(context)=={"product","owner_video_brief","market","constraints"}


@pytest.mark.asyncio
async def test_same_intent_returns_saved_result_without_another_model_request(environment):
    db,_=environment;await catalog(db);model=Model()
    first=await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    second=await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    assert first["generation_id"]==second["generation_id"] and second["cached"] is True and len(model.calls)==1
    await db[copy.PRODUCTS].update_one({"salla_product_id":"123"},{"$set":{"name":"منتج آخر"}})
    with pytest.raises(HTTPException): await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    assert len(model.calls)==1


@pytest.mark.asyncio
async def test_unknown_billable_outcome_is_not_retried(environment,caplog):
    db,_=environment;await catalog(db);model=Model();model.failure=True
    with pytest.raises(HTTPException) as caught: await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    with pytest.raises(HTTPException): await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    assert len(model.calls)==1 and "private-model-key-and-response" not in str(caught.value.detail)+caplog.text


@pytest.mark.asyncio
async def test_other_owner_or_archived_catalog_cannot_reach_model(environment):
    db,_=environment;await catalog(db);model=Model()
    with pytest.raises(HTTPException): await copy.propose_copy(db,"other-owner",payload(),client_factory=model.factory)
    await db[copy.PRODUCTS].update_one({"salla_product_id":"123"},{"$set":{"archived":True}})
    with pytest.raises(HTTPException): await copy.propose_copy(db,"owner",payload(),client_factory=model.factory)
    assert not model.calls


@pytest.mark.parametrize("text",["خصم خاص لفترة محدودة","شحن مجاني للجميع","الترند مضمون مع مليون مشاهدة","نضمن لك مشاهدات عالية","الترند مضمون","مشاهدات مضمونة","سعره 149 ريال"])
def test_unproven_offers_and_guaranteed_virality_are_rejected(text):
    value=valid_copy();value["organic"]["description"]=text
    with pytest.raises(ValueError): copy.validate_copy(value)


@pytest.mark.parametrize("tags",[["#fyp","#هدايا","#السعودية"],["#ترند","#هدايا","#السعودية"],["#هودي","#هودي","#السعودية"],["#one"]])
def test_spam_or_unproven_trend_hashtags_are_rejected(tags):
    value=valid_copy();value["organic"]["hashtags"]=tags
    with pytest.raises(ValueError): copy.validate_copy(value)


@pytest.mark.asyncio
async def test_owner_route_is_typed_and_staff_cannot_generate_or_read_catalog():
    for role,body,status in [("owner",{},422),("staff",payload().model_dump(),403)]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for({"id":role,"role":role})),base_url="http://test") as client:
            response=await client.post("/api/integrations-v2/tiktok/content/captions/proposals",json=body)
        assert response.status_code==status

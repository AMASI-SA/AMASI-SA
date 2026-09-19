from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from accounting_customer_refunds import create_case, post_case, create_bank_payment, post_bank_payment
from accounting_sales_tax import TaxError
from accounting_recognition_evidence import EvidenceError


class CaseInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    original_key: str = Field(min_length=64,max_length=64)
    case_reference: str = Field(min_length=1,max_length=200)
    amount: str = Field(min_length=1,max_length=30)
    recognized_at: str = Field(min_length=1,max_length=40)
    reason: str = Field(min_length=1,max_length=1000)


class PaymentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    original_key: str = Field(min_length=64,max_length=64)
    case_reference: str = Field(min_length=1,max_length=200)
    bank_account_id: str = Field(min_length=1,max_length=100)
    amount: str = Field(min_length=1,max_length=30)
    paid_at: str = Field(min_length=1,max_length=40)
    bank_reference: str = Field(min_length=1,max_length=200)
    proof_name: str = Field(min_length=1,max_length=200)
    proof_base64: str = Field(min_length=1,max_length=1400000)


def install_customer_refund_routes(router, db, current_user, actor_for):
    base='/accounting-module/customer-refunds'
    async def call(fn, user, permission, **payload):
        actor,owner=await actor_for(user,permission)
        try:
            return await fn(db,owner=owner,actor=actor,**payload)
        except (TaxError,EvidenceError) as exc:
            raise HTTPException(409,detail=str(exc)) from None

    @router.get(base)
    async def get_cases(order_number: str='', user: dict=Depends(current_user)):
        _,owner=await actor_for(user,'accounting.movements.view')
        query={'user_id':owner}
        if order_number:
            query['order_number']=order_number
        originals=await db.mz2_recognition_events.find({'user_id':owner,'status':'posted',
            'proposal.event.kind':'sale',**({'proposal.event.order_number':order_number} if order_number else {})},
            {'_id':0,'event_key':'$_id','proposal.event':1,'proposal.tax':1}).limit(100).to_list(100)
        # Keep original identity explicit; Mongo projection expressions vary by
        # supported server version, so use the persisted proposal key.
        for item in originals:
            if not item.get('event_key'):
                item['event_key']=item.get('proposal',{}).get('event_key')
        return dict(cases=await db.mz2_customer_refunds.find(query,{'_id':0}).limit(100).to_list(100),
            payments=await db.mz2_customer_refund_payments.find(query,{'_id':0,'proof_bytes':0}).limit(100).to_list(100),
            originals=originals,
            banks=await db.accounts.find({'user_id':owner,'account_type':'bank'},{'_id':0,'id':1,'name':1}).limit(100).to_list(100))

    @router.post(base)
    async def create(payload:CaseInput,user:dict=Depends(current_user)):
        return await call(create_case,user,'accounting.refunds.create',**payload.model_dump())

    @router.post(base+'/{case_id}/approve')
    async def approve(case_id:str,user:dict=Depends(current_user)):
        return await call(post_case,user,'accounting.refunds.post',case_id=case_id)

    @router.post(base+'/bank-payments')
    async def payment(payload:PaymentInput,user:dict=Depends(current_user)):
        return await call(create_bank_payment,user,'accounting.refunds.create',**payload.model_dump())

    @router.post(base+'/bank-payments/{payment_id}/approve')
    async def approve_payment(payment_id:str,user:dict=Depends(current_user)):
        return await call(post_bank_payment,user,'accounting.refunds.pay',payment_id=payment_id)

    @router.get(base+'/bank-payments/{payment_id}/proof')
    async def proof(payment_id:str,user:dict=Depends(current_user)):
        _,owner=await actor_for(user,'accounting.movements.view')
        row=await db.mz2_customer_refund_payments.find_one({'id':payment_id,'user_id':owner})
        if not row:
            raise HTTPException(404,'refund_payment_not_found')
        return Response(content=bytes(row['proof_bytes']),media_type='application/octet-stream',
            headers={'Content-Disposition':'attachment; filename="refund-proof.bin"','X-Content-Type-Options':'nosniff',
                     'Cache-Control':'private, no-store','X-Proof-SHA256':row['proof_sha256']})

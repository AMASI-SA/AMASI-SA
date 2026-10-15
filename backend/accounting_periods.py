"""Explicit month closure, serialized with every Mezan 2 owner transaction."""
from datetime import datetime, timezone
import re

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_module_contract import accounting_owner_id, require_owner
from accounting_report_dates import accounting_instant, RIYADH
from accounting_write_control import fresh_actor


async def assert_open_journal_periods(db, owner, rows):
    months = set()
    for row in rows:
        try:
            months.add(accounting_instant(row).astimezone(RIYADH).strftime('%Y-%m'))
        except ValueError as exc:
            raise HTTPException(409, 'accounting_date_required_for_posting') from exc
    closed = await db.mz2_accounting_periods.find_one({
        'user_id': owner, 'month': {'$in': sorted(months)}, 'closed': True})
    if closed:
        raise HTTPException(409, detail={'code': 'accounting_period_closed',
            'month': closed['month'], 'timezone': 'Asia/Riyadh',
            'message': 'الفترة المحاسبية مقفلة؛ لم تُرحّل المعاملة ولم يتغير تاريخها'})


class PeriodChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    month: str = Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    closed: bool = Field(strict=True)
    revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ref: str = Field(min_length=1, max_length=200)


async def set_period(db, owner, actor_id, payload):
    from accounting_atomic import _owner_transaction
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', payload.month):
        raise HTTPException(422, 'invalid_accounting_month')
    if not payload.reason.strip() or not payload.evidence_ref.strip():
        raise HTTPException(422, 'period_reason_and_evidence_required')
    async def change(scoped):
        actor = await fresh_actor(scoped, {'id': actor_id})
        require_owner(actor)
        if accounting_owner_id(actor) != owner:
            raise HTTPException(403, 'period_owner_required')
        key = owner + ':' + payload.month
        prior = await scoped.mz2_accounting_periods.find_one({'_id': key}) or {}
        if prior.get('revision', 0) != payload.revision:
            raise HTTPException(409, 'period_changed_refresh_required')
        now = datetime.now(timezone.utc).isoformat()
        result = dict(user_id=owner, month=payload.month, closed=payload.closed,
            revision=payload.revision + 1, reason=payload.reason.strip(),
            evidence_ref=payload.evidence_ref.strip(), changed_by=actor_id,
            changed_at=now, timezone='Asia/Riyadh')
        await scoped.mz2_accounting_periods.update_one({'_id': key}, {'$set': result}, upsert=True)
        await scoped.mz2_accounting_period_audit.insert_one({**result,
            'previous_closed': prior.get('closed', False)})
        return result
    # Owner control shares the financial write barrier, also while writes paused.
    return await _owner_transaction(db, owner, change, control=True)


def install_period_routes(router, db, current_user):
    base = '/accounting-module/periods'
    @router.get(base)
    async def periods(user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        return dict(items=await db.mz2_accounting_periods.find(
            {'user_id': accounting_owner_id(actor)}, {'_id': 0}).sort('month', -1).to_list(1200),
            can_manage=actor.get('role') == 'owner', timezone='Asia/Riyadh')

    @router.put(base)
    async def update(payload: PeriodChange, user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        require_owner(actor)
        return await set_period(db, accounting_owner_id(actor), actor['id'], payload)

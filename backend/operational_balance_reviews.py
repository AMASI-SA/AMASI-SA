"""Explicit, scoped receipt review over the existing transactional writer.

Only the saved queue is read. No source refresh, automatic approval, balance
backfill, or second financial writer is introduced by the workspace.
"""
from hashlib import sha256
from math import ceil
from typing import Literal
from decimal import Decimal

from fastapi import Depends, Query, Request, Response

from operational_balance_access import fresh_identity, load_access
from operational_balance_store import fail
from operational_driver_payment_review import REVIEWS, RECEIPTS, ReviewInput
from operational_visible_report_read import run_visible_report_read


async def review_access(db, principal, *, expected_owner=None, assignment_id=None, destination=None):
    actor, owner = await fresh_identity(db, principal, expected_owner)
    policy = await load_access(db, actor, owner)
    policy.require_enabled()
    policy.require_operation('courier.review')
    if 'store_delivery.payments.review' in (actor.get('denied_permissions') or []):
        fail('operational_review_forbidden', 'لا توجد صلاحية مراجعة الإيصالات', 403)
    review = None
    if assignment_id:
        review = await db[REVIEWS].find_one({'user_id': owner, 'assignment_id': assignment_id}, {'_id': 0})
        if not review or policy.resource('store_driver', review.get('driver_id'))['movements'] != 'all':
            fail('operational_review_not_found', 'الإيصال غير متاح ضمن صلاحياتك', 404)
        bank = review.get('bank_account_id')
        if bank and not policy.can_reference('bank', bank):
            fail('operational_review_not_found', 'الإيصال غير متاح ضمن صلاحياتك', 404)
        if destination:
            kind = 'bank' if review.get('payment_method') == 'bank_transfer' else 'pos_receivable'
            policy.require_use(kind, destination)
    return actor, owner, policy, review


def require_bank_movements(policy, bank):
    policy.require_use('bank', bank)
    if policy.resource('bank', bank)['movements'] != 'all':
        fail('operational_review_bank_movements_forbidden', 'يلزم السماح بمشاهدة حركات البنك لمطابقة الإيصال', 403)


async def bank_movement_access(db, principal, *, owner, assignment_id, bank_id):
    actor, _, policy, review = await review_access(db, principal, expected_owner=owner, assignment_id=assignment_id)
    if (review.get('status') != 'pending' or review.get('payment_method') != 'bank_transfer'
            or review.get('bank_account_id') != bank_id):
        fail('operational_review_bank_selection_unavailable', 'تغير التحويل أو البنك؛ أعد فتح الإيصال', 409)
    require_bank_movements(policy, bank_id)
    return actor, policy


def queue_query(owner, policy, *, status, driver_id=None, method=None, bank_id=None):
    query = {'user_id': owner, 'status': status}
    if not policy.owner_override:
        drivers = [r['id'] for r in policy.resources if r['kind'] == 'store_driver' and r['movements'] == 'all']
        banks = [r['id'] for r in policy.resources if r['kind'] == 'bank' and policy.can_reference('bank', r['id'])]
        query['driver_id'] = {'$in': drivers}
        query['$or'] = [{'bank_account_id': {'$in': banks}}, {'bank_account_id': None}, {'bank_account_id': ''}]
        if driver_id and driver_id not in drivers:
            fail('operational_review_not_found', 'الموصل غير متاح ضمن صلاحياتك', 404)
        if bank_id and bank_id not in banks:
            fail('operational_review_not_found', 'الحساب غير متاح ضمن صلاحياتك', 404)
    if driver_id:
        query['driver_id'] = driver_id
    if method:
        query['payment_method'] = method
    if bank_id:
        query['bank_account_id'] = bank_id
    return query


def install_workspace_review_routes(router, db, current_user, scope):
    async def public_review(owner, row):
        assignment = await db.store_delivery_assignments.find_one(
            {'user_id': owner, 'id': row['assignment_id']}, {'_id': 0}) or {}
        driver = await db.store_drivers.find_one(
            {'user_id': owner, 'id': row.get('driver_id')}, {'_id': 0, 'name': 1}) or {}
        reviewer = await db.users.find_one({'id': row.get('reviewed_by'), '$or': [
            {'id': owner}, {'created_by': owner}]}, {'_id': 0, 'name': 1}) if row.get('reviewed_by') else None
        return {
            **{key: row.get(key) for key in ('assignment_id', 'driver_id', 'payment_method', 'amount',
                'currency', 'status', 'submitted_at', 'reviewed_at', 'review_note', 'bank_account_id',
                'bank_name_snapshot', 'receipt_reference')},
            'driver_name': driver.get('name') or row.get('driver_name_snapshot') or 'الموصل',
            'order_number': assignment.get('order_number') or row.get('order_number'),
            'customer_name': assignment.get('customer_name_snapshot') or assignment.get('customer_name'),
            'delivery_status': assignment.get('status'),
            'has_receipt': bool(row.get('receipt_reference')),
            'reviewed_by_name': (reviewer or {}).get('name'),
            'courier_balance_effect': '0.00',
        }

    async def unchanged(principal, owner, policy, **kwargs):
        actor, _, latest, review = await review_access(db, principal, expected_owner=owner, **kwargs)
        if (actor['id'], latest.version, latest.owner_override) != (policy.actor['id'], policy.version, policy.owner_override):
            fail('operational_access_changed', 'تغيرت صلاحياتك؛ أعد فتح الصفحة', 409)
        return latest, review

    @router.get('/workspace/reviews')
    async def reviews(request: Request, status: Literal['pending', 'approved', 'rejected'] = 'pending',
                      driver_id: str | None = Query(default=None, max_length=200),
                      method: Literal['bank_transfer', 'card_terminal'] | None = None,
                      bank_id: str | None = Query(default=None, max_length=200),
                      page: int = Query(default=1, ge=1), page_size: int = Query(default=5, ge=1, le=5),
                      user=Depends(current_user)):
        async def read():
            _, owner, policy, _ = await review_access(db, user)
            query = queue_query(owner, policy, status=status, driver_id=driver_id, method=method, bank_id=bank_id)
            total = await db[REVIEWS].count_documents(query)
            pages = max(1, ceil(total / page_size))
            current_page = min(page, pages)
            rows = await db[REVIEWS].find(query, {'_id': 0}).sort([
                ('submitted_at', -1), ('assignment_id', -1)
            ]).skip((current_page - 1) * page_size).limit(page_size).to_list(page_size)
            items = []
            for row in rows:
                items.append(await public_review(owner, row))
            await unchanged(user, owner, policy)
            return {'items': items, 'total': total, 'page': current_page, 'page_size': page_size, 'total_pages': pages}
        return await run_visible_report_read(request, read)

    @router.get('/workspace/reviews/{assignment_id}')
    async def detail(assignment_id: str, request: Request, user=Depends(current_user)):
        async def read():
            _, owner, policy, row = await review_access(db, user, assignment_id=assignment_id)
            result = await public_review(owner, row)
            await unchanged(user, owner, policy, assignment_id=assignment_id)
            return result
        return await run_visible_report_read(request, read)

    @router.get('/workspace/reviews/{assignment_id}/options')
    async def approval_options(assignment_id: str, request: Request, user=Depends(current_user)):
        async def read():
            from operational_driver_payment_review import options
            actor, owner, policy, _ = await review_access(db, user, assignment_id=assignment_id)
            async def authorize(database):
                await review_access(database, user, expected_owner=owner, assignment_id=assignment_id)
            # The workspace reads bank rows through its bounded, paged endpoint.
            # Its receipt options must not inherit the legacy embedded-list cap.
            result = await options(db, owner=owner, actor=actor['id'], assignment_id=assignment_id,
                                   authorize_review=authorize)
            from store_delivery_bank_identity import collection_bank_accounts
            banks = (await collection_bank_accounts(db, owner, actor_id=actor['id']))['items']
            latest, _ = await unchanged(user, owner, policy, assignment_id=assignment_id)
            result['pos_receivables'] = [item for item in result.get('pos_receivables', [])
                                         if latest.resource('pos_receivable', item['id'])['use']]
            result['banks'] = [{'id': item['id'], 'name': item.get('name') or item['id']} for item in banks
                               if item['id'] == result.get('bank_account_id')
                               and latest.resource('bank', item['id'])['use']
                               and latest.resource('bank', item['id'])['movements'] == 'all']
            result['can_select_bank_movement'] = bool(result['banks'])
            return result
        return await run_visible_report_read(request, read)

    @router.get('/workspace/reviews/{assignment_id}/bank-movements')
    async def bank_movements(assignment_id: str, request: Request,
                             page: int = Query(default=1, ge=1),
                             page_size: int = Query(default=5, ge=1, le=5),
                             user=Depends(current_user)):
        """Bounded saved candidates; the writer still verifies original bytes.

        Seeing a bank in a selector does not grant access to its statement.
        Only the receipt's bank, exact amount and unconsumed imported rows are
        eligible. Selection never imports, classifies or confirms a movement.
        """
        async def read_candidates():
            from operational_balance_store import read, now
            from operational_salary_start import instant, RIYADH
            from operational_driver_payment_review import exact_money, financial_source
            _, owner, policy, review = await review_access(db, user, assignment_id=assignment_id)
            bank = review.get('bank_account_id')
            if review.get('status') != 'pending' or review.get('payment_method') != 'bank_transfer' or not bank:
                fail('operational_review_bank_selection_unavailable', 'اختيار حركة البنك متاح للتحويل المعلق فقط', 409)
            require_bank_movements(policy, bank)
            if await financial_source(db, owner) != 'operational':
                fail('operational_driver_review_phase_changed', 'تغير مسار الاعتماد؛ أعد فتح الإيصال', 409)
            state = await read(db, owner)
            value = exact_money(review.get('amount'))
            # The importer stores fixed two-decimal strings. Numeric and
            # unpadded legacy values are accepted without widening the amount.
            decimal_value = Decimal(value)
            amount_values = [value, format(decimal_value.normalize(), 'f')]
            legacy_numeric = float(value)
            if Decimal(str(legacy_numeric)) == decimal_value:
                amount_values.append(legacy_numeric)
            query = {'user_id': owner, 'bank_account_id': bank, 'status': 'unclassified',
                'direction': 'in', 'currency': 'SAR', 'amount': {'$in': amount_values},
                'source': {'$in': ['bank_statement_import', 'manual_reconciled_bank_statement']},
                'movement_date': {'$gt': instant(state['started_at']).astimezone(RIYADH).date().isoformat(),
                                  '$lte': instant(now()).astimezone(RIYADH).date().isoformat()}}
            for key in ('receipt_id', 'accounting_event_id', 'operational_event_id',
                        'confirmed_provider', 'explicit_provider', 'suggested_provider'):
                query[key] = {'$in': [None, '']}
            # Keep the established native query (including exact numeric legacy
            # amounts), and merge the new immutable operational namespace into
            # its existing five-row pages. At most 200 operational candidates
            # and a native window of 200 + page_size rows are read.
            from operational_driver_bank_store import MOVEMENTS, SOURCE
            from operational_driver_bank_statements import seal
            operational_query = {**query, 'source': SOURCE, 'financial_source': 'operational',
                                 'started_at': state['started_at'], 'amount': value}
            operational_rows = await db[MOVEMENTS].find(operational_query).sort([
                ('movement_date', -1), ('id', -1)
            ]).limit(201).to_list(201)
            if len(operational_rows) > 200:
                fail('operational_driver_bank_candidate_scope_too_large')
            operational_rows = [row for row in operational_rows if row.get('seal') == seal(row)]
            total = await db.mz2_daily_movements.count_documents(query) + len(operational_rows)
            pages = max(1, ceil(total / page_size))
            current = min(page, pages)
            fields = {'_id': 0, **{key: 1 for key in ('id', 'movement_date', 'amount', 'currency', 'reference', 'description', 'bank_account_id')}}
            offset = (current - 1) * page_size
            native_start = max(0, offset - len(operational_rows))
            rows = await db.mz2_daily_movements.find(query, fields).sort([
                ('movement_date', -1), ('id', -1)
            ]).skip(native_start).limit(page_size + len(operational_rows)).to_list(page_size + len(operational_rows))
            rows.extend({key: row.get(key) for key in fields if key != '_id'} for row in operational_rows)
            rows.sort(key=lambda row: (str(row.get('movement_date') or ''), str(row.get('id') or '')), reverse=True)
            rows = rows[offset - native_start:offset - native_start + page_size]
            await unchanged(user, owner, policy, assignment_id=assignment_id, destination=bank)
            await bank_movement_access(db, user, owner=owner, assignment_id=assignment_id, bank_id=bank)
            return {'items': rows, 'total': total, 'page': current, 'page_size': page_size, 'total_pages': pages}
        return await run_visible_report_read(request, read_candidates)

    @router.get('/workspace/reviews/{assignment_id}/receipt')
    async def receipt(assignment_id: str, request: Request, user=Depends(current_user)):
        async def read():
            _, owner, policy, review = await review_access(db, user, assignment_id=assignment_id)
            token = review.get('receipt_reference')
            row = await db[RECEIPTS].find_one({'user_id': owner, 'driver_id': review['driver_id'],
                'assignment_id': assignment_id, 'token': token, 'status': 'bound'}) if token else None
            content = bytes((row or {}).get('content') or b'')
            mime = (row or {}).get('content_type')
            if (not content or len(content) > 8 * 1024 * 1024 or mime not in {'image/jpeg', 'image/png', 'image/webp', 'application/pdf'}
                    or sha256(content).hexdigest() != row.get('sha256')):
                fail('operational_review_receipt_unavailable', 'صورة الإيصال غير متاحة أو تعذر التحقق منها', 404)
            await unchanged(user, owner, policy, assignment_id=assignment_id)
            return Response(content, media_type=mime, headers={'Cache-Control': 'no-store',
                'X-Content-Type-Options': 'nosniff', 'Content-Disposition': 'inline; filename="receipt"'})
        return await run_visible_report_read(request, read)

    @router.post('/workspace/reviews/{assignment_id}')
    async def decide(assignment_id: str, payload: ReviewInput, user=Depends(current_user)):
        from operational_driver_payment_review import review_payment
        destination = payload.destination_financial_id if payload.decision == 'approved' else None
        actor, owner, _, _ = await review_access(db, user, assignment_id=assignment_id, destination=destination)
        if payload.expected_financial_source != 'operational':
            fail('operational_driver_review_phase_changed', 'تغير مسار الاعتماد؛ أعد تحميل خيارات الإيصال', 409)
        async def authorize(database):
            await review_access(database, user, expected_owner=owner,
                                assignment_id=assignment_id, destination=destination)
        return await review_payment(db, owner=owner, actor=actor['id'], assignment_id=assignment_id,
                                    payload=payload, authorize_review=authorize)

"""Atomic evidence-only resubmission for an operational courier bank transfer."""
from hashlib import sha256

from operational_balance_store import digest, fail, now
from operational_driver_review_store import transact
from store_delivery_bank_identity import SNAPSHOT_FIELDS, collection_bank_snapshot, find_collection_bank


async def authorize_driver(db, owner, actor, driver):
    user = await db.users.find_one({'id': actor})
    profile = await db.store_drivers.find_one({'user_id': owner, 'id': driver,
                                               'account_user_id': actor, 'status': 'active'})
    if (not user or user.get('role') != 'store_driver' or user.get('created_by') != owner or not profile
            or user.get('disabled') or user.get('is_active') is False or user.get('deleted_at')):
        fail('store_driver_profile_not_linked', status=403)


async def _one(db, collection, owner, **query):
    rows = await db[collection].find({'user_id': owner, **query}).limit(2).to_list(2)
    if len(rows) != 1:
        fail('payment_review_resubmit_conflict')
    return rows[0]


def _receipt_valid(receipt, *, status):
    content = bytes(receipt.get('content') or b'')
    return (receipt.get('status') == status and bool(content) and len(content) <= 8 * 1024 * 1024
            and receipt.get('sha256') == sha256(content).hexdigest()
            and not receipt.get('financial_txn_group_id') and not receipt.get('operational_event_id'))


async def resubmit(db, *, owner, actor, driver, assignment_id, receipt_reference, bank_account_id):
    fingerprint = digest([owner, actor, driver, assignment_id, receipt_reference, bank_account_id])

    async def commit(scoped):
        assignment = await _one(scoped, 'store_delivery_assignments', owner, id=assignment_id, driver_id=driver)
        review = await _one(scoped, 'store_delivery_payment_reviews', owner, assignment_id=assignment_id, driver_id=driver)
        collection = await _one(scoped, 'store_delivery_collections', owner, assignment_id=assignment_id, driver_id=driver)
        receipt = await _one(scoped, 'store_delivery_receipts', owner, assignment_id=assignment_id,
                              driver_id=driver, token=receipt_reference)
        if assignment.get('active') is not True or assignment.get('status') != 'delivered':
            fail('driver_delivery_required')
        if review.get('payment_method') != 'bank_transfer' or collection.get('payment_method') != 'bank_transfer':
            fail('payment_review_resubmit_conflict')
        # A lost response after binding a new receipt must not re-resolve a bank
        # whose status/phase changed later, nor create another review revision.
        if (review.get('status') == 'pending' and review.get('resubmission_fingerprint') == fingerprint
                and review.get('receipt_reference') == receipt_reference and review.get('bank_account_id') == bank_account_id
                and collection.get('receipt_reference') == receipt_reference and collection.get('bank_account_id') == bank_account_id
                and collection.get('payment_confirmed') is False and collection.get('review_status') == 'pending_accountant_review'
                and all(collection.get(field) == review.get(field) for field in SNAPSHOT_FIELDS)
                and _receipt_valid(receipt, status='bound')):
            return {key: value for key, value in review.items() if key not in {'_id', 'user_id'}}
        if review.get('status') != 'rejected':
            fail('payment_review_not_rejected')
        from operational_driver_payment_review import financial_source
        if await financial_source(scoped, owner) != 'operational':
            fail('driver_payment_bank_context_changed')
        if not _receipt_valid(receipt, status='uploaded'):
            fail('collection_receipt_invalid', status=422)
        bank = await find_collection_bank(scoped, owner, bank_account_id)
        if bank is None or bank['bank_financial_source'] != 'operational':
            fail('business_bank_account_invalid', status=422)
        revision = review.get('revision', 1)
        if type(revision) is not int or revision < 1 or collection.get('payment_confirmed') is True:
            fail('payment_review_resubmit_conflict')
        order = await _one(scoped, 'unified_orders', owner, order_number=assignment.get('order_number'))
        stamp = now()
        url = f'/api/store-delivery/evidence/receipt/{receipt_reference}'
        snapshot = collection_bank_snapshot(bank)
        patch = {**snapshot, 'status': 'pending', 'receipt_reference': receipt_reference, 'receipt_url': url,
            'submitted_at': stamp, 'resubmitted_at': stamp, 'revision': revision + 1, 'reviewed_at': None,
            'reviewed_by': None, 'review_note': '', 'resubmission_fingerprint': fingerprint}
        mutations = [
            ('store_delivery_payment_reviews', review, {'status': 'rejected',
                'revision': revision if 'revision' in review else {'$exists': False}}, patch),
            ('store_delivery_collections', collection, {}, {**snapshot,
                'review_status': 'pending_accountant_review', 'payment_status': 'pending_accountant_review',
                'payment_confirmed': False, 'receipt_reference': receipt_reference, 'receipt_url': url, 'review_note': ''}),
            ('store_delivery_assignments', assignment, {}, {'payment_review_status': 'pending_accountant_review',
                'payment_status': 'pending_accountant_review', 'updated_at': stamp}),
            ('unified_orders', order, {}, {'store_delivery_payment_status': 'pending_accountant_review',
                'store_delivery_payment_review_status': 'pending_accountant_review', 'store_delivery_updated_at': stamp}),
            ('store_delivery_receipts', receipt, {'status': 'uploaded'}, {'status': 'bound', 'bound_at': stamp, 'review_status': None}),
        ]
        old_token = review.get('receipt_reference')
        if old_token and old_token != receipt_reference:
            old = await _one(scoped, 'store_delivery_receipts', owner, assignment_id=assignment_id,
                              driver_id=driver, token=old_token)
            mutations.append(('store_delivery_receipts', old, {'status': 'bound'},
                {'status': 'superseded', 'superseded_at': stamp, 'superseded_by': receipt_reference}))
        for name, row, conditions, values in mutations:
            result = await scoped[name].update_one({'_id': row['_id'], 'user_id': owner, **conditions}, {'$set': values})
            if result.matched_count != 1:
                fail('payment_review_resubmit_conflict')
        return {key: value for key, value in {**review, **patch}.items() if key not in {'_id', 'user_id'}}

    return await transact(db, owner, commit, lambda scoped: authorize_driver(scoped, owner, actor, driver),
                          operation='receipt_resubmission')

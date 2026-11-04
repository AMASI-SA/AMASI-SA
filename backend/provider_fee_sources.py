"""Read-only product identities from owner-scoped, imported provider statements."""
from collections import defaultdict
from copy import deepcopy

STATEMENT_COLLECTIONS = frozenset({'settlement_entries', 'settlement_files'})


def statement_plan_index(entries, files, owner):
    """Keep the source's product label verbatim and reject conflicting evidence."""
    documents = {row['id']: row for row in files if row.get('id') and row.get('user_id') == owner}
    grouped = defaultdict(list)
    for row in entries:
        document = documents.get(row.get('file_id'))
        product = row.get('raw_product_type')
        if (row.get('user_id') != owner or not document or not isinstance(product, str)
                or not product.strip() or row.get('provider') != document.get('provider')
                or not document.get('file_hash') or not row.get('order_number')):
            continue
        labels = [document.get('filename'), row.get('settlement_reference'),
                  (document.get('header') or {}).get('statement_id')]
        if any(str(label or '').upper().startswith(('SYN-', 'SYNTHETIC')) for label in labels):
            continue
        grouped[row['provider'], str(row['order_number'])].append(row)
    result = {}
    for key, rows in grouped.items():
        products = {row['raw_product_type'] for row in rows}
        evidence = {'source': 'settlement_entries',
                    'entry_ids': sorted({str(row['id']) for row in rows if row.get('id')}),
                    'file_ids': sorted({row['file_id'] for row in rows})}
        if len(products) == 1:
            result[key] = {'plan_id': products.pop(), 'plan_evidence': evidence}
        else:
            result[key] = {'fee_plan_issue': 'provider_payment_plan_conflict', 'plan_evidence': evidence}
    return result


async def load_statement_plans(db, owner, *, providers, order_numbers=None, max_rows=20000):
    providers = sorted(set(providers) & {'salla', 'tabby', 'tamara', 'emkan'})
    if not providers:
        return {}
    query = {'user_id': owner, 'provider': {'$in': providers}, 'raw_product_type': {'$nin': [None, '']}}
    batches = [None]
    if order_numbers is not None:
        numbers = sorted({str(value) for value in order_numbers if value is not None})
        if not numbers:
            return {}
        batches = [numbers[start:start + 250] for start in range(0, len(numbers), 250)]
    entries = []
    for batch in batches:
        scoped_query = {**query, **({'order_number': {'$in': batch}} if batch is not None else {})}
        entries.extend(await db['settlement_entries'].find(scoped_query, {
            '_id': 0, 'id': 1, 'user_id': 1, 'provider': 1, 'order_number': 1,
            'file_id': 1, 'raw_product_type': 1, 'settlement_reference': 1,
        }).to_list(max_rows + 1 - len(entries)))
        if len(entries) > max_rows:
            raise ValueError('provider_fee_product_scope_too_large')
    if len(entries) > max_rows:
        raise ValueError('provider_fee_product_scope_too_large')
    file_ids = sorted({row['file_id'] for row in entries if row.get('file_id')})
    if not file_ids:
        return {}
    files = []
    for start in range(0, len(file_ids), 250):
        files.extend(await db['settlement_files'].find({
            'user_id': owner, 'provider': {'$in': providers}, 'id': {'$in': file_ids[start:start + 250]},
        }, {'_id': 0, 'id': 1, 'user_id': 1, 'provider': 1, 'filename': 1,
            'file_hash': 1, 'header.statement_id': 1}).to_list(max_rows + 1 - len(files)))
        if len(files) > max_rows:
            raise ValueError('provider_fee_product_scope_too_large')
    if len(files) > max_rows:
        raise ValueError('provider_fee_product_scope_too_large')
    return statement_plan_index(entries, files, owner)


def bind_statement_plan(payment, index, order_number):
    """Add pricing metadata without changing capture, refund or payment amounts."""
    result = deepcopy(payment)
    tenders = (result.get('split') or {}).get('tenders')
    rows = tenders if tenders is not None else [result]
    for row in rows:
        source = index.get((row.get('provider'), str(order_number)))
        if not source:
            continue
        if (source.get('fee_plan_issue') or
                row.get('plan_id') not in (None, '', source.get('plan_id'))):
            row['fee_plan_issue'] = 'provider_payment_plan_conflict'
        else:
            row['plan_id'] = source['plan_id']
        row['plan_evidence'] = deepcopy(source['plan_evidence'])
    return result

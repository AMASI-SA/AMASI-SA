"""Dashboard-only disk groups and bounded financial detail pages.

Ordering indexes never calculate money. Callers finish every original reduction
before selecting a response page. No cross-request result cache is retained.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_page = ContextVar('dashboard_financial_page', default=('payments', None, 50, None))
KINDS = ('payments', 'shipping', 'sources', 'months', 'payment_methods')

def current_financial_page_request():
    return _page.get()

@contextmanager
def financial_page_request(kind='payments', cursor=None, limit=50, parent_key=None):
    if kind not in KINDS:
        raise ValueError('invalid financial detail kind')
    _pagination(0, cursor, limit)
    token = _page.set((kind, cursor, limit, parent_key))
    try:
        yield
    finally:
        _page.reset(token)

def _pagination(total, cursor=None, limit=50):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError('financial page limit must be 1..50')
    try:
        offset = 0 if cursor is None else int(cursor)
    except (ValueError, TypeError):
        raise ValueError('invalid financial cursor') from None
    if offset < 0 or offset > 9223372036854775807 or (cursor is not None and str(offset) != str(cursor)):
        raise ValueError('invalid financial cursor')
    following = min(offset + limit, total)
    return dict(limit=limit, offset=offset, total=total, has_more=following < total,
                next_cursor=str(following) if following < total else None)

class FinancialRows:
    """Repeatable mutable rows ordered by a frozen, stable disk index."""
    def __init__(self, store, rows=(), *, number=None, text=None, descending=False):
        self.store = store
        self.name = 'financial-' + uuid4().hex
        self.rows = store.map(self.name)
        self.count = 0
        self.direction = 'DESC' if descending else 'ASC'
        store.execute('CREATE TABLE IF NOT EXISTS dashboard_financial_rows(namespace TEXT, ordinal INTEGER, number REAL, label TEXT, PRIMARY KEY(namespace,ordinal)) WITHOUT ROWID')
        store.execute('CREATE INDEX IF NOT EXISTS dashboard_financial_sort ON dashboard_financial_rows(namespace,number,label,ordinal)')
        for row in rows:
            self.rows[self.count] = row
            store.execute('INSERT INTO dashboard_financial_rows VALUES(?,?,?,?)',
                          (self.name, self.count, number(row) if number else 0, text(row) if text else ''))
            self.count += 1
        self.rows.flush()

    def __len__(self):
        return self.count

    def __iter__(self):
        yield from self.iter_page(None, 0)

    def iter_page(self, limit, offset):
        query = ('SELECT ordinal FROM dashboard_financial_rows WHERE namespace=? ORDER BY number '
                 + self.direction + ',label ' + self.direction + ',ordinal ASC')
        args = (self.name,)
        if limit is not None:
            query += ' LIMIT ? OFFSET ?'
            args += (limit, offset)
        cursor = self.store.execute(query, args)
        try:
            while batch := cursor.fetchmany(128):
                for (key,) in batch:
                    yield self.rows[key]
        finally:
            cursor.close()

    def __eq__(self, other):
        from itertools import zip_longest
        sentinel = object()
        return all(a == b for a, b in zip_longest(self, other, fillvalue=sentinel))


def group_map(store, name):
    return store.map(name + '-' + uuid4().hex) if store is not None else {}


def rollup_payments(store, rows, normalize, parent_labels):
    buckets = group_map(store, 'payment-rollup')
    for row in rows:
        raw = (row.get('name') or '').strip()
        sub_key, display, parent = normalize(raw)
        if not sub_key:
            continue
        key = parent or sub_key
        bucket = buckets.setdefault(key, dict(name=parent_labels.get(parent, display) if parent else display,
            key=key,total_sales=0.,fee_amount=0.,vat_amount=0.,orders_count=0,
            commission_percent=row.get('commission_percent'),fixed_fee=row.get('fixed_fee'),
            vat_percent=row.get('vat_percent'),sub_methods=[]))
        sales, fee, vat, count = (float(row.get('total_sales') or 0), float(row.get('fee_amount') or 0),
                                 float(row.get('vat_amount') or 0), int(row.get('orders_count') or 0))
        bucket['total_sales'] += sales
        bucket['fee_amount'] += fee
        bucket['vat_amount'] += vat
        bucket['orders_count'] += count
        # A known parent has finitely many canonical rails; unknown labels
        # have parent=None, hence their only child key equals the parent key.
        child = next((child for child in bucket['sub_methods'] if child['key'] == sub_key), None)
        if child is None:
            bucket['sub_methods'].append(dict(key=sub_key,display=display,name=display,
                                              total_sales=sales,fee_amount=fee,orders_count=count))
        else:
            child['total_sales'] += sales
            child['fee_amount'] += fee
            child['orders_count'] += count
    def finalized():
        for bucket in buckets.values():
            bucket['sub_methods'].sort(key=lambda child: child['total_sales'], reverse=True)
            for child in bucket['sub_methods']:
                child['total_sales'] = round(child['total_sales'], 2)
                child['fee_amount'] = round(child['fee_amount'], 2)
            for field in ('total_sales', 'fee_amount', 'vat_amount'):
                bucket[field] = round(bucket[field], 2)
            yield bucket
    return FinancialRows(store, finalized(), number=lambda row: row['total_sales'], descending=True)


def financial_response_pages(payments, shipping, sources, months):
    requested, cursor, limit, parent = _page.get()
    sections = dict(payments=payments,shipping=shipping,sources=sources,months=months)
    result, metadata = {}, {}
    for kind, rows in sections.items():
        page = _pagination(len(rows), cursor if requested == kind else None, limit if requested == kind else 50)
        result[kind] = [dict(row) for row in rows.iter_page(page['limit'], page['offset'])]
        metadata[kind] = page
    for row in result['payments']:
        children = row.get('sub_methods') or []
        page = _pagination(len(children), cursor if requested == 'payment_methods' and parent == row.get('key') else None,
                           limit if requested == 'payment_methods' and parent == row.get('key') else 50)
        row['sub_methods'] = children[page['offset']:page['offset'] + page['limit']]
        row['sub_methods_pagination'] = page
    if requested == 'payment_methods':
        # Parent may be outside the first parent page; do not silently return
        # a different parent's children. Scan bounded rows without collecting.
        found = next((row for row in payments if row.get('key') == parent), None)
        children = found.get('sub_methods') or [] if found else []
        page = _pagination(len(children), cursor, limit)
        result['payment_methods'] = children[page['offset']:page['offset'] + page['limit']]
        metadata['payment_methods'] = dict(page, parent_key=parent)
    return result, metadata

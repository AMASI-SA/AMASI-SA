"""Attempt-owned diagnostic lookup; financial output remains an ordinary list.

The collector owns its append-only issue list. All identity-changing updates in
that scope use update_issue; metadata and nested values remain live references.
Other callers and unusual rows retain ordinary Python list equality semantics.
"""

INDEX_KEY = '_operational_issue_index'


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _key(row):
    # Owned producers insert these two discriminators first. Preserve unusual
    # dictionary comparison/exception order by falling back for any other form.
    if type(row) is not dict:
        return None
    keys = iter(row)
    first, second = next(keys, None), next(keys, None)
    if (type(first) is str and type(second) is str
            and first == 'code' and second == 'source_id'
            and all(type(key) is str for key in row)
            and type(row.get('code')) is str and type(row.get('source_id')) is str):
        return row['code'], row['source_id']
    return None


class _IssueIndex:
    def __init__(self, rows):
        self.rows = rows
        self.buckets = {}
        self.count = 0
        self.valid = type(rows) is list
        self.last = None
        self.last_key = None

    def invalidate(self):
        self.valid = False
        self.buckets.clear()

    def sync(self, rows):
        if not self.valid:
            return False
        if rows is not self.rows or len(rows) < self.count:
            self.invalidate()
        if not self.valid:
            return False
        if self.count and (rows[self.count - 1] is not self.last or _key(self.last) != self.last_key):
            self.invalidate()
            return False
        while self.count < len(rows):
            _checkpoint()
            row = rows[self.count]
            key = _key(row)
            if key is None:
                self.invalidate()
                return False
            self.buckets.setdefault(key, []).append((self.count, row))
            self.count += 1
            self.last, self.last_key = row, key
        return True

    def candidates(self, rows, item):
        if not self.sync(rows):
            return rows
        key = _key(item)
        if key is None:
            return rows
        return (row for _, row in self.buckets.get(key, ()))

    def changed(self, row, old_key):
        if not self.valid or _key(row) == old_key:
            return
        # Identity changes are uncommon; invalidate rather than reconstruct
        # ordering or silently retain an obsolete equality bucket.
        self.invalidate()


def enable_issue_index(out):
    """Only the collector activates this private, nonpersistent scope."""
    out[INDEX_KEY] = _IssueIndex(out['issues'])


def finish_issue_index(out):
    out.pop(INDEX_KEY, None)


def invalidate_issue_index(out):
    """Required before externally replacing/reordering rows or their keys."""
    index = out.get(INDEX_KEY)
    if isinstance(index, _IssueIndex):
        index.invalidate()


def _index(out):
    if type(out) is not dict:
        return None
    index = out.get(INDEX_KEY)
    return index if isinstance(index, _IssueIndex) else None


def append_issue(out, item):
    """Append without deduplication, including deliberately equal rows."""
    rows = out['issues']
    index = _index(out)
    if index is not None:
        index.sync(rows)
    rows.append(item)
    if index is not None:
        index.sync(rows)


def append_unique_issue(out, item):
    rows = out['issues']
    index = _index(out)
    if index is None:
        if item not in rows:
            rows.append(item)
        return
    candidates = index.candidates(rows, item)
    if candidates is rows:
        if item not in rows:
            append_issue(out, item)
        return
    for row in candidates:
        _checkpoint()
        # Python list containment first checks object identity, then compares
        # the existing element with the candidate. Do not canonicalize values.
        if row is item or row == item:
            return
    append_issue(out, item)


def update_issue(out, row, values):
    index = _index(out)
    if index is not None:
        index.sync(out['issues'])
        old_key = _key(row)
    row.update(values)
    if index is not None:
        index.changed(row, old_key)


def issues_for_identity(out, code, source_id):
    """Preserve row order; the caller keeps its original matching predicate."""
    rows = out['issues']
    index = _index(out)
    if index is None or not index.sync(rows) or type(code) is not str or type(source_id) is not str:
        return rows
    return [row for _, row in index.buckets.get((code, source_id), ())]

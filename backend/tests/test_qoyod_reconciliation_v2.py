"""Regression tests for real Qoyod invoice authority in reconciliation v2."""
import asyncio

from integrations.qoyod.reconciliation_v2 import _load_local_qoyod_invoices


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *args, **kwargs):
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _QoyodInvoices:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None):
        assert query == {"user_id": "main"}
        return _Cursor([dict(row) for row in self.rows])


class _DB:
    def __init__(self, rows):
        self.qoyod_invoices = _QoyodInvoices(rows)


def test_dry_and_preview_invoice_ids_are_not_authoritative():
    db = _DB([
        {
            "qoyod_invoice_id": "DRY:invoice:f0804da4",
            "reference": "273310114",
            "issue_date": "2026-08-06",
        },
        {
            "qoyod_invoice_id": "PREVIEW:invoice:abc",
            "reference": "273310115",
            "issue_date": "2026-08-06",
        },
        {
            "qoyod_invoice_id": None,
            "reference": "273310116",
            "issue_date": "2026-08-06",
        },
        {
            "qoyod_invoice_id": "712",
            "invoice_number": "712",
            "reference": "275957683",
            "issue_date": "2026-08-06",
            "total": 239.41,
        },
    ])

    result = asyncio.run(_load_local_qoyod_invoices(
        db,
        markers_user_id="main",
    ))

    assert set(result) == {"275957683"}
    assert result["275957683"]["qoyod_invoice_id"] == "712"


"""The mobile register preserves cross-account projections and stable page boundaries."""
from random import Random
import weakref

from operational_balance_workspace import paginate, register_page
from operational_workspace_projection import row_order


def statement(event, account, order):
    return {"event_id": event, "id": str(order % 4), "business_date": f"2026-11-{order % 28 + 1:02}",
            "recorded_at": "2026-11-19T12:00:00Z", "account": account}


def test_mobile_page_matches_complete_sort_for_shared_postings_ties_and_past_end():
    for seed in range(12):
        rng = Random(seed)
        ledgers = [[statement(rng.randrange(45), account, rng.randrange(32)) for _ in range(rng.randrange(35))]
                   for account in range(7)]
        first = {}
        for ledger in ledgers:
            for row in ledger:
                first.setdefault(row["event_id"], row)
        reference = sorted(first.values(), key=row_order, reverse=True)
        for size in (1, 5, 8):
            for page in (1, 2, 5, 10**30):
                assert register_page(iter(ledgers), page, size) == paginate(reference, page, size)
    assert register_page([], 97, 5) == paginate([], 97, 5)


def test_first_page_releases_old_row_objects_while_reading_the_next_accounts():
    class Posting(dict):
        pass

    refs = []
    def ledgers():
        for account in range(5):
            if account >= 2:
                # Counts require event IDs. Only the shortlist and the last
                # yielded row may remain alive from earlier iterators.
                assert sum(ref() is not None for ref in refs) <= 6
            def entries():
                for index in range(100):
                    key = account * 100 + index
                    row = Posting(statement(key, account, key))
                    refs.append(weakref.ref(row))
                    yield row
            yield entries()

    assert register_page(ledgers(), 1, 5)["total"] == 500

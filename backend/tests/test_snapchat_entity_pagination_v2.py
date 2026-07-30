from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlsplit

import pytest

from integrations_control_center import snapchat_native_entities_sync as entities


class FakeCollection:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    async def update_one(self, query, update, upsert=False):
        target = next(
            (
                row
                for row in self.rows
                if all(row.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if target is None and upsert:
            target = deepcopy(query)
            target.update(deepcopy(update.get("$setOnInsert") or {}))
            self.rows.append(target)
        if target is not None:
            target.update(deepcopy(update.get("$set") or {}))
        return object()


class FakeDB:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {}

    def __getitem__(self, name):
        return FakeCollection(self.rows.setdefault(name, []))

    def __getattr__(self, name):
        return self[name]


class PaginatedContext:
    def __init__(self):
        self.db = FakeDB()
        self.user_id = "owner-1"
        self.calls: list[str] = []

    def now_iso(self):
        return "2026-07-30T13:00:00+00:00"

    async def get_json(self, client, url, *, headers, params=None):
        self.calls.append(url)
        path = urlsplit(url).path
        if path.endswith("/campaigns"):
            return {"request_status": "SUCCESS", "campaigns": []}
        if path.endswith("/adsquads"):
            return {"request_status": "SUCCESS", "adsquads": []}
        if path.endswith("/creatives"):
            return {"request_status": "SUCCESS", "creatives": []}
        if path.endswith("/ads") and "cursor=2" not in url:
            return {
                "request_status": "SUCCESS",
                "ads": [
                    {"ad": {"id": "ad-1", "name": "Ad 1"}},
                    {"ad": {"id": "ad-2", "name": "Ad 2"}},
                ],
                "paging": {
                    "next_link": (
                        "https://adsapi.snapchat.com/v1/adaccounts/"
                        "account-1/ads?cursor=2"
                    )
                },
            }
        if path.endswith("/ads") and "cursor=2" in url:
            return {
                "request_status": "SUCCESS",
                "ads": [
                    {"ad": {"id": "ad-2", "name": "Ad 2 duplicate"}},
                    {"ad": {"id": "ad-3", "name": "Ad 3"}},
                    {"ad": {"id": "ad-4", "name": "Ad 4"}},
                ],
            }
        raise AssertionError(f"unexpected Snapchat URL: {url}")


def _ad_page_calls(context: PaginatedContext) -> int:
    return sum(urlsplit(url).path.endswith("/ads") for url in context.calls)


ACCOUNT = {
    "ad_account_id": "account-1",
    "mezan_integration_account_id": "mezan-account-1",
}


@pytest.mark.asyncio
async def test_entity_sync_streams_all_pages_and_deduplicates_ids():
    context = PaginatedContext()
    saved, counts, errors = await entities.sync_snapchat_entities(
        context,
        object(),
        "access-token",
        ACCOUNT,
    )

    assert saved == 4
    assert counts == {
        "campaign": 0,
        "ad_squad": 0,
        "ad": 4,
        "creative": 0,
    }
    assert errors == []
    stored = context.db.rows["mezan_snapchat_entities_v2"]
    assert {row["external_id"] for row in stored} == {
        "ad-1",
        "ad-2",
        "ad-3",
        "ad-4",
    }
    assert _ad_page_calls(context) == 2


@pytest.mark.asyncio
async def test_row_limit_marks_sync_partial_instead_of_silent_complete(monkeypatch):
    monkeypatch.setattr(entities, "MAX_ENTITY_ROWS_PER_TYPE", 2)
    context = PaginatedContext()

    saved, counts, errors = await entities.sync_snapchat_entities(
        context,
        object(),
        "access-token",
        ACCOUNT,
    )

    assert saved == 2
    assert counts["ad"] == 2
    assert any(
        error.get("error") == "entity_row_limit_reached"
        and error.get("next_page_present") == "true"
        for error in errors
    )
    assert _ad_page_calls(context) == 1


@pytest.mark.asyncio
async def test_page_limit_marks_sync_partial_when_next_page_remains(monkeypatch):
    monkeypatch.setattr(entities, "MAX_ENTITY_PAGES", 1)
    context = PaginatedContext()

    saved, counts, errors = await entities.sync_snapchat_entities(
        context,
        object(),
        "access-token",
        ACCOUNT,
    )

    assert saved == 2
    assert counts["ad"] == 2
    assert any(
        error.get("error") == "entity_page_limit_reached"
        and error.get("next_page_present") == "true"
        for error in errors
    )

import pytest

from integrations_control_center.ads_auto_sync_target_recovery import (
    augment_auto_sync_targets,
    selected_meta_scheduler_targets,
    selected_snapchat_scheduler_targets,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length):
        return self.rows[:length]


class FakeCollection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.last_query = None
        self.last_projection = None

    def find(self, query, projection):
        self.last_query = query
        self.last_projection = projection
        matched = []
        for row in self.rows:
            if any(row.get(key) != value for key, value in query.items()):
                continue
            matched.append(row)
        return FakeCursor(matched)


class FakeDb:
    def __init__(self, accounts):
        self.mezan_integration_accounts_v2 = FakeCollection(accounts)


@pytest.mark.asyncio
async def test_recovers_meta_target_when_provider_projection_is_missing():
    db = FakeDb([
        {
            "user_id": "owner-1",
            "provider": "meta_ads",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": True,
        },
        {
            "user_id": "owner-1",
            "provider": "meta_ads",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": True,
        },
    ])

    assert await selected_meta_scheduler_targets(db) == {
        ("owner-1", "meta_ads")
    }
    assert await augment_auto_sync_targets(db, []) == [
        ("owner-1", "meta_ads")
    ]


@pytest.mark.asyncio
async def test_keeps_normal_targets_and_ignores_unselected_or_disconnected_meta():
    db = FakeDb([
        {
            "user_id": "owner-unselected",
            "provider": "meta_ads",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": False,
        },
        {
            "user_id": "owner-disconnected",
            "provider": "meta_ads",
            "connection_status": "error",
            "connection_provenance": "api_connection",
            "mezan_selected": True,
        },
        {
            "user_id": "owner-wrong-source",
            "provider": "meta_ads",
            "connection_status": "connected",
            "connection_provenance": "legacy_import",
            "mezan_selected": True,
        },
    ])

    result = await augment_auto_sync_targets(
        db,
        [
            ("owner-snap", "snapchat_ads"),
            ("owner-meta-existing", "meta_ads"),
        ],
    )

    assert result == [
        ("owner-meta-existing", "meta_ads"),
        ("owner-snap", "snapchat_ads"),
    ]


@pytest.mark.asyncio
async def test_recovers_snapchat_target_from_selected_connected_account():
    db = FakeDb([
        {
            "user_id": "owner-snap",
            "provider": "snapchat_ads",
            "connection_status": "connected",
            "mezan_selected": True,
        },
        {
            "user_id": "owner-snap",
            "provider": "snapchat_ads",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "mezan_selected": True,
        },
        {
            "user_id": "owner-unselected",
            "provider": "snapchat_ads",
            "connection_status": "connected",
            "mezan_selected": False,
        },
        {
            "user_id": "owner-disconnected",
            "provider": "snapchat_ads",
            "connection_status": "needs_reauth",
            "mezan_selected": True,
        },
    ])

    assert await selected_snapchat_scheduler_targets(db) == {
        ("owner-snap", "snapchat_ads")
    }
    assert await augment_auto_sync_targets(db, []) == [
        ("owner-snap", "snapchat_ads")
    ]

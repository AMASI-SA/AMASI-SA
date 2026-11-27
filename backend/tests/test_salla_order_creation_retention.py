"""Synthetic quote privacy TTL acceptance without real Mongo access."""

import asyncio
from types import SimpleNamespace

import pytest

from salla_order_creation.retention import (
    INDEX_NAME, RetentionNotReady,
    install_assisted_quote_ttl, require_assisted_quote_ttl,
)


class Quotes:
    def __init__(self, indexes=None, failure=False):
        self.indexes=indexes or {}
        self.fail=failure
        self.created=[]

    async def index_information(self):
        if self.fail:
            raise PermissionError("synthetic")
        return dict(self.indexes)

    async def create_index(self, keys, **options):
        self.created.append((list(keys),dict(options)))
        self.indexes[options["name"]]={
            "key":list(keys),"expireAfterSeconds":options["expireAfterSeconds"],
        }
        return options["name"]


def db(indexes=None, failure=False):
    return SimpleNamespace(salla_ai_order_quotes=Quotes(indexes,failure))


def test_correct_existing_ttl_allows_privacy_scoped_write():
    d=db({
        INDEX_NAME:{
            "key":[("purge_after",1)],
            "expireAfterSeconds":0,
        },
    })
    asyncio.run(require_assisted_quote_ttl(d))
    assert d.salla_ai_order_quotes.created==[]


def test_missing_ttl_denies_without_creating_anything():
    d=db()
    with pytest.raises(RetentionNotReady,match="ttl_missing"):
        asyncio.run(require_assisted_quote_ttl(d))
    assert d.salla_ai_order_quotes.created==[]


@pytest.mark.parametrize("unsafe",[
    {"key":[("created_at",1)],"expireAfterSeconds":0},
    {"key":[("purge_after",1)],"expireAfterSeconds":3600},
    {"key":[("purge_after",-1)],"expireAfterSeconds":0},
    {"key":[("purge_after",1),("user_id",1)],"expireAfterSeconds":0},
])
def test_wrong_ttl_cannot_be_treated_as_privacy_retention(unsafe):
    d=db({INDEX_NAME:unsafe})
    with pytest.raises(RetentionNotReady):
        asyncio.run(require_assisted_quote_ttl(d))


def test_explicit_installer_only_creates_targeted_temporary_quote_index():
    d=db()
    name=asyncio.run(install_assisted_quote_ttl(d))
    assert name==INDEX_NAME
    assert d.salla_ai_order_quotes.created==[
        ([("purge_after",1)],{"expireAfterSeconds":0,"name":INDEX_NAME}),
    ]


def test_index_permission_failure_denies_quote_write():
    d=db(failure=True)
    with pytest.raises(RetentionNotReady,match="index_unreadable"):
        asyncio.run(require_assisted_quote_ttl(d))

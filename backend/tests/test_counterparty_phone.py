"""Existing counterparty endpoint phone regression checks; no external database."""
from copy import deepcopy
from uuid import UUID

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import counterparties_routes


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, _sort):
        return self

    async def to_list(self, maximum):
        return deepcopy(self.rows[:maximum])


class Counterparties:
    def __init__(self):
        self.rows = []
        self.writes = []

    def find(self, query, _projection):
        return Cursor([row for row in self.rows if all(row.get(key) == value for key, value in query.items())])

    async def find_one(self, query, projection):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))
        self.writes.append(("insert", deepcopy(row)))

    async def update_one(self, query, update):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(deepcopy(update["$set"]))
                self.writes.append(("update", deepcopy(update["$set"])))


class ContactOnlyDatabase:
    """Any access to a ledger/settings/financial collection fails the test."""
    def __init__(self):
        self.counterparties = Counterparties()

    def __getattr__(self, key):
        raise AssertionError(f"Unexpected collection access: {key}")


@pytest.fixture
def contact_api(monkeypatch):
    db = ContactOnlyDatabase()
    actor = {"id": "owner"}

    async def authenticated_user(_request, _db):
        return dict(actor)

    monkeypatch.setattr(counterparties_routes, "get_current_user_from_db", authenticated_user)
    router = APIRouter(prefix="/api")
    counterparties_routes.attach_counterparties_routes(router, db)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, db.counterparties, actor


def test_create_select_edit_phone_on_existing_general_counterparty_endpoint(contact_api):
    client, contacts, _ = contact_api
    response = client.post("/api/counterparties", json={"kind": "general", "name": "External person", "phone": " +966 555 123 456 ", "notes": "Contact notes"})
    assert response.status_code == 200
    person = response.json()
    assert str(UUID(person["id"])) == person["id"]
    assert person["phone"] == "+966 555 123 456"
    assert person["kind"] == "general"
    selected = client.get("/api/counterparties?kind=general").json()["items"]
    assert selected == [person]
    updated = client.put(f"/api/counterparties/{person['id']}", json={"phone": " 0500000000 "})
    assert updated.status_code == 200
    assert updated.json()["phone"] == "0500000000"
    assert updated.json()["id"] == person["id"]
    assert set(contacts.writes[-1][1]) == {"phone", "updated_at"}


def test_omitted_or_null_phone_preserves_existing_and_empty_string_clears(contact_api):
    client, contacts, _ = contact_api
    person = client.post("/api/counterparties", json={"kind": "general", "name": "Person", "phone": "123"}).json()
    path = f"/api/counterparties/{person['id']}"
    assert client.put(path, json={"notes": "new"}).json()["phone"] == "123"
    assert client.put(path, json={"phone": None}).json()["phone"] == "123"
    assert client.put(path, json={"phone": ""}).json()["phone"] == ""
    old_client = client.post("/api/counterparties", json={"kind": "general", "name": "Unrelated contact"})
    assert old_client.status_code == 200 and "phone" not in old_client.json()


def test_phone_length_bound_and_ownership_checks_precede_writes(contact_api):
    client, contacts, actor = contact_api
    invalid = client.post("/api/counterparties", json={"kind": "general", "name": "P", "phone": "1" * 41})
    assert invalid.status_code == 422 and not contacts.writes
    person = client.post("/api/counterparties", json={"kind": "general", "name": "Person", "phone": "123"}).json()
    count = len(contacts.writes)
    path = f"/api/counterparties/{person['id']}"
    assert client.put(path, json={"phone": "1" * 41}).status_code == 422
    actor["id"] = "other-owner"
    assert client.put(path, json={"phone": "456"}).status_code == 404
    assert client.get("/api/counterparties?kind=general").json()["items"] == []
    assert len(contacts.writes) == count


def test_phone_does_not_bypass_duplicate_or_kind_rules(contact_api):
    client, contacts, _ = contact_api
    first = {"kind": "general", "name": "Same person", "phone": "123"}
    assert client.post("/api/counterparties", json=first).status_code == 200
    assert client.post("/api/counterparties", json={**first, "phone": "456", "force": True}).status_code == 409
    assert client.post("/api/counterparties", json={"kind": "invalid", "name": "P", "phone": "123"}).status_code == 400
    assert client.post("/api/counterparties", json={"kind": "ad_account", "name": "P", "phone": "123"}).status_code == 400
    assert len(contacts.writes) == 1

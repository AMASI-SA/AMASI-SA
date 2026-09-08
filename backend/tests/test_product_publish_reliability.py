import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

import product_control_center_routes as module
from product_control_center_routes import DRAFTS, PRODUCTS, REVISIONS
from product_v2_details_routes import make_product_v2_details_router

ATTEMPTS = getattr(module, "ATTEMPTS", "mezan_product_publish_attempts_v2")


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            continue
        if actual != expected:
            return False
    return True


def _apply_update(document, update, inserted=False):
    if inserted:
        document.update(deepcopy(update.get("$setOnInsert") or {}))
    document.update(deepcopy(update.get("$set") or {}))
    for key in update.get("$unset") or {}:
        document.pop(key, None)
    for key, amount in (update.get("$inc") or {}).items():
        document[key] = int(document.get(key) or 0) + amount


class _Result:
    def __init__(self, *, matched=0, modified=0, inserted_id=None):
        self.matched_count = matched
        self.modified_count = modified
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction=None):
        if isinstance(field, list):
            specs = field
        else:
            specs = [(field, direction)]
        for key, order in reversed(specs):
            self.documents.sort(key=lambda row: row.get(key) or datetime.min, reverse=order == -1)
        return self

    async def to_list(self, length):
        return deepcopy(self.documents[:length])


class _Collection:
    def __init__(self, documents=None):
        self.documents = [deepcopy(row) for row in (documents or [])]
        self.lock = asyncio.Lock()

    async def create_index(self, *_args, **_kwargs):
        return "index"

    async def find_one(self, query, projection=None, sort=None):
        rows = [row for row in self.documents if _matches(row, query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda row: row.get(key) or datetime.min, reverse=direction == -1)
        return deepcopy(rows[0]) if rows else None

    async def insert_one(self, document):
        async with self.lock:
            if any(
                row.get("user_id") == document.get("user_id")
                and row.get("draft_id") == document.get("draft_id")
                for row in self.documents
                if document.get("draft_id")
            ):
                raise module.DuplicateKeyError("duplicate")
            self.documents.append(deepcopy(document))
        return _Result(inserted_id=document.get("id"))

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):
        async with self.lock:
            for row in self.documents:
                if _matches(row, query):
                    before = deepcopy(row)
                    _apply_update(row, update)
                    return deepcopy(row if return_document else before)
            if not upsert:
                return None
            row = {key: deepcopy(value) for key, value in query.items() if not key.startswith("$") and not isinstance(value, dict)}
            _apply_update(row, update, inserted=True)
            self.documents.append(row)
            return deepcopy(row)

    async def update_one(self, query, update, upsert=False):
        async with self.lock:
            for row in self.documents:
                if _matches(row, query):
                    _apply_update(row, update)
                    return _Result(matched=1, modified=1)
            if upsert:
                row = {key: deepcopy(value) for key, value in query.items() if not key.startswith("$") and not isinstance(value, dict)}
                _apply_update(row, update, inserted=True)
                self.documents.append(row)
                return _Result(matched=0, modified=0, inserted_id=row.get("id"))
        return _Result()

    async def update_many(self, query, update):
        modified = 0
        for row in self.documents:
            if _matches(row, query):
                _apply_update(row, update)
                modified += 1
        return _Result(matched=modified, modified=modified)

    def find(self, query, projection=None):
        return _Cursor([deepcopy(row) for row in self.documents if _matches(row, query)])


class _Db:
    def __init__(self):
        now = module._now()
        self.collections = {
            PRODUCTS: _Collection([{
                "id": "mpv2-1",
                "mezan_product_id": "mpv2-1",
                "salla_product_id": "salla-1",
                "user_id": "user-1",
                "name": "old",
                "price": 100,
                "sale_price": 90,
            }]),
            DRAFTS: _Collection([{
                "id": "draft-1",
                "user_id": "user-1",
                "salla_product_id": "salla-1",
                "mezan_product_id": "mpv2-1",
                "status": "approved",
                "changes": {"name": "new"},
                "before": {"name": "old"},
                "source": "human",
                "reason": "test",
                "updated_at": now,
            }]),
            ATTEMPTS: _Collection(),
            REVISIONS: _Collection(),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


def _route(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if isinstance(route, APIRoute) and route.path == path and method in route.methods
    )


def _payload(response):
    if isinstance(response, JSONResponse):
        return json.loads(response.body)
    return response


def _publish_endpoint(db):
    router = module.make_product_control_center_router(db, lambda: {"id": "user-1"})
    return _route(router, "/products-v2/{product_id}/control-center/draft/{draft_id}/publish", "POST")


def _verify_endpoint(db):
    router = module.make_product_control_center_router(db, lambda: {"id": "user-1"})
    return _route(router, "/products-v2/{product_id}/control-center/publish-attempt/{attempt_id}/verify", "POST")


async def _publish(endpoint):
    return await endpoint(
        "mpv2-1",
        "draft-1",
        {"confirmation": "نشر التعديل إلى سلة"},
        {"id": "user-1"},
    )


def test_normal_publish_is_durable_and_uses_one_get_put_get(monkeypatch):
    db = _Db()
    calls = []

    async def provider(_db, _user, method, _path, **kwargs):
        calls.append((method, kwargs.get("json")))
        if method == "PUT":
            return {"status": 200, "success": True}
        return {"data": {"name": "old" if len(calls) == 1 else "new", "price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    result = _payload(asyncio.run(_publish(_publish_endpoint(db))))

    assert [method for method, _ in calls] == ["GET", "PUT", "GET"]
    assert result["attempt"]["status"] == "succeeded"
    assert result["attempt"]["provider_write_acknowledged_at"]
    assert len(db[ATTEMPTS].documents) == 1
    assert len(db[REVISIONS].documents) == 1


def test_two_concurrent_publish_clicks_share_one_attempt_and_one_put(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        await asyncio.sleep(0)
        return {"data": {"name": "new", "price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)

    async def scenario():
        endpoint = _publish_endpoint(db)
        return await asyncio.gather(_publish(endpoint), _publish(endpoint))

    responses = [_payload(row) for row in asyncio.run(scenario())]
    assert methods.count("PUT") == 1
    assert len(db[ATTEMPTS].documents) == 1
    assert len({row["attempt"]["id"] for row in responses}) == 1


def test_prewrite_timeout_is_retryable_and_never_puts(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        raise asyncio.TimeoutError

    monkeypatch.setattr(module, "call_salla", provider)
    response = asyncio.run(_publish(_publish_endpoint(db)))
    result = _payload(response)

    assert response.status_code == 503
    assert result["code"] == "salla_publish_failed_before_write"
    assert result["stage"] == "preparing"
    assert result["retryable"] is True
    assert result["outcome"] == "failed_before_write"
    assert methods == ["GET"]


def test_put_timeout_is_unknown_and_retry_does_not_send_second_put(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        if method == "GET":
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        raise asyncio.TimeoutError

    monkeypatch.setattr(module, "call_salla", provider)

    async def scenario():
        endpoint = _publish_endpoint(db)
        first = await _publish(endpoint)
        second = await _publish(endpoint)
        return first, second

    first, second = (_payload(row) for row in asyncio.run(scenario()))
    assert first["outcome"] == "outcome_unknown"
    assert first["retryable"] is False
    assert second["attempt"]["id"] == first["attempt"]["id"]
    assert methods.count("PUT") == 1


def test_acknowledged_put_with_verify_timeout_becomes_verification_pending(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        if methods == ["GET"]:
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        if method == "PUT":
            return {"status": 200}
        raise asyncio.TimeoutError

    monkeypatch.setattr(module, "call_salla", provider)
    result = _payload(asyncio.run(_publish(_publish_endpoint(db))))

    assert result["status"] == "verification_pending"
    assert result["outcome"] == "verification_pending"
    assert result["retryable"] is True
    assert methods == ["GET", "PUT", "GET"]


def test_verification_retry_is_get_only_and_later_match_succeeds(monkeypatch):
    db = _Db()
    attempt = {
        "id": "attempt-1",
        "draft_id": "draft-1",
        "user_id": "user-1",
        "product_id": "mpv2-1",
        "salla_product_id": "salla-1",
        "stage": "verifying",
        "status": "verification_pending",
        "outcome": "verification_pending",
        "expected_projection": {"name": "new", "price": 100.0, "sale_price": 90.0},
        "expected_projection_hash": "hash",
        "rollback_projection": {"price": 100.0, "sale_price": 90.0},
        "patch": {"name": "new"},
        "before": {"name": "old"},
        "provider_write_acknowledged_at": module._now(),
        "verification_attempts": 1,
        "updated_at": module._now(),
    }
    db[ATTEMPTS].documents.append(attempt)
    calls = []

    async def provider(_db, _user, method, _path, **_kwargs):
        calls.append(method)
        return {"data": {"name": "new", "price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    endpoint = _verify_endpoint(db)
    response = asyncio.run(endpoint("mpv2-1", "attempt-1", {"id": "user-1"}))
    result = _payload(response)

    assert calls == ["GET"]
    assert result["attempt"]["status"] == "succeeded"
    assert len(db[REVISIONS].documents) == 1


def test_explicit_mismatch_is_the_only_path_that_invokes_controlled_rollback(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **kwargs):
        methods.append(method)
        if methods == ["GET"]:
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        if methods == ["GET", "PUT"]:
            return {"status": 200}
        if methods == ["GET", "PUT", "GET"]:
            return {"data": {"name": "new", "price": 1, "sale_price": 1}}
        if methods == ["GET", "PUT", "GET", "PUT"]:
            assert kwargs["json"] == {"price": 100.0, "sale_price": 90.0}
            return {"status": 200}
        return {"data": {"price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    result = _payload(asyncio.run(_publish(_publish_endpoint(db))))

    assert methods == ["GET", "PUT", "GET", "PUT", "GET"]
    assert result["status"] == "rolled_back"
    assert result["outcome"] == "rolled_back"


def test_rollback_timeout_never_claims_success(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        if methods == ["GET"]:
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        if methods == ["GET", "PUT"]:
            return {"status": 200}
        if methods == ["GET", "PUT", "GET"]:
            return {"data": {"name": "new", "price": 1, "sale_price": 1}}
        raise asyncio.TimeoutError

    monkeypatch.setattr(module, "call_salla", provider)
    result = _payload(asyncio.run(_publish(_publish_endpoint(db))))

    assert methods == ["GET", "PUT", "GET", "PUT"]
    assert result["status"] == "rollback_required"
    assert result["outcome"] == "outcome_unknown"


def test_hung_provider_returns_bounded_structured_state(monkeypatch):
    db = _Db()

    async def provider(*_args, **_kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(module, "call_salla", provider)
    monkeypatch.setattr(module, "PRODUCT_PROVIDER_READ_TIMEOUT_SECONDS", 0.01)
    response = asyncio.run(asyncio.wait_for(_publish(_publish_endpoint(db)), timeout=0.2))
    result = _payload(response)

    assert result["code"] == "salla_publish_failed_before_write"
    assert result["attempt_id"]


def test_local_mezan_cost_save_never_calls_salla(monkeypatch):
    db = _Db()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Salla must not be called by Mezan cost save")

    monkeypatch.setattr("product_v2_details_routes.call_salla", forbidden)
    router = make_product_v2_details_router(db, lambda: {"id": "user-1"})
    endpoint = _route(router, "/products-v2/{product_id}/costs", "PUT")
    result = asyncio.run(endpoint(
        "mpv2-1",
        {"base_cost": 22, "variant_costs": {}, "notes": "local"},
        {"id": "user-1"},
    ))

    assert result["ok"] is True
    assert result["base_cost"] == 22


@pytest.mark.parametrize(
    ("status_code", "provider_code", "retryable"),
    [(429, "salla_rate_limited", True), (503, "salla_unavailable", True)],
)
def test_provider_http_failures_before_write_are_structured(
    monkeypatch, status_code, provider_code, retryable,
):
    db = _Db()

    async def provider(*_args, **_kwargs):
        raise module.SallaError("provider failure", status_code=status_code)

    monkeypatch.setattr(module, "call_salla", provider)
    response = asyncio.run(_publish(_publish_endpoint(db)))
    result = _payload(response)

    assert response.status_code == status_code
    assert result["stage"] == "preparing"
    assert result["attempt"]["last_provider_error_code"] == provider_code
    assert result["retryable"] is retryable


def test_connection_reset_during_put_is_unknown_and_not_replayed(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        if method == "GET":
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        raise ConnectionResetError("upstream closed after send")

    monkeypatch.setattr(module, "call_salla", provider)
    endpoint = _publish_endpoint(db)
    first = _payload(asyncio.run(_publish(endpoint)))
    second = _payload(asyncio.run(_publish(endpoint)))

    assert first["status"] == "outcome_unknown"
    assert second["attempt_id"] == first["attempt_id"]
    assert methods == ["GET", "PUT"]


def test_verify_only_mismatch_never_performs_a_write(monkeypatch):
    db = _Db()
    db[ATTEMPTS].documents.append({
        "id": "attempt-mismatch",
        "draft_id": "draft-1",
        "user_id": "user-1",
        "product_id": "mpv2-1",
        "salla_product_id": "salla-1",
        "stage": "verifying",
        "status": "verification_pending",
        "outcome": "verification_pending",
        "expected_projection": {"name": "new", "price": 100.0},
        "expected_projection_hash": "hash",
        "rollback_projection": {"price": 100.0, "sale_price": 90.0},
        "patch": {"name": "new"},
        "before": {"name": "old"},
        "provider_write_acknowledged_at": module._now(),
        "verification_attempts": 1,
        "updated_at": module._now(),
    })
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        return {"data": {"name": "wrong", "price": 1, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    endpoint = _verify_endpoint(db)
    result = _payload(asyncio.run(endpoint(
        "mpv2-1", "attempt-mismatch", {"id": "user-1"},
    )))

    assert methods == ["GET"]
    assert result["status"] == "rollback_required"
    assert result["outcome"] == "mismatch_confirmed"


def test_non_price_mismatch_never_attempts_price_only_rollback(monkeypatch):
    db = _Db()
    methods = []

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        if methods == ["GET"]:
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        if method == "PUT":
            return {"status": 200}
        return {"data": {"name": "wrong", "price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    result = _payload(asyncio.run(_publish(_publish_endpoint(db))))

    assert methods == ["GET", "PUT", "GET"]
    assert result["status"] == "rollback_required"
    assert result["outcome"] == "mismatch_confirmed"


def test_google_taxonomy_mezan_managed_skip_finishes_without_false_mismatch(monkeypatch):
    db = _Db()
    db[DRAFTS].documents[0]["changes"] = {"google_category": "201"}
    db[DRAFTS].documents[0]["before"] = {"google_category": None}
    methods = []

    async def provider(_db, _user, method, _path, **kwargs):
        methods.append(method)
        if method == "PUT":
            assert kwargs["json"] == {"google_product_category": "201"}
            return {"skipped": True, "reason": "google_taxonomy_mezan_managed"}
        return {"data": {"name": "old", "price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    result = _payload(asyncio.run(_publish(_publish_endpoint(db))))

    assert methods == ["GET", "PUT"]
    assert result["status"] == "succeeded"
    assert result["revision"]["salla_response"]["reason"] == "google_taxonomy_mezan_managed"


def test_timed_out_verification_reconciles_later_with_one_total_put(monkeypatch):
    db = _Db()
    methods = []
    phase = {"verify_timeout": True}

    async def provider(_db, _user, method, _path, **_kwargs):
        methods.append(method)
        if method == "PUT":
            return {"status": 200}
        if methods == ["GET"]:
            return {"data": {"name": "old", "price": 100, "sale_price": 90}}
        if phase["verify_timeout"]:
            phase["verify_timeout"] = False
            raise asyncio.TimeoutError
        return {"data": {"name": "new", "price": 100, "sale_price": 90}}

    monkeypatch.setattr(module, "call_salla", provider)
    first = _payload(asyncio.run(_publish(_publish_endpoint(db))))
    assert first["status"] == "verification_pending"
    db[ATTEMPTS].documents[0]["next_verification_at"] = module._now() - timedelta(seconds=1)

    endpoint = _verify_endpoint(db)
    second = _payload(asyncio.run(endpoint(
        "mpv2-1", first["attempt_id"], {"id": "user-1"},
    )))

    assert second["status"] == "succeeded"
    assert methods == ["GET", "PUT", "GET", "GET"]
    assert methods.count("PUT") == 1

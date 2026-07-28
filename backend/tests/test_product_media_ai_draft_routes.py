import asyncio
from copy import deepcopy

from starlette.requests import Request

from product_media_ai_draft_routes import make_product_media_ai_draft_router
from product_media_ai_routes import AI_MEDIA_JOBS
from product_media_draft_routes import MEDIA_DRAFTS
from product_media_upload_routes import MEDIA_UPLOADS
from product_v2_routes import PRODUCTS


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        value = row.get(key)
        if isinstance(expected, dict):
            for operator, operand in expected.items():
                if operator == "$in" and value not in operand:
                    return False
                if operator == "$exists" and (key in row) != bool(operand):
                    return False
                if operator == "$ne" and value == operand:
                    return False
        elif value != expected:
            return False
    return True


class UpdateResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class Collection:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query, projection=None, sort=None):
        matches = [row for row in self.rows if _matches(row, query)]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda row: str(row.get(key) or ""), reverse=direction < 0)
        if not matches:
            return None
        row = deepcopy(matches[0])
        if projection and any(projection.values()):
            row = {key: value for key, value in row.items() if projection.get(key)}
        row.pop("_id", None)
        return row

    async def update_one(self, query, update):
        row = next((item for item in self.rows if _matches(item, query)), None)
        if not row:
            return UpdateResult(0)
        row.update(deepcopy(update.get("$set") or {}))
        for key in (update.get("$unset") or {}):
            row.pop(key, None)
        return UpdateResult(1)

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        return object()


class DB:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, name):
        return Collection(self.rows.setdefault(name, []))


async def current_user():
    return {"id": "owner-1", "role": "owner", "name": "Owner"}


def _request():
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/products-v2/p1/media-ai/jobs/j1/add-to-draft",
        "raw_path": b"/api/products-v2/p1/media-ai/jobs/j1/add-to-draft",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("mezansalla.com", 443),
        "root_path": "",
    })


def _endpoint(db):
    router = make_product_media_ai_draft_router(db, current_user)
    return next(route.endpoint for route in router.routes if route.path.endswith("/add-to-draft"))


def _base_rows():
    return {
        PRODUCTS: [{
            "id": "p1",
            "mezan_product_id": "p1",
            "salla_product_id": "s1",
            "user_id": "owner-1",
            "images": [{
                "id": "10",
                "url": "https://cdn.salla.sa/original.jpg",
                "alt": "",
                "is_main": True,
                "source": "salla",
            }],
        }],
        AI_MEDIA_JOBS: [{
            "id": "j1",
            "user_id": "owner-1",
            "salla_product_id": "s1",
            "status": "completed",
            "operation": "remove_background",
            "operation_label": "إزالة الخلفية",
            "result_image": {
                "url": "/api/products-v2/media-upload/file/temp-token",
                "upload_token": "temp-token",
                "source": "temporary_upload",
                "filename": "result.webp",
                "is_main": False,
            },
        }],
        MEDIA_UPLOADS: [{
            "token": "temp-token",
            "user_id": "owner-1",
            "salla_product_id": "s1",
        }],
        MEDIA_DRAFTS: [],
        "mezan_role_assignments_v2": [],
        "mezan_ai_action_log_v2": [],
    }


def test_completed_ai_image_creates_draft_without_publishing():
    rows = _base_rows()
    db = DB(rows)
    result = asyncio.run(_endpoint(db)("p1", "j1", _request(), {"id": "owner-1", "role": "owner", "name": "Owner"}))
    assert result["direct_publish"] is False
    assert result["draft"]["status"] == "draft"
    assert len(result["draft"]["images"]) == 2
    generated = result["draft"]["images"][1]
    assert generated["source"] == "temporary_upload"
    assert generated["url"].startswith("https://mezansalla.com/api/")
    assert rows[AI_MEDIA_JOBS][0]["status"] == "added_to_draft"
    assert len(rows[MEDIA_DRAFTS]) == 1


def test_approved_media_draft_blocks_ai_result_insertion():
    rows = _base_rows()
    rows[MEDIA_DRAFTS].append({
        "id": "approved-1",
        "user_id": "owner-1",
        "salla_product_id": "s1",
        "status": "approved",
        "updated_at": "2026-07-28T00:00:00+00:00",
        "images": rows[PRODUCTS][0]["images"],
    })
    db = DB(rows)
    try:
        asyncio.run(_endpoint(db)("p1", "j1", _request(), {"id": "owner-1", "role": "owner", "name": "Owner"}))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert exc.detail["code"] == "approved_media_draft_exists"
    else:
        raise AssertionError("approved draft should block insertion")

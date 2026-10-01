import hashlib
import io
from unittest.mock import AsyncMock
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from accounting_source_files import preserve_original, original_for_draft, install_accounting_source_file_routes


def workbook():
    book = openpyxl.Workbook()
    book.active.append(["ORIGINAL", 123.45])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_original_bytes_immutable_and_duplicate_conflict():
    content = workbook()
    collection = SimpleNamespace(insert_one=AsyncMock(), find_one=AsyncMock(return_value=None))
    db = SimpleNamespace(accounting_source_files=collection)
    digest = await preserve_original(db, "owner", "file", content)
    doc = collection.insert_one.call_args.args[0]
    assert bytes(doc["content"]) == content
    assert digest == hashlib.sha256(content).hexdigest()
    assert collection.find_one.call_args.args[0] == {"_id": doc["_id"], "user_id": "owner"}
    collection.find_one.return_value = doc
    assert await preserve_original(db, "owner", "file", content) == digest
    collection.insert_one.assert_awaited_once()
    with pytest.raises(ValueError, match="تعارض"):
        await preserve_original(db, "owner", "file", b"different")
    collection.insert_one.assert_awaited_once()

    # A second writer may insert after the initial lookup. Preserve coverage of
    # the duplicate-key race as well as the early existing-document path.
    collection.insert_one.side_effect = DuplicateKeyError("same")
    collection.find_one.side_effect = [None, doc]
    assert await preserve_original(db, "owner", "file", content) == digest
    assert collection.insert_one.await_count == 2
    collection.find_one.side_effect = [None, doc]
    with pytest.raises(ValueError, match="تعارض"):
        await preserve_original(db, "owner", "file", b"different")
    assert collection.insert_one.await_count == 3

    # Matching stored digest alone cannot authorize altered original bytes.
    collection.find_one.side_effect = None
    collection.find_one.return_value = {**doc, "content": b"corrupt"}
    with pytest.raises(ValueError, match="تعارض"):
        await preserve_original(db, "owner", "file", content)
    assert collection.insert_one.await_count == 3
    assert bytes(doc["content"]) == content


@pytest.mark.asyncio
async def test_tenant_and_missing_original_fail_closed():
    drafts = SimpleNamespace(find_one=AsyncMock(return_value=None))
    db = SimpleNamespace(accounting_settlements_v2=drafts)
    with pytest.raises(HTTPException) as exc:
        await original_for_draft(db, "other", "draft")
    assert exc.value.status_code == 404
    assert drafts.find_one.call_args.args[0] == {"id": "draft", "user_id": "other"}
    drafts.find_one.return_value = {"source_file_id": "file"}
    db.settlement_files = SimpleNamespace(find_one=AsyncMock(return_value={"id": "file"}))
    db.accounting_source_files = SimpleNamespace(find_one=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await original_for_draft(db, "owner", "draft")
    assert exc.value.detail["code"] == "original_file_unavailable"


def test_authenticated_download_and_preview_same_bytes(monkeypatch):
    content = workbook()
    digest = hashlib.sha256(content).hexdigest()
    db = SimpleNamespace(
        accounting_settlements_v2=SimpleNamespace(find_one=AsyncMock(return_value={"source_file_id": "file"})),
        settlement_files=SimpleNamespace(find_one=AsyncMock(return_value={"filename": "أصل.xlsx", "file_hash": digest})),
        accounting_source_files=SimpleNamespace(find_one=AsyncMock(return_value={"content": content, "sha256": digest})),
    )
    actor = {"id": "viewer", "created_by": "owner", "role": "viewer",
             "accounting_permissions": ["accounting.settlements.view"]}
    async def current(): return actor
    async def fresh(db, user): return user
    monkeypatch.setattr("accounting_source_files.fresh_accounting_user", fresh)
    app = FastAPI()
    install_accounting_source_file_routes(app, db, current)
    client = TestClient(app)
    base = "/accounting-module/settlements/drafts/draft/original"
    response = client.get(base)
    assert response.status_code == 200 and response.content == content
    assert response.headers["x-content-sha256"] == digest
    assert "no-store" in response.headers["cache-control"]
    preview = client.get(base + "/preview").json()
    assert preview["sha256"] == digest
    assert preview["sheets"][0]["rows"][0][:2] == ["ORIGINAL", "123.45"]
    actor["accounting_permissions"] = []
    db.accounting_settlements_v2.find_one.reset_mock()
    assert client.get(base).status_code == 403
    db.accounting_settlements_v2.find_one.assert_not_called()


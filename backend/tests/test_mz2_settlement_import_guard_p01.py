import pytest

import accounting_settlement_import_guard as guard


class Workbook:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_guard_rejects_file_that_does_not_match_selected_provider(monkeypatch):
    workbook = Workbook()
    base_called = False

    monkeypatch.setattr(guard.openpyxl, "load_workbook", lambda *_args, **_kwargs: workbook)
    monkeypatch.setattr(guard, "detect_provider", lambda _workbook: "tabby")

    async def base_import(*_args, **_kwargs):
        nonlocal base_called
        base_called = True
        return {"provider": "tabby"}

    monkeypatch.setattr(guard, "_base_import_file", base_import)

    with pytest.raises(ValueError) as error:
        await guard.import_accounting_settlement_file(
            object(),
            "owner-1",
            filename="statement.xlsx",
            content=b"xlsx",
            provider_hint="tamara",
        )
    assert "اخترت تمارا" in str(error.value)
    assert "تابي" in str(error.value)
    assert base_called is False
    assert workbook.closed is True


@pytest.mark.asyncio
async def test_guard_passes_detected_provider_to_existing_importer(monkeypatch):
    workbook = Workbook()
    captured = {}

    monkeypatch.setattr(guard.openpyxl, "load_workbook", lambda *_args, **_kwargs: workbook)
    monkeypatch.setattr(guard, "detect_provider", lambda _workbook: "imkan")

    async def base_import(db, user_id, **kwargs):
        captured.update({"db": db, "user_id": user_id, **kwargs})
        return {"status": "imported", "provider": "emkan", "file_id": "file-1"}

    monkeypatch.setattr(guard, "_base_import_file", base_import)
    db = object()
    result = await guard.import_accounting_settlement_file(
        db,
        "owner-1",
        filename="emkan.xlsx",
        content=b"xlsx",
        provider_hint="emkan",
    )
    assert result["provider"] == "emkan"
    assert captured["db"] is db
    assert captured["user_id"] == "owner-1"
    assert captured["provider_hint"] == "emkan"
    assert workbook.closed is True

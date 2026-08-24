import pytest

import accounting_settlement_import_guard as guard


class Sheet:
    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, values_only=True):
        assert values_only is True
        return iter(self.rows)


class Workbook:
    def __init__(self, rows=None):
        self.closed = False
        self.worksheets = [Sheet(rows)] if rows is not None else []

    def close(self):
        self.closed = True


class SettlementFiles:
    def __init__(self):
        self.calls = []

    async def update_one(self, query, update):
        self.calls.append((query, update))


class DB:
    def __init__(self):
        self.settlement_files = SettlementFiles()


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
async def test_guard_rejects_explicit_non_sar_currency_before_base_import(monkeypatch):
    workbook = Workbook([
        ["Order Number", "Currency", "Transferred amount"],
        ["1001", "USD", 90.0],
    ])
    base_called = False

    monkeypatch.setattr(guard.openpyxl, "load_workbook", lambda *_args, **_kwargs: workbook)
    monkeypatch.setattr(guard, "detect_provider", lambda _workbook: "tabby")

    async def base_import(*_args, **_kwargs):
        nonlocal base_called
        base_called = True
        return {"provider": "tabby", "file_id": "file-1"}

    monkeypatch.setattr(guard, "_base_import_file", base_import)

    with pytest.raises(ValueError) as error:
        await guard.import_accounting_settlement_file(
            DB(),
            "owner-1",
            filename="tabby-usd.xlsx",
            content=b"xlsx",
            provider_hint="tabby",
        )
    assert "USD" in str(error.value)
    assert "SAR" in str(error.value)
    assert base_called is False
    assert workbook.closed is True


@pytest.mark.asyncio
async def test_guard_persists_explicit_sar_and_provenance_after_import(monkeypatch):
    workbook = Workbook([
        ["Merchant Order ID", "Currency", "Total Payable to Merchant"],
        ["280001", "SAR", 95.0],
    ])
    monkeypatch.setattr(guard.openpyxl, "load_workbook", lambda *_args, **_kwargs: workbook)
    monkeypatch.setattr(guard, "detect_provider", lambda _workbook: "tamara")

    async def base_import(*_args, **_kwargs):
        return {"status": "imported", "provider": "tamara", "file_id": "file-1"}

    monkeypatch.setattr(guard, "_base_import_file", base_import)
    db = DB()
    result = await guard.import_accounting_settlement_file(
        db,
        "owner-1",
        filename="tamara.xlsx",
        content=b"xlsx",
        provider_hint="tamara",
    )

    assert result["currency"] == "SAR"
    assert "workbook.currency_column" in result["currency_source"]
    assert workbook.closed is True
    assert len(db.settlement_files.calls) == 1
    query, update = db.settlement_files.calls[0]
    assert query == {"id": "file-1", "user_id": "owner-1"}
    assert update["$set"]["currency"] == "SAR"
    assert update["$set"]["header.currency"] == "SAR"


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
    assert result["currency"] == "SAR"
    assert result["currency_source"] == "provider_contract_sar:emkan"
    assert captured["db"] is db
    assert captured["user_id"] == "owner-1"
    assert captured["provider_hint"] == "emkan"
    assert workbook.closed is True

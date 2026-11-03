"""Manager-only toggles: mandatory-by-default supplier/assembly shortcuts."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import assembly_mobile_settings as module
from mobile_operational_flow_policy import (
    REQUIRED, OPTIONAL, FIELD_PREPARATION, FIELD_SUPPLIER,
    read_modes, is_optional,
)


class Settings:
    def __init__(self, row=None):
        self.row = row or {}
        self.writes = []

    async def find_one(self, selector, projection=None, **kwargs):
        assert selector == {"user_id": "owner"}
        return self.row

    async def update_one(self, selector, update, **kwargs):
        assert selector == {"user_id": "owner"}
        assert kwargs.get("upsert") is True
        self.writes.append(update)
        if not self.row:
            self.row = {}
        for path, value in update.get("$set", {}).items():
            field, key = path.split(".", 1)
            self.row.setdefault(field, {})[key] = value
        return SimpleNamespace(modified_count=1)


class DB:
    def __init__(self, row=None):
        self.settings = Settings(row)


@pytest.mark.asyncio
async def test_missing_malformed_and_legacy_policy_are_mandatory():
    for row in (
        {},
        {"operational_flow": {}},
        {"operational_flow": {FIELD_PREPARATION: "surprise", FIELD_SUPPLIER: None}},
        {"operational_flow": "broken"},
    ):
        db = DB(row)
        assert await read_modes(db, "owner") == {
            FIELD_PREPARATION: REQUIRED, FIELD_SUPPLIER: REQUIRED,
        }
        assert not await is_optional(db, "owner", FIELD_PREPARATION)
        assert not await is_optional(db, "owner", FIELD_SUPPLIER)
    assert await read_modes(DB(), "") == {
        FIELD_PREPARATION: REQUIRED, FIELD_SUPPLIER: REQUIRED,
    }


@pytest.mark.asyncio
async def test_merchant_scoped_modes_are_independent():
    db = DB({"operational_flow": {
        FIELD_PREPARATION: OPTIONAL, FIELD_SUPPLIER: REQUIRED,
    }})
    assert await is_optional(db, "owner", FIELD_PREPARATION)
    assert not await is_optional(db, "owner", FIELD_SUPPLIER)
    assert not await is_optional(DB(), "owner", FIELD_PREPARATION)
    with pytest.raises(ValueError):
        await is_optional(db, "owner", "not_an_operational_setting")


def test_patch_contract_rejects_empty_null_and_unknown_values():
    for value in (
        {}, {"supplier_assignment_mode": None},
        {"supplier_assignment_mode": "unexpected"},
        {"preparation_employee_receipt_mode": False},
        {"show_salla_order_link": "optional"},
        {"unknown_toggle": "optional"},
    ):
        with pytest.raises(ValidationError):
            module.AssemblySettingsPatch.model_validate(value)
    assert module.AssemblySettingsPatch(
        preparation_employee_receipt_mode=OPTIONAL,
    ).model_fields_set == {FIELD_PREPARATION}
    assert module.AssemblySettingsPatch(
        show_salla_order_link=False,
    ).model_fields_set == {"show_salla_order_link"}


@pytest.mark.asyncio
async def test_manager_can_switch_each_setting_without_resetting_salla_link(monkeypatch):
    db = DB({"assembly_shipping": {"show_salla_order_link": True}})
    async def scope(_db, _user):
        return "owner", True
    monkeypatch.setattr(module, "_scope", scope)
    router = module.make_assembly_mobile_settings_router(db, lambda: None)
    patch = next(x.endpoint for x in router.routes
                 if x.path == "/assembly/settings" and "PATCH" in x.methods)
    read = next(x.endpoint for x in router.routes
                if x.path == "/assembly/settings" and "GET" in x.methods)
    original = await read(user={})
    assert original["show_salla_order_link"] is True
    assert original[FIELD_PREPARATION] == REQUIRED
    assert original[FIELD_SUPPLIER] == REQUIRED
    changed = await patch(module.AssemblySettingsPatch(
        preparation_employee_receipt_mode=OPTIONAL,
    ), user={})
    assert changed[FIELD_PREPARATION] == OPTIONAL
    assert changed[FIELD_SUPPLIER] == REQUIRED
    assert changed["show_salla_order_link"] is True
    assert db.settings.writes[-1]["$set"] == {
        "operational_flow.preparation_employee_receipt_mode": OPTIONAL,
    }
    changed = await patch(module.AssemblySettingsPatch(
        supplier_assignment_mode=OPTIONAL,
    ), user={})
    assert changed[FIELD_PREPARATION] == OPTIONAL
    assert changed[FIELD_SUPPLIER] == OPTIONAL
    assert changed["show_salla_order_link"] is True
    changed = await patch(module.AssemblySettingsPatch(
        preparation_employee_receipt_mode=REQUIRED,
    ), user={})
    assert changed[FIELD_PREPARATION] == REQUIRED
    assert changed[FIELD_SUPPLIER] == OPTIONAL


@pytest.mark.asyncio
async def test_employee_cannot_see_manager_policies_or_modify_them(monkeypatch):
    db = DB({"operational_flow": {
        FIELD_PREPARATION: OPTIONAL, FIELD_SUPPLIER: OPTIONAL,
    }})
    async def scope(_db, _user):
        return "owner", False
    monkeypatch.setattr(module, "_scope", scope)
    router = module.make_assembly_mobile_settings_router(db, lambda: None)
    patch = next(x.endpoint for x in router.routes
                 if x.path == "/assembly/settings" and "PATCH" in x.methods)
    read = next(x.endpoint for x in router.routes
                if x.path == "/assembly/settings" and "GET" in x.methods)
    current = await read(user={"id": "employee"})
    assert current["can_manage"] is False
    assert FIELD_PREPARATION not in current
    assert FIELD_SUPPLIER not in current
    with pytest.raises(HTTPException) as denied:
        await patch(module.AssemblySettingsPatch(
            supplier_assignment_mode=REQUIRED,
        ), user={"id": "employee"})
    assert denied.value.status_code == 403
    assert db.settings.writes == []

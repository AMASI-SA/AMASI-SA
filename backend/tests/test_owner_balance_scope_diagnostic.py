"""Read-only owner scope audit must not infer salary account identity."""
import importlib.util
from pathlib import Path
import asyncio


spec = importlib.util.spec_from_file_location(
    "owner_balance_scope_readonly",
    Path(__file__).resolve().parents[2] / "scripts" / "diagnose_owner_balance_scope_readonly.py",
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def limit(self, n):
        assert n == 2
        return self
    def max_time_ms(self, n):
        assert n == 2000
        return self
    async def to_list(self, length):
        assert length == 2
        return self.rows[:2]
    async def close(self):
        return None


class Users:
    def __init__(self, data):
        self.data = data
    def find(self, criteria, projection):
        assert "email" in criteria and "_id" in projection
        return Cursor(self.data)


class DB:
    def __init__(self, user):
        self.users = Users([user])
    def __getattr__(self, name):
        return name


def test_no_state_reports_database_context_not_absent_salary(monkeypatch):
    async def limited(collection, query):
        if collection == "mezan_employee_salary_contracts_v2":
            return 1
        if collection == "mezan_employees_v2":
            return 1
        return 0
    monkeypatch.setattr(probe, "_upto2", limited)
    result = asyncio.run(probe.audit(DB({"id": "owner", "role": "owner"}), "synthetic@example.test"))
    assert result["read_only"] is True
    assert result["result"] == "DATABASE_HAS_NO_OPERATIONAL_STATES_VERIFY_ACTIVE_RUNTIME_DB"
    assert result["sources"]["v2_salary_contracts"] == 1
    assert result["sources"]["any_ledger_state_in_this_database"] == 0
    assert "owner" not in str(result).replace("owner_account_unique", "").replace("owner_reverse_link_field_present", "").replace("owner_direct_v2_links", "").replace("owner_reverse_v2_links", "").replace("owner_ledger_state_exact", "").replace("owner_ledger_by_owner_id", "").replace("owner_general_ledger_entries", "").replace("owner_employee_financial_events", "").replace("SELECTED_OWNER_SCOPE", "")


def test_other_tenant_state_does_not_prove_owner_ledger(monkeypatch):
    async def limited(collection, query):
        if collection == "operational_balance_states_v1" and not query:
            return 1
        return 0
    monkeypatch.setattr(probe, "_upto2", limited)
    result = asyncio.run(probe.audit(DB({"id": "owner", "role": "owner"}), "synthetic@example.test"))
    assert result["result"] == "OWNER_LEDGER_NOT_FOUND_IN_SELECTED_DATABASE"
    assert result["sources"]["owner_ledger_state_exact"] == 0


def test_no_user_does_not_query_finance_sources(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("read after missing owner")
    monkeypatch.setattr(probe, "_upto2", forbidden)
    class Missing(DB):
        def __init__(self):
            self.users = Users([])
    result = asyncio.run(probe.audit(Missing(), "synthetic@example.test"))
    assert result == {"read_only": True, "result": "OWNER_ACCOUNT_NOT_UNIQUELY_RESOLVED"}

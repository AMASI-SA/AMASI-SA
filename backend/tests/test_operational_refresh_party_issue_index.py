"""Party names and review rows retain their exact financial snapshot meaning."""
from copy import deepcopy

import pytest

from operational_balance_service import _apply_refreshed_party_names
import operational_issue_index as issue_index


CODE = "operational_obligation_entity_incomplete"


def obligation(identity, party_type="courier", party_id="missing"):
    return {"id": identity, "party_type": party_type, "party_id": party_id,
            "currency": "SAR", "confirmed": "34.50", "expected": "0.00",
            "direction": "payable", "name": "previous name"}


def test_names_and_unique_diagnostics_preserve_money_metadata_and_existing_order():
    metadata_issue = {"code": CODE, "source_id": "missing-1", "detail": ["source evidence"]}
    duplicate = {"code": CODE, "source_id": "missing-2"}
    result = {"engine": {"obligations": {
        "known": obligation("known", party_id="approved"),
        "missing-1": obligation("missing-1"),
        "missing-2": obligation("missing-2"),
        "supplier": obligation("supplier", "supplier", ""),
    }, "issues": [metadata_issue, duplicate, deepcopy(duplicate)],
        "facts": {"fee": {"amount": "34.50", "recorded_at": "2026-11-18T19:00:00Z"}}},
        "movements": [{"id": "payment", "amount": "34.50"}]}
    before = deepcopy(result)
    names = {"courier": {"approved": "approved courier"}}
    _apply_refreshed_party_names(result, names)
    assert result["engine"]["issues"] == [metadata_issue, duplicate, duplicate,
                                            {"code": CODE, "source_id": "missing-1"}]
    assert result["engine"]["issues"][0] is metadata_issue
    assert {key: value["name"] for key, value in result["engine"]["obligations"].items()} == {
        "known": "approved courier", "missing-1": "جهة غير مكتملة الإعداد",
        "missing-2": "جهة غير مكتملة الإعداد", "supplier": "تكلفة منتجات لم تُستلم من المورد"}
    for key, value in result["engine"]["obligations"].items():
        assert {k: v for k, v in value.items() if k != "name"} == {
            k: v for k, v in before["engine"]["obligations"][key].items() if k != "name"}
    assert result["movements"] == before["movements"]
    assert result["engine"]["facts"] == before["engine"]["facts"]
    assert issue_index.INDEX_KEY not in result
    assert issue_index.INDEX_KEY not in result["engine"]
    assert names == {"courier": {"approved": "approved courier"}}


@pytest.mark.parametrize("existing", [None, "legacy review", {"source_id": "one", "code": CODE}])
def test_unusual_review_rows_keep_ordinary_list_equality(existing):
    rows = [existing]
    result = {"engine": {"obligations": {"one": obligation("one")}, "issues": rows}}
    _apply_refreshed_party_names(result, {})
    expected = [existing]
    candidate = {"code": CODE, "source_id": "one"}
    if candidate not in expected:
        expected.append(candidate)
    assert rows == expected
    assert result["engine"]["issues"] is rows


def test_unused_diagnostics_are_not_added_to_a_known_only_snapshot():
    result = {"engine": {"obligations": {"known": obligation("known", party_id="approved")}}}
    _apply_refreshed_party_names(result, {"courier": {"approved": "known name"}})
    assert "issues" not in result["engine"]
    assert result["engine"]["obligations"]["known"]["name"] == "known name"


def test_party_review_lookup_work_is_bounded_by_existing_and_added_rows(monkeypatch):
    original_key = issue_index._key
    calls = 0

    def measured_key(row):
        nonlocal calls
        calls += 1
        return original_key(row)

    monkeypatch.setattr(issue_index, "_key", measured_key)
    existing_count, missing_count = 4000, 1000
    rows = [{"code": "source_incomplete", "source_id": str(i)} for i in range(existing_count)]
    result = {"engine": {"obligations": {
        str(i): obligation(str(i)) for i in range(missing_count)}, "issues": rows}}
    _apply_refreshed_party_names(result, {})
    assert len(rows) == existing_count + missing_count
    assert existing_count <= calls <= existing_count + 7 * missing_count
    assert rows[existing_count:] == [{"code": CODE, "source_id": str(i)} for i in range(missing_count)]


def test_party_naming_observes_cancellation_before_touching_an_obligation(monkeypatch):
    import asyncio
    import operational_demand_refresh as demand

    def cancelled():
        raise asyncio.CancelledError

    result = {"engine": {"obligations": {"one": obligation("one")}, "issues": []}}
    before = deepcopy(result)
    monkeypatch.setattr(demand, "calculation_checkpoint", cancelled)
    with pytest.raises(asyncio.CancelledError):
        _apply_refreshed_party_names(result, {})
    assert result == before

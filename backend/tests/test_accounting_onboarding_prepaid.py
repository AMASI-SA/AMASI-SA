import asyncio
import pytest
from accounting_onboarding_prepaid import calculate_prepaid, list_prepaid_obligations


def row(**changes):
    return {"source_mode": "obligation", "obligation_id": "salla-v2", "title": "Salla",
            "coverage_start": "2026-08-21", "coverage_end": "2027-08-21",
            "payment_date": "2026-08-20", "amount_paid": "3650.00", "currency": "SAR",
            "evidence_ref": "payment-file", **changes}


def test_annual_actual_calendar_days_and_exact_remainder():
    result = calculate_prepaid(row(), "2026-10-01")
    assert result == {"coverage_days": 365, "consumed_days": 41, "remaining_days": 324,
                      "consumed_before_cutover": "410.00", "prepaid_remaining_at_cutover": "3240.00"}
    result = calculate_prepaid(row(amount_paid="0.01", coverage_start="2026-09-30", coverage_end="2026-10-02"), "2026-10-01")
    assert result["consumed_before_cutover"] == "0.01"
    assert result["prepaid_remaining_at_cutover"] == "0.00"


@pytest.mark.parametrize("changes", [
    {"amount_paid": "0"}, {"amount_paid": "-10"}, {"amount_paid": "NaN"}, {"amount_paid": "1.001"},
    {"payment_status": "unpaid"}, {"payment_date": "2026-10-01"}, {"payment_date": "2026-10-02"},
    {"coverage_start": "2026-02-30"}, {"coverage_end": "2026-08-21"}, {"coverage_end": "2026-09-30"},
    {"evidence_ref": ""}, {"obligation_id": ""}, {"currency": "USD"}, {"source_mode": "legacy"},
])
def test_invalid_or_unpaid_never_becomes_asset(changes):
    with pytest.raises(ValueError):
        calculate_prepaid(row(**changes), "2026-10-01")


def test_leap_year_and_future_coverage_manual_exception():
    result = calculate_prepaid(row(source_mode="manual_exception", entity="Service", coverage_start="2023-10-01", coverage_end="2024-10-01", payment_date="2023-09-30"), "2024-03-01")
    assert result["coverage_days"] == 366
    assert result["consumed_days"] == 152
    assert calculate_prepaid(row(coverage_start="2026-11-01"), "2026-10-01")["consumed_before_cutover"] == "0.00"


def test_v2_tenant_readonly_discovery_never_treats_period_amount_as_payment():
    queries = []
    class Collection:
        def __init__(self, name): self.name = name
        def find(self, query, projection):
            queries.append((self.name, query))
            return self
        async def to_list(self, limit):
            if self.name.endswith("invoices_v2"):
                return [{"obligation_id": "salla-v2", "period_start": "2026-08-21", "period_end": "2027-08-20", "payment_status": "unpaid"}]
            return [{"id": "salla-v2", "start_date": "2026-08-21", "cycle": "annual", "period_amount": 3650, "entity_name": "Salla", "status": "active"}]
    class DB:
        def __getitem__(self, name): return Collection(name)
    result = asyncio.run(list_prepaid_obligations(DB(), "tenant-a", "2026-10-01"))
    assert all(query == {"user_id": "tenant-a"} for _, query in queries)
    assert result[0]["coverage_end"] == "2027-08-21"
    assert result[0]["payment_status"] == "unpaid"
    assert "amount_paid" not in result[0]
    assert result[0]["currency"] is None

"""Synthetic PayPal Activity Download CSV contracts: review-only, no PII leakage."""
from operational_provider_statement_intake import normalize_statement
import pytest


HEADER = "Date,Time,TimeZone,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID,Invoice Number,From Email Address"
def csv_body(rows):
    return (HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


def test_paypal_activity_is_scoped_review_only_and_retains_transaction_reference():
    raw = csv_body([
        "11/27/2026,12:00:00,GMT,Private User,Express Checkout Payment,Completed,USD,100.00,-3.50,96.50,PAYPAL-SYNTH-1,ORDER-SYNTHETIC,private@example.test",
        "11/28/2026,13:00:00,GMT,Private User,General Withdrawal,Completed,USD,-96.50,0.00,-96.50,PAYPAL-SYNTH-2,,private@example.test",
    ])
    parsed = normalize_statement(raw)
    assert parsed["provider"] == "paypal"
    assert parsed["currency"] == "USD"
    assert parsed["totals"]["capture_principal"] is None
    assert parsed["totals"]["transferred_amount"] is None
    assert len(parsed["rows"]) == 2
    assert all(row["event_type"] == "unsupported" for row in parsed["rows"])
    assert all("statement_event_unsupported" in row["blockers"] for row in parsed["rows"])
    first = parsed["rows"][0]
    assert first["provider_order_id"] == "PAYPAL-SYNTH-1"
    assert first["principal_amount"] == "100.00"
    assert first["fee_net"] == "3.50" and first["transferred_amount"] == "96.50"
    assert first["fee_vat"] is None and first["fee_total"] is None
    assert "Private User" not in str(parsed) and "private@example.test" not in str(parsed)


def test_paypal_mixed_currency_refuses_final_currency_aggregate():
    raw = csv_body([
        "11/27/2026,12:00:00,GMT,Synthetic,Payment,Completed,USD,100.00,-3.00,97.00,PAYPAL-1,,",
        "11/28/2026,12:00:00,GMT,Synthetic,Currency Conversion,Completed,SAR,380.00,0.00,380.00,PAYPAL-2,,",
    ])
    parsed = normalize_statement(raw)
    assert parsed["currency"] is None
    assert all("statement_currency_conflict" in row["blockers"] for row in parsed["rows"])
    assert parsed["totals"]["fee_total"] is None


def test_paypal_duplicate_id_and_net_mismatch_always_blocked():
    raw = csv_body([
        "11/27/2026,12:00:00,GMT,Synthetic,Payment,Completed,USD,100.00,-3.00,98.00,PAYPAL-1,,",
        "11/28/2026,12:00:00,GMT,Synthetic,Payment,Completed,USD,100.00,-3.00,97.00,PAYPAL-1,,",
    ])
    parsed = normalize_statement(raw)
    assert "statement_transfer_mismatch" in parsed["rows"][0]["blockers"]
    assert all("statement_duplicate_row_ambiguous" in row["blockers"] for row in parsed["rows"])


def test_non_paypal_csv_and_bad_sizes_fail_closed():
    with pytest.raises(ValueError, match="statement_file_invalid"):
        normalize_statement(b"hello,there\n1,2\n")
    with pytest.raises(ValueError, match="statement_provider_unknown"):
        normalize_statement(b"Date,Transaction ID,Currency\n2026-11-27,abc,SAR\n")
    with pytest.raises(ValueError, match="statement_file_invalid"):
        normalize_statement(csv_body(["11/27/2026,12:00,GMT,Name,Payment,Completed,USD,1.00,0.00,1.00,ID,,\x00"]))


def test_paypal_fingerprints_ignore_csv_row_order_but_hash_original_bytes():
    a = "11/27/2026,12:00:00,GMT,Synthetic,Payment,Completed,USD,100.00,-3.00,97.00,PAYPAL-1,,"
    b = "11/28/2026,12:00:00,GMT,Synthetic,Refund,Completed,USD,-10.00,0.00,-10.00,PAYPAL-2,,"
    x = normalize_statement(csv_body([a,b]))
    y = normalize_statement(csv_body([b,a]))
    assert x["fingerprint"] == y["fingerprint"]
    assert x["file_hash"] != y["file_hash"]

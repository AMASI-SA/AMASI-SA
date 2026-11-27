"""Pure TikTok advertiser/day coverage; missing accounts are not zero spend."""
from __future__ import annotations
from datetime import date
from typing import Any


def tiktok_reporting_coverage(
    account_ids: list[str], start: str, end: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Report partial TikTok advertiser coverage, never silently turn gaps into zero."""
    unique_ids = set(account_ids)
    num_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    observed_pairs: set[tuple[str, str]] = set()
    invalid_pairs: set[tuple[str, str]] = set()
    duplicate_pairs: set[tuple[str, str]] = set()
    for row in rows:
        account = str(row.get("ad_account_id") or "")
        report_day = str(row.get("date") or "")
        if account not in unique_ids or not start <= report_day <= end:
            continue
        pair = (account, report_day)
        if pair in observed_pairs or pair in invalid_pairs:
            duplicate_pairs.add(pair)
        proof = row.get("source_close_proof") or {}
        has_source = (
            row.get("spend_native") is not None
            and row.get("spend_sar") is not None
            and bool(row.get("currency_native"))
            and bool(row.get("account_timezone"))
            and (
                row.get("spend_native") != 0
                or proof.get("zero_confirmed") is True
            )
        )
        if has_source:
            observed_pairs.add(pair)
        else:
            invalid_pairs.add(pair)
    expected = len(unique_ids) * num_days
    valid_pairs = observed_pairs - invalid_pairs - duplicate_pairs
    covered_accounts = {key[0] for key in valid_pairs}
    complete_accounts = {
        account_id for account_id in unique_ids
        if sum(key[0] == account_id for key in valid_pairs) == num_days
    }
    missing_accounts = sorted(unique_ids - complete_accounts)
    covered = len(valid_pairs)
    complete = (
        len(unique_ids) == len(account_ids)
        and covered == expected
        and not invalid_pairs
        and not duplicate_pairs
    )
    return {
        "accounts_expected": len(unique_ids),
        "accounts_with_facts": len(covered_accounts),
        "expected_account_days": expected,
        "covered_account_days": covered,
        "missing_account_days": max(0, expected - covered),
        "missing_account_ids": missing_accounts[:20],
        "duplicate_account_days": len(duplicate_pairs),
        "coverage_complete": complete,
        "data_state": "complete" if complete else "incomplete",
    }



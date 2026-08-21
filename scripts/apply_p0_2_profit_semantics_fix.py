#!/usr/bin/env python3
"""Apply P0-2 Campaign AI finance-semantics hardening deterministically.

This patch does not change provider execution, attribution matching, spend,
orders, sales, or profit arithmetic. It only makes financial authority and
metric scope explicit so contribution profit can never be interpreted as full
store net profit and provider-reported sales can never become profit truth.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "backend" / "campaign_ai_policy_v2.py"
PROFIT = ROOT / "backend" / "integrations_control_center" / "snapchat_campaign_profitability.py"
TEST = ROOT / "backend" / "tests" / "test_campaign_ai_profit_semantics_p0_2.py"

POLICY_OLD = '''    base = {\n        "source": SNAPCHAT_AI_SALLA_SOURCE,\n        "page_salla_orders": page_orders,\n        "page_salla_sales_sar": round(page_sales, 2),\n        "engine_orders": engine_orders,\n        "engine_sales_sar": round(engine_sales, 2),\n        "verified_against_page_salla": aligned,\n        "product_count": int(source.get("product_count") or 0),\n        "products": _compact_products(source.get("products")),\n        "profit_scope": source.get("profit_scope"),\n        "allocation_method": source.get("allocation_method"),\n    }\n'''

POLICY_NEW = '''    contribution = source.get("contribution_profit_sar")\n    contribution_available = aligned and contribution is not None\n    base = {\n        # P0-2 finance contract: ad platforms are performance evidence only.\n        # Mezan attribution + cost engines own commercial outcomes. This value\n        # is contribution profit, never full store net profit.\n        "source": "mezan_exact_campaign_attribution",\n        "legacy_source": SNAPCHAT_AI_SALLA_SOURCE,\n        "finance_authority": "mezan",\n        "commercial_outcomes_authority": "mezan_attribution",\n        "provider_finance_authority": False,\n        "provider_sales_used_as_profit": False,\n        "profit_metric": "contribution_profit",\n        "contribution_profit_available": contribution_available,\n        "net_profit_available": False,\n        "net_profit_sar": None,\n        "net_profit_unavailable_reason": (\n            "campaign_level_full_cost_allocation_not_implemented"\n        ),\n        "mezan_attributed_orders": page_orders,\n        "mezan_attributed_sales_sar": round(page_sales, 2),\n        # Compatibility fields retained until the UI/API migration is complete.\n        "page_salla_orders": page_orders,\n        "page_salla_sales_sar": round(page_sales, 2),\n        "engine_orders": engine_orders,\n        "engine_sales_sar": round(engine_sales, 2),\n        "verified_against_page_salla": aligned,\n        "verified_against_mezan_attribution": aligned,\n        "product_count": int(source.get("product_count") or 0),\n        "products": _compact_products(source.get("products")),\n        "profit_scope": source.get("profit_scope"),\n        "allocation_method": source.get("allocation_method"),\n    }\n'''

ROW_OLD = '''                "salla_campaign_results": {\n                    "source": "unified_orders:salla_exact_account_campaign_match",\n                    **salla_results,\n                },\n                "campaign_profitability": profit,\n'''

ROW_NEW = '''                "mezan_campaign_results": {\n                    "source": "mezan_attribution:unified_orders:exact_account_campaign_match",\n                    **salla_results,\n                },\n                # Legacy compatibility only; do not treat this key name as the\n                # finance authority. Mezan is authoritative for commercial truth.\n                "salla_campaign_results": {\n                    "source": "legacy_alias:mezan_attribution",\n                    **salla_results,\n                },\n                "finance_semantics": {\n                    "finance_authority": "mezan",\n                    "provider_role": "ad_delivery_performance_only",\n                    "provider_sales_used_as_profit": False,\n                    "campaign_profit_metric": "contribution_profit",\n                    "campaign_net_profit_available": False,\n                },\n                "campaign_profitability": profit,\n'''

PROFIT_OLD = '''        "allocation_method": CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD,\n        "profit_scope": "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations",\n    }\n'''

PROFIT_NEW = '''        "allocation_method": CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD,\n        "profit_scope": "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations",\n        "finance_authority": "mezan",\n        "profit_metric": "contribution_profit",\n        "contribution_profit_available": contribution_profit is not None,\n        "net_profit_available": False,\n        "net_profit_sar": None,\n        "net_profit_unavailable_reason": "campaign_level_full_cost_allocation_not_implemented",\n        "provider_sales_used_as_profit": False,\n    }\n'''

TEST_CONTENT = '''import campaign_ai_policy_v2 as policy\nfrom integrations_control_center.snapchat_campaign_profitability import _finalize_campaign\n\n\ndef test_p0_2_campaign_profit_is_explicitly_mezan_contribution_not_net_profit():\n    raw = {\n        "orders": 2,\n        "sales_sar": 500.0,\n        "product_cost_sar": 200.0,\n        "missing_cost_orders": 0,\n        "products": {},\n    }\n    result = _finalize_campaign(raw, spend_sar=100.0)\n    assert result["contribution_profit_sar"] == 200.0\n    assert result["finance_authority"] == "mezan"\n    assert result["profit_metric"] == "contribution_profit"\n    assert result["contribution_profit_available"] is True\n    assert result["net_profit_available"] is False\n    assert result["net_profit_sar"] is None\n    assert result["provider_sales_used_as_profit"] is False\n\n\ndef test_p0_2_missing_cost_never_becomes_profit_or_net_profit():\n    raw = {\n        "orders": 1,\n        "sales_sar": 250.0,\n        "product_cost_sar": 0.0,\n        "missing_cost_orders": 1,\n        "products": {},\n    }\n    result = _finalize_campaign(raw, spend_sar=50.0)\n    assert result["contribution_profit_sar"] is None\n    assert result["contribution_profit_available"] is False\n    assert result["net_profit_sar"] is None\n\n\ndef test_p0_2_ai_context_marks_mezan_as_finance_authority():\n    raw = {\n        "orders": 2,\n        "sales_sar": 500.0,\n        "product_cost_sar": 200.0,\n        "ad_spend_sar": 100.0,\n        "contribution_profit_sar": 200.0,\n        "profit_scope": "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations",\n        "allocation_method": "test",\n        "products": [],\n    }\n    result = policy._page_aligned_profitability(\n        raw, {"orders": 2, "sales_sar": 500.0}\n    )\n    assert result["finance_authority"] == "mezan"\n    assert result["commercial_outcomes_authority"] == "mezan_attribution"\n    assert result["provider_finance_authority"] is False\n    assert result["provider_sales_used_as_profit"] is False\n    assert result["mezan_attributed_orders"] == 2\n    assert result["mezan_attributed_sales_sar"] == 500.0\n    assert result["contribution_profit_available"] is True\n    assert result["net_profit_available"] is False\n    assert result["net_profit_sar"] is None\n\n\ndef test_p0_2_attribution_mismatch_fails_closed_for_contribution_profit():\n    raw = {\n        "orders": 2,\n        "sales_sar": 500.0,\n        "contribution_profit_sar": 200.0,\n        "products": [],\n    }\n    result = policy._page_aligned_profitability(\n        raw, {"orders": 1, "sales_sar": 250.0}\n    )\n    assert result["verified_against_mezan_attribution"] is False\n    assert result["contribution_profit_available"] is False\n    assert result["contribution_profit_sar"] is None\n    assert result["net_profit_sar"] is None\n'''


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"{label}: already patched")
        return
    if old not in text:
        raise SystemExit(f"{label}: expected anchor not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: patched")


def main() -> None:
    replace_once(POLICY, POLICY_OLD, POLICY_NEW, "campaign_ai_policy_v2 base semantics")
    replace_once(POLICY, ROW_OLD, ROW_NEW, "campaign_ai_policy_v2 row semantics")
    replace_once(PROFIT, PROFIT_OLD, PROFIT_NEW, "snapchat campaign profitability semantics")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")
    print("wrote backend/tests/test_campaign_ai_profit_semantics_p0_2.py")


if __name__ == "__main__":
    main()

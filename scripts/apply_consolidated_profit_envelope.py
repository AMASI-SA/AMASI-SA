#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

ENGINE = r'''"""Authoritative read-only profit envelope for Mezan decision systems.

This module is the single Campaign-AI-facing contract for store P&L. It keeps
financial totals, component provenance, and accounting completeness together so
consumers do not independently reinterpret missing inputs as zero or rebuild the
meaning of net profit in multiple loaders.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from auth import DEFAULT_PAYMENT_METHODS, DEFAULT_SHIPPING_COMPANIES, ensure_user_settings
from dashboard_v2_routes import _filtered_orders, build_mezan_v2_ads, build_mezan_v2_product_cost
from excel_parser import match_settings
from expenses_routes import compute_operating_expenses_for_range
from orders_db import orders_to_parsed
from recurring_obligations_routes import compute_recurring_obligations_for_range
from shipping_cost_ssot import aggregate_breakdown, get_company_configs

CONTRACT_VERSION = "mezan_profit_envelope_v1"
SOURCE = "mezan_profit_engine_v2_read_only"


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed != parsed or abs(parsed) == float("inf"):
        return 0.0
    return parsed


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _accounting_quality(
    *,
    matched: dict[str, Any],
    shipping: dict[str, Any],
    product_cost: dict[str, Any],
    ads: dict[str, Any],
    operating: dict[str, Any],
    recurring: dict[str, Any],
) -> dict[str, Any]:
    missing = _count(product_cost.get("missing_products_count"))
    incomplete = _count(product_cost.get("incomplete_orders_count"))
    component_known = {
        "orders_sales": True,
        "product_cost": (
            "total" in product_cost
            and missing is not None
            and incomplete is not None
        ),
        "advertising": "total" in ads,
        "payment_fees": "total_payment_fees" in matched,
        "shipping": "total_with_tax" in shipping,
        "payroll": "salaries_total" in operating,
        "recurring_obligations": "total" in recurring,
    }
    known = all(component_known.values())
    complete = bool(known and missing == 0 and incomplete == 0)
    issues: list[str] = []
    for name, is_known in component_known.items():
        if not is_known:
            issues.append(f"unknown_component:{name}")
    if missing is not None and missing > 0:
        issues.append("missing_product_cost")
    if incomplete is not None and incomplete > 0:
        issues.append("incomplete_profit_orders")
    return {
        "known": known,
        "complete": complete,
        "scale_safe": complete,
        "missing_product_cost_count": missing,
        "incomplete_profit_orders_count": incomplete,
        "component_known": component_known,
        "issues": issues,
        "unknown_is_zero": False,
    }


async def build_mezan_profit_envelope(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    payment_methods: str | None = None,
    shipping_companies: str | None = None,
) -> dict[str, Any]:
    """Return P&L totals and their accounting-quality contract in one object."""
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    if end < start:
        start, end = end, start
    start_s = start.isoformat()
    end_s = end.isoformat()

    orders = await _filtered_orders(
        db,
        user_id,
        from_date=start_s,
        to_date=end_s,
        payment_methods=payment_methods,
        shipping_companies=shipping_companies,
        include_marketing_attribution=False,
    )
    settings = await ensure_user_settings(db, user_id)
    parsed = orders_to_parsed(orders)
    matched = match_settings(
        parsed,
        settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
        settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
    )
    company_configs = await get_company_configs(db, user_id)
    shipping = aggregate_breakdown(orders, company_configs)

    product_cost, ads, operating, recurring = await asyncio.gather(
        build_mezan_v2_product_cost(db, user_id, orders),
        build_mezan_v2_ads(db, user_id, from_date=start_s, to_date=end_s),
        compute_operating_expenses_for_range(db, user_id, start, end),
        compute_recurring_obligations_for_range(db, user_id, start, end),
    )

    payment_fees = _number(matched.get("total_payment_fees"))
    shipping_total = _number(shipping.get("total_with_tax"))
    ad_spend = _number(ads.get("total"))
    ad_bank_fee = _number((ads.get("bank_commissions") or {}).get("total_fee_sar"))
    payment_fees_with_ads = payment_fees + ad_bank_fee
    product_total = _number(product_cost.get("total"))
    salary_total = _number(operating.get("salaries_total"))
    recurring_total = _number(recurring.get("total"))
    operating_total = salary_total + recurring_total
    total_sales = round(sum(_number(order.get("total_amount")) for order in orders), 2)
    total_orders = len(orders)
    net_profit = round(
        total_sales
        - payment_fees_with_ads
        - shipping_total
        - product_total
        - ad_spend
        - operating_total,
        2,
    )
    quality = _accounting_quality(
        matched=matched,
        shipping=shipping,
        product_cost=product_cost,
        ads=ads,
        operating=operating,
        recurring=recurring,
    )
    source_contract = {
        "orders_sales": "unified_orders:mezan_v2",
        "product_cost": product_cost.get("source_contract") or {},
        "advertising": ads.get("source_contract") or {},
        "payment_fees": "settings.payment_methods + mezan_ad_account_cost_settings_v2",
        "shipping": "shipping_cost_ssot",
        "payroll": "mezan_employee_salary_contracts_v2",
        "recurring_obligations": "operating_recurring_obligations_v2",
    }
    totals = {
        "total_sales": total_sales,
        "total_orders": total_orders,
        "net_profit": net_profit,
        "total_ads_cost": round(ad_spend, 2),
        "total_product_cost": round(product_total, 2),
        "total_payment_fees": round(payment_fees_with_ads, 2),
        "total_shipping_cost": round(shipping_total, 2),
        "operating_expenses_total": round(operating_total, 2),
        "overall_roas": round(total_sales / ad_spend, 2) if ad_spend > 0 else None,
        "avg_cost_per_order": round(ad_spend / total_orders, 2) if ad_spend > 0 and total_orders > 0 else None,
        "missing_product_cost_count": quality["missing_product_cost_count"],
        "incomplete_profit_orders_count": quality["incomplete_profit_orders_count"],
        "profit_accounting_complete": quality["complete"],
        "profit_accounting_quality_known": quality["known"],
        "profit_source": SOURCE,
        "profit_contract_version": CONTRACT_VERSION,
        "profit_source_contract": source_contract,
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "source": SOURCE,
        "period": {"from": start_s, "to": end_s},
        "totals": totals,
        "components": {
            "sales": {"amount_sar": total_sales, "orders": total_orders},
            "product_cost": {"amount_sar": round(product_total, 2)},
            "advertising": {"amount_sar": round(ad_spend, 2)},
            "payment_fees": {"amount_sar": round(payment_fees_with_ads, 2)},
            "shipping": {"amount_sar": round(shipping_total, 2)},
            "payroll": {"amount_sar": round(salary_total, 2)},
            "recurring_obligations": {"amount_sar": round(recurring_total, 2)},
        },
        "quality": quality,
        "source_contract": source_contract,
        "read_only": True,
    }


async def build_mezan_profit_totals(
    db: Any,
    user_id: str,
    *,
    from_date: str,
    to_date: str,
    payment_methods: str | None = None,
    shipping_companies: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible totals view backed by the consolidated envelope."""
    envelope = await build_mezan_profit_envelope(
        db,
        user_id,
        from_date=from_date,
        to_date=to_date,
        payment_methods=payment_methods,
        shipping_companies=shipping_companies,
    )
    return dict(envelope["totals"])


__all__ = [
    "CONTRACT_VERSION",
    "SOURCE",
    "build_mezan_profit_envelope",
    "build_mezan_profit_totals",
]
'''

LOADER = r'''"""Read-only Campaign AI adapter for Mezan's consolidated profit envelope."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from mezan_profit_engine import build_mezan_profit_envelope, build_mezan_profit_totals


def make_mezan_campaign_profit_loader(db: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Expose the legacy loader shape without rebuilding P&L semantics here."""

    async def loader(
        *,
        user: dict[str, Any],
        from_date: str,
        to_date: str,
        payment_methods: str | None = None,
        shipping_companies: str | None = None,
        include_legacy_analyses: bool = False,
        allow_self_heal: bool = False,
    ) -> dict[str, Any]:
        del include_legacy_analyses, allow_self_heal
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise ValueError("mezan_profit_loader_user_required")
        envelope = await build_mezan_profit_envelope(
            db,
            user_id,
            from_date=from_date,
            to_date=to_date,
            payment_methods=payment_methods,
            shipping_companies=shipping_companies,
        )
        return {
            "totals": dict(envelope["totals"]),
            "profit_envelope": envelope,
            "dashboard_source": envelope["source"],
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    return loader


__all__ = ["build_mezan_profit_totals", "make_mezan_campaign_profit_loader"]
'''

GATE = r'''"""Fail-closed store-level profit accounting gate for Campaign AI scaling."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException

from mezan_profit_engine import build_mezan_profit_envelope

RIYADH = timezone(timedelta(hours=3))


def _count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def accounting_quality_from_totals(totals: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility parser for old snapshots; unknown stays unknown, never zero."""
    source = totals if isinstance(totals, dict) else {}
    missing = _count(source.get("missing_product_cost_count"))
    incomplete = _count(source.get("incomplete_profit_orders_count"))
    known = missing is not None and incomplete is not None
    complete = bool(known and missing == 0 and incomplete == 0)
    return {
        "known": known,
        "complete": complete,
        "scale_safe": complete,
        "missing_product_cost_count": missing,
        "incomplete_profit_orders_count": incomplete,
        "source": source.get("profit_source") or "mezan_profit_engine_v2_read_only",
        "unknown_is_zero": False,
    }


def accounting_quality_from_envelope(envelope: dict[str, Any] | None) -> dict[str, Any]:
    source = envelope if isinstance(envelope, dict) else {}
    quality = source.get("quality") if isinstance(source.get("quality"), dict) else {}
    known = quality.get("known") is True
    complete = bool(known and quality.get("complete") is True and quality.get("scale_safe") is True)
    return {
        **quality,
        "known": known,
        "complete": complete,
        "scale_safe": complete,
        "source": source.get("source") or "mezan_profit_engine_v2_read_only",
        "contract_version": source.get("contract_version"),
        "unknown_is_zero": False,
    }


async def require_profit_accounting_complete_for_scale(
    db: Any,
    user_id: str,
    action: str,
) -> dict[str, Any]:
    """Allow defensive actions, but block spend expansion unless envelope proves completeness."""
    if str(action or "").strip().lower() != "scale":
        return {"complete": True, "scale_gate_applied": False}
    today = datetime.now(RIYADH).date()
    envelope = await build_mezan_profit_envelope(
        db,
        user_id,
        from_date=today.replace(day=1).isoformat(),
        to_date=today.isoformat(),
    )
    quality = accounting_quality_from_envelope(envelope)
    if not quality["complete"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "campaign_ai_profit_accounting_incomplete",
                "message": (
                    "صافي الربح الحالي غير مثبت كمحاسبة مكتملة؛ أُوقفت زيادة الإنفاق "
                    "حتى يثبت عقد ربح ميزان اكتمال كل مكونات الربح المطلوبة."
                ),
                **quality,
                "recovery_action": "complete_missing_profit_inputs_then_refresh_recommendation",
            },
        )
    return {**quality, "scale_gate_applied": True}


__all__ = [
    "accounting_quality_from_envelope",
    "accounting_quality_from_totals",
    "require_profit_accounting_complete_for_scale",
]
'''

TEST = r'''import pytest
from fastapi import HTTPException

import campaign_ai_profit_accounting_gate as gate
import mezan_campaign_profit_loader as loader_module
import mezan_profit_engine as engine


@pytest.mark.asyncio
async def test_consolidated_envelope_arithmetic_and_complete_quality(monkeypatch):
    async def filtered(*args, **kwargs):
        return [{"total_amount": 200.0}, {"total_amount": 100.0}]
    async def settings(*args, **kwargs):
        return {"payment_methods": [], "shipping_companies": []}
    async def company_configs(*args, **kwargs):
        return []
    async def product_cost(*args, **kwargs):
        return {"total": 80.0, "missing_products_count": 0, "incomplete_orders_count": 0, "source_contract": {"v": 1}}
    async def ads(*args, **kwargs):
        return {"total": 30.0, "bank_commissions": {"total_fee_sar": 2.0}, "source_contract": {"v": 2}}
    async def operating(*args, **kwargs):
        return {"salaries_total": 20.0}
    async def recurring(*args, **kwargs):
        return {"total": 10.0}

    monkeypatch.setattr(engine, "_filtered_orders", filtered)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda orders: orders)
    monkeypatch.setattr(engine, "match_settings", lambda *a, **k: {"total_payment_fees": 8.0})
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(engine, "aggregate_breakdown", lambda *a, **k: {"total_with_tax": 15.0})
    monkeypatch.setattr(engine, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring)

    envelope = await engine.build_mezan_profit_envelope(object(), "u1", from_date="2026-08-01", to_date="2026-08-02")
    assert envelope["contract_version"] == "mezan_profit_envelope_v1"
    assert envelope["quality"]["known"] is True
    assert envelope["quality"]["complete"] is True
    assert envelope["quality"]["scale_safe"] is True
    assert envelope["quality"]["unknown_is_zero"] is False
    assert envelope["totals"]["total_sales"] == 300.0
    assert envelope["totals"]["net_profit"] == 135.0


@pytest.mark.asyncio
async def test_missing_component_is_unknown_and_not_zero(monkeypatch):
    async def filtered(*args, **kwargs): return []
    async def settings(*args, **kwargs): return {"payment_methods": [], "shipping_companies": []}
    async def company_configs(*args, **kwargs): return []
    async def product_cost(*args, **kwargs): return {"total": 0, "missing_products_count": 0, "incomplete_orders_count": 0}
    async def ads(*args, **kwargs): return {}  # total absent => unknown, not proven zero
    async def operating(*args, **kwargs): return {"salaries_total": 0}
    async def recurring(*args, **kwargs): return {"total": 0}
    monkeypatch.setattr(engine, "_filtered_orders", filtered)
    monkeypatch.setattr(engine, "ensure_user_settings", settings)
    monkeypatch.setattr(engine, "orders_to_parsed", lambda orders: orders)
    monkeypatch.setattr(engine, "match_settings", lambda *a, **k: {"total_payment_fees": 0})
    monkeypatch.setattr(engine, "get_company_configs", company_configs)
    monkeypatch.setattr(engine, "aggregate_breakdown", lambda *a, **k: {"total_with_tax": 0})
    monkeypatch.setattr(engine, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(engine, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(engine, "compute_operating_expenses_for_range", operating)
    monkeypatch.setattr(engine, "compute_recurring_obligations_for_range", recurring)
    envelope = await engine.build_mezan_profit_envelope(object(), "u1", from_date="2026-08-01", to_date="2026-08-01")
    assert envelope["quality"]["known"] is False
    assert envelope["quality"]["complete"] is False
    assert "unknown_component:advertising" in envelope["quality"]["issues"]


def test_gate_quality_envelope_is_fail_closed_when_unknown():
    quality = gate.accounting_quality_from_envelope({"quality": {"known": False, "complete": True, "scale_safe": True}})
    assert quality["complete"] is False
    assert quality["scale_safe"] is False
    assert quality["unknown_is_zero"] is False


@pytest.mark.asyncio
async def test_scale_gate_uses_envelope_and_blocks_unknown(monkeypatch):
    async def envelope(*args, **kwargs):
        return {"source": "mezan_profit_engine_v2_read_only", "contract_version": "mezan_profit_envelope_v1", "quality": {"known": False, "complete": False, "scale_safe": False, "issues": ["unknown_component:advertising"]}}
    monkeypatch.setattr(gate, "build_mezan_profit_envelope", envelope)
    with pytest.raises(HTTPException) as exc:
        await gate.require_profit_accounting_complete_for_scale(object(), "u1", "scale")
    assert exc.value.status_code == 409
    assert exc.value.detail["unknown_is_zero"] is False


@pytest.mark.asyncio
async def test_defensive_action_does_not_require_profit_envelope(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("profit envelope should not be loaded for defensive action")
    monkeypatch.setattr(gate, "build_mezan_profit_envelope", should_not_run)
    result = await gate.require_profit_accounting_complete_for_scale(object(), "u1", "reduce")
    assert result == {"complete": True, "scale_gate_applied": False}


@pytest.mark.asyncio
async def test_campaign_loader_is_thin_adapter_over_envelope(monkeypatch):
    expected = {
        "contract_version": "mezan_profit_envelope_v1",
        "source": "mezan_profit_engine_v2_read_only",
        "totals": {"net_profit": 123.0},
        "quality": {"known": True, "complete": True, "scale_safe": True},
    }
    async def envelope(*args, **kwargs): return expected
    monkeypatch.setattr(loader_module, "build_mezan_profit_envelope", envelope)
    loader = loader_module.make_mezan_campaign_profit_loader(object())
    payload = await loader(user={"id": "u1"}, from_date="2026-08-01", to_date="2026-08-02")
    assert payload["totals"] == {"net_profit": 123.0}
    assert payload["profit_envelope"] is expected
    assert payload["source_only"] is True
'''


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{label}: expected text not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: patched")


(BACKEND / "mezan_profit_engine.py").write_text(ENGINE, encoding="utf-8")
print("wrote backend/mezan_profit_engine.py")
(BACKEND / "mezan_campaign_profit_loader.py").write_text(LOADER, encoding="utf-8")
print("rewrote backend/mezan_campaign_profit_loader.py")
(BACKEND / "campaign_ai_profit_accounting_gate.py").write_text(GATE, encoding="utf-8")
print("rewrote backend/campaign_ai_profit_accounting_gate.py")

monitor = BACKEND / "campaign_ai_monitor_legacy.py"
replace_once(
    monitor,
    '        totals = (payload or {}).get("totals") or {}\n        from campaign_ai_time_window_quality import window_quality\n',
    '        totals = (payload or {}).get("totals") or {}\n        profit_envelope = (payload or {}).get("profit_envelope") or {}\n        from campaign_ai_time_window_quality import window_quality\n',
    "monitor envelope capture",
)
replace_once(
    monitor,
    '            "time_window_quality": quality,\n            "contains_open_current_day": quality["contains_open_current_day"],\n',
    '            "time_window_quality": quality,\n            "profit_contract_version": profit_envelope.get("contract_version"),\n            "profit_accounting": profit_envelope.get("quality"),\n            "contains_open_current_day": quality["contains_open_current_day"],\n',
    "monitor envelope exposure",
)

monthly = BACKEND / "campaign_ai_monthly_profit_goal_v1.py"
replace_once(
    monthly,
    '    totals = (payload or {}).get("totals") or {}\n    return {\n',
    '    totals = (payload or {}).get("totals") or {}\n    profit_envelope = (payload or {}).get("profit_envelope") or {}\n    return {\n',
    "monthly envelope capture",
)
replace_once(
    monthly,
    '        "incomplete_profit_orders_count": totals.get("incomplete_profit_orders_count"),\n    }\n',
    '        "incomplete_profit_orders_count": totals.get("incomplete_profit_orders_count"),\n        "profit_accounting": profit_envelope.get("quality"),\n        "profit_contract_version": profit_envelope.get("contract_version"),\n    }\n',
    "monthly envelope exposure",
)
old_quality = '''    missing_costs = month_to_date.get("missing_product_cost_count")\n    incomplete_orders = month_to_date.get("incomplete_profit_orders_count")\n    try:\n        missing_costs = int(missing_costs)\n    except (TypeError, ValueError, OverflowError):\n        missing_costs = None\n    try:\n        incomplete_orders = int(incomplete_orders)\n    except (TypeError, ValueError, OverflowError):\n        incomplete_orders = None\n    accounting_quality_known = (\n        missing_costs is not None and incomplete_orders is not None\n    )\n    accounting_complete = bool(\n        accounting_quality_known\n        and missing_costs == 0\n        and incomplete_orders == 0\n    )\n    accounting_incomplete = bool(\n        accounting_quality_known\n        and (missing_costs > 0 or incomplete_orders > 0)\n    )\n'''
new_quality = '''    envelope_quality = month_to_date.get("profit_accounting")\n    if isinstance(envelope_quality, dict):\n        accounting_quality_known = envelope_quality.get("known") is True\n        accounting_complete = bool(\n            accounting_quality_known\n            and envelope_quality.get("complete") is True\n            and envelope_quality.get("scale_safe") is True\n        )\n        missing_costs = envelope_quality.get("missing_product_cost_count")\n        incomplete_orders = envelope_quality.get("incomplete_profit_orders_count")\n    else:\n        missing_costs = month_to_date.get("missing_product_cost_count")\n        incomplete_orders = month_to_date.get("incomplete_profit_orders_count")\n        try:\n            missing_costs = int(missing_costs)\n        except (TypeError, ValueError, OverflowError):\n            missing_costs = None\n        try:\n            incomplete_orders = int(incomplete_orders)\n        except (TypeError, ValueError, OverflowError):\n            incomplete_orders = None\n        accounting_quality_known = (\n            missing_costs is not None and incomplete_orders is not None\n        )\n        accounting_complete = bool(\n            accounting_quality_known\n            and missing_costs == 0\n            and incomplete_orders == 0\n        )\n    accounting_incomplete = bool(accounting_quality_known and not accounting_complete)\n'''
replace_once(monthly, old_quality, new_quality, "monthly envelope authority")

(BACKEND / "tests" / "test_campaign_ai_consolidated_profit_envelope.py").write_text(TEST, encoding="utf-8")
print("wrote backend/tests/test_campaign_ai_consolidated_profit_envelope.py")

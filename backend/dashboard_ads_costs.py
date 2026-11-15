"""Request-local evidence for advertising-row cost summaries.

Product values are observed during the existing dashboard pricing pass. Shipping
and payment fees use the same pure calculators and loaded merchant settings as
the executive snapshot. This module performs no reads, writes, or allocations.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

from auth import DEFAULT_PAYMENT_METHODS
from dashboard_cpu_budget import COST_PROFIT_BUDGET_MS, CPUWorkBudget
from excel_parser import match_settings
from order_currency import currency_code, order_total_sar
from orders_db import orders_to_parsed_cooperative
from shipping_cost_ssot import shipping_breakdown


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return amount if isfinite(amount) and amount >= 0 else None


class DashboardAdsCostCollector:
    """Only a caller-owned instance can retain per-order pricing evidence."""

    def __init__(self) -> None:
        self.payment_settings: list[dict] | None = None
        self.company_configs: dict[str, dict] | None = None
        self.product_orders: dict[int, dict] = {}

    def capture_settings(self, settings: dict) -> None:
        self.payment_settings = deepcopy(settings.get("payment_methods", DEFAULT_PAYMENT_METHODS))
        # Same names/aliases as shipping_cost_ssot.get_company_configs, using
        # the settings already loaded for this exact order filter.
        self.company_configs = {}
        for config in settings.get("shipping_companies") or []:
            if not isinstance(config, dict):
                continue
            name = str(config.get("name") or "").strip()
            if not name:
                continue
            cleaned = name.strip("'\"")
            copied = deepcopy(config)
            for key in (cleaned, cleaned.lower(), name, name.lower()):
                self.company_configs[key] = copied

    def begin_order(self, order: dict) -> None:
        items = order.get("products") or []
        valid_items = bool(items) and all(isinstance(item, dict) for item in items)
        self.product_orders[id(order)] = {
            "known_product_cost_sar": 0.0,
            "product_cost_complete": valid_items,
            "known_pieces": 0.0,
            "pieces_complete": valid_items,
        }

    def observe_line(self, order: dict, item: dict, result: dict) -> None:
        evidence = self.product_orders[id(order)]
        evidence["product_cost_complete"] &= result.get("base_complete") is True
        quantity = _number(result.get("quantity"))
        # The catalog calculator's legacy default of one is not evidence that
        # an absent quantity was one. Keep that uncertainty out of the tooltip.
        source_quantity = _number(item.get("quantity"))
        if quantity is None or quantity <= 0 or source_quantity is None or source_quantity <= 0:
            evidence["pieces_complete"] = False
        else:
            evidence["known_pieces"] += quantity

    def finish_order(self, order: dict, adjusted_total: float) -> None:
        evidence = self.product_orders[id(order)]
        amount = _number(adjusted_total)
        if amount is None:
            evidence["product_cost_complete"] = False
        else:
            # This is the already-computed effective_product_cost result,
            # including the existing return/cancellation policy exactly once.
            evidence["known_product_cost_sar"] = amount

    def _shipping(self, order: dict) -> float | None:
        if self.company_configs is None:
            return None
        breakdown = shipping_breakdown(order, self.company_configs)
        amount = _number(breakdown.get("total"))
        if amount is None:
            return None
        if breakdown.get("source") == "salla":
            # The executive SSOT has no foreign-shipping FX rule. Do not
            # invent one here or relabel a native foreign amount as SAR.
            native_currency = (currency_code(order.get("original_currency"))
                               or currency_code(order.get("currency"), default="SAR"))
            return amount if native_currency == "SAR" else None
        if breakdown.get("source") == "company_config":
            return amount
        company = str(order.get("shipping_company") or "").strip()
        config = (self.company_configs.get(company) or self.company_configs.get(company.lower())
                  or self.company_configs.get(company.strip("'\""))
                  or self.company_configs.get(company.strip("'\"").lower()) or {})
        configured = config.get("cost_per_order")
        if configured is None:
            configured = config.get("cost")
        return 0.0 if amount == 0 and _number(configured) == 0 else None

    async def summary(
        self,
        orders: list[dict],
        *,
        scope_complete: bool,
        reason_codes: list[str] | None = None,
    ) -> dict:
        reasons = set(reason_codes or [])
        known = {"pieces": 0.0, "product_cost_sar": 0.0, "shipping_cost_sar": 0.0,
                 "payment_fees_sar": 0.0}
        complete = {"pieces": True, "product_cost_sar": True, "shipping_cost_sar": True,
                    "payment_fees_sar": True}
        verified_payment_orders: list[dict] = []
        budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
        for order in orders:
            await budget.checkpoint()
            evidence = self.product_orders.get(id(order))
            if evidence is None:
                complete["pieces"] = complete["product_cost_sar"] = False
            else:
                for name, flag in (("pieces", "pieces_complete"),
                                   ("product_cost_sar", "product_cost_complete")):
                    known[name] += evidence["known_" + name]
                    complete[name] &= evidence[flag]
            shipping = self._shipping(order)
            if shipping is None:
                complete["shipping_cost_sar"] = False
            else:
                known["shipping_cost_sar"] += shipping
            if order_total_sar(order) is None:
                complete["payment_fees_sar"] = False
                reasons.add("payment_sales_currency_unverified")
            else:
                verified_payment_orders.append(order)

        payment_bases: set[str] = set()
        if verified_payment_orders:
            if self.payment_settings is None:
                complete["payment_fees_sar"] = False
            else:
                parsed = await orders_to_parsed_cooperative(verified_payment_orders)
                try:
                    payment = match_settings(parsed, self.payment_settings, [])
                except (TypeError, ValueError, ArithmeticError):
                    complete["payment_fees_sar"] = False
                    reasons.add("payment_settings_invalid")
                else:
                    for row in payment.get("payment_breakdown") or []:
                        amount = _number(row.get("fee_amount"))
                        if row.get("matched") is not True or amount is None:
                            complete["payment_fees_sar"] = False
                        else:
                            known["payment_fees_sar"] += amount
                        payment_bases.add(str(row.get("fee_calculation_basis") or "unknown"))

        output: dict[str, Any] = {
            "basis": "executive_order_costs_v1", "currency": "SAR",
            "payment_fees_basis": "merchant_settings_cohort",
            "payment_fee_calculation_bases": sorted(payment_bases),
        }
        flags = {"pieces": "pieces_complete", "product_cost_sar": "product_cost_complete",
                 "shipping_cost_sar": "shipping_cost_complete", "payment_fees_sar": "payment_fees_complete"}
        missing = {"pieces": "pieces_unverified", "product_cost_sar": "product_cost_incomplete",
                   "shipping_cost_sar": "shipping_cost_unverified", "payment_fees_sar": "payment_fees_unverified"}
        for name, value in known.items():
            value = round(value, 2)
            output["known_" + name] = value
            output[flags[name]] = scope_complete and complete[name]
            output[name] = value if output[flags[name]] else None
            if not complete[name]:
                reasons.add(missing[name])
        financial_complete = all(output[flags[name]] for name in (
            "product_cost_sar", "shipping_cost_sar", "payment_fees_sar"))
        output["total_cost_sar"] = round(sum(output[name] for name in (
            "product_cost_sar", "shipping_cost_sar", "payment_fees_sar")), 2) if financial_complete else None
        output["complete"] = financial_complete and output["pieces_complete"]
        output["reason_codes"] = sorted(reasons)
        return output

"""Bounded product-stage evidence uses the real owned refresh lifecycle.

Calculation/query parity uses intercepted collection I/O. Fault injection at
source/reduction boundaries retains the real deadline, cancellation and lease
cleanup paths.
"""
import asyncio
from copy import deepcopy
import json
import math

from fastapi import HTTPException
from pymongo.errors import ExecutionTimeout
import pytest

import dashboard_v2_routes as dashboard
from operational_demand_refresh import DemandRefreshContext, demand_context
from operational_read_runtime import SourceReadError, load_collections, source_read_budget
from order_cost_authority import LIFECYCLES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from supplier_invoice_reporting_costs import PIECES
from test_dashboard_bundle_cost_authority import OWNER, db as bundle_db, sale, sale_line
from test_operational_balance_demand_refresh import Request
from test_operational_balance_profit_product_authority_parallel import (
    ProductBoundary, documents, mixed_orders,
)


STAGES = (
    "profit_product_catalog", "profit_product_inputs", "profit_product_authority",
    "profit_product_supplier", "profit_product_reduce",
)


def product_trace(boundary):
    assert isinstance(boundary.context, DemandRefreshContext)
    rows = [row for row in boundary.context.stage_timings if row["stage"] in STAGES]
    for row in rows:
        assert set(row) == {"stage", "duration_ms", "outcome"}
        assert type(row["duration_ms"]) in (int, float)
        assert math.isfinite(row["duration_ms"]) and 0 <= row["duration_ms"] <= 300000
    return [(row["stage"], row["outcome"]) for row in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("include_product_rows", [False, True])
async def test_product_stage_trace_preserves_result_reads_and_saved_evidence(
        monkeypatch, bundle_db, caplog, include_product_rows):
    orders = await mixed_orders(bundle_db)
    original_orders, before = deepcopy(orders), await documents(bundle_db)
    boundary = ProductBoundary(monkeypatch, bundle_db)
    caplog.set_level("INFO", logger="operational_demand_refresh")
    try:
        async def load():
            return await dashboard.build_mezan_v2_product_cost_cooperative(
                boundary.db, OWNER, orders, include_product_rows=include_product_rows,
                parallel_order_cost_reads=True)

        ordinary = await load()
        assert demand_context() is None
        assert not [record for record in caplog.records if "operational_refresh_stage" in record.message]
        ordinary_calls = deepcopy(boundary.calls)
        boundary.calls.clear()
        owned = await boundary.visible(load)

        assert owned == ordinary and json.dumps(owned) == json.dumps(ordinary)
        assert owned["total"] == 374  # frozen165 + corrected26 + ordinary18 + current165
        assert owned["missing_products_count"] == owned["incomplete_orders_count"] == 1
        assert boundary.calls == ordinary_calls
        assert boundary.authority_peak == 3
        assert product_trace(boundary) == [(stage, "complete") for stage in STAGES]
        assert orders == original_orders and await documents(bundle_db) == before
        boundary.settled()
    finally:
        boundary.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("collection,failed_stage", [
    (PRODUCTS, "profit_product_catalog"),
    (COST_PROFILES, "profit_product_inputs"),
    (LIFECYCLES, "profit_product_authority"),
    (PIECES, "profit_product_supplier"),
])
async def test_product_source_failure_names_only_the_stage_and_preserves_error(
        monkeypatch, bundle_db, collection, failed_stage):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    error = ExecutionTimeout("private source identity must not enter trace")
    boundary.errors[collection] = error
    before = await documents(bundle_db)
    try:
        with pytest.raises(ExecutionTimeout) as failure:
            await boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
                boundary.db, OWNER, [sale("failed", sale_line("line", product="plain"))],
                include_product_rows=False, parallel_order_cost_reads=True))
        assert failure.value is error
        prefix = [(stage, "complete") for stage in STAGES[:STAGES.index(failed_stage)]]
        assert product_trace(boundary) == prefix + [(failed_stage, "failed")]
        assert "private source identity" not in json.dumps(list(boundary.context.stage_timings))
        assert await documents(bundle_db) == before
        boundary.settled()
    finally:
        boundary.client.close()


@pytest.mark.asyncio
async def test_product_reduction_failure_is_not_replaced_by_diagnostics(monkeypatch, bundle_db):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    before = await documents(bundle_db)
    error = ValueError("private order evidence")

    def fail_reduction(order, policy):
        raise error

    monkeypatch.setattr(dashboard, "effective_product_cost", fail_reduction)
    try:
        with pytest.raises(ValueError) as failure:
            await boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
                boundary.db, OWNER, [sale("reduction", sale_line("line", product="plain"))],
                include_product_rows=False, parallel_order_cost_reads=True))
        assert failure.value is error
        assert product_trace(boundary) == [
            *[(stage, "complete") for stage in STAGES[:-1]],
            ("profit_product_reduce", "failed"),
        ]
        assert "private order evidence" not in json.dumps(list(boundary.context.stage_timings))
        assert await documents(bundle_db) == before
        boundary.settled()
    finally:
        boundary.client.close()


@pytest.mark.asyncio
async def test_product_source_deadline_keeps_timeout_contract_and_reports_cancelled_stage(
        monkeypatch, bundle_db):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    boundary.blocked = True
    before = await documents(bundle_db)

    async def reader(db, owner, name):
        assert name == "products"
        return await dashboard.build_mezan_v2_product_cost_cooperative(
            db, owner, [sale("deadline", sale_line("line", product="plain"))],
            include_product_rows=False, parallel_order_cost_reads=True)

    async def bounded_read():
        with source_read_budget(.5):
            return await load_collections(
                boundary.db, OWNER, ["products"], reader=reader, per_read_timeout=.2)

    try:
        with pytest.raises(SourceReadError) as failure:
            await boundary.visible(bounded_read)
        assert boundary.entered.is_set() and boundary.all_stopping.is_set()
        assert (failure.value.collection, failure.value.code) == (
            "products", "operational_source_read_timeout")
        assert product_trace(boundary) == [
            ("profit_product_catalog", "complete"),
            ("profit_product_inputs", "complete"),
            ("profit_product_authority", "cancelled"),
        ]
        assert await documents(bundle_db) == before
        boundary.settled()
    finally:
        boundary.client.close()


@pytest.mark.asyncio
async def test_product_disconnect_records_stage_only_after_owned_workers_are_joined(
        monkeypatch, bundle_db):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    boundary.blocked = True
    boundary.cleanup.clear()
    request, task = Request(), None
    before = await documents(bundle_db)
    try:
        task = asyncio.create_task(boundary.visible(
            lambda: dashboard.build_mezan_v2_product_cost_cooperative(
                boundary.db, OWNER, [sale("disconnect", sale_line("line", product="plain"))],
                include_product_rows=False, parallel_order_cost_reads=True),
            request=request))
        await asyncio.wait_for(boundary.entered.wait(), 1)
        request.disconnect()
        await asyncio.wait_for(boundary.all_stopping.wait(), 1)
        assert not task.done()
        assert product_trace(boundary) == [
            ("profit_product_catalog", "complete"),
            ("profit_product_inputs", "complete"),
        ]
        boundary.cleanup.set()
        with pytest.raises(HTTPException) as failure:
            await task
        assert failure.value.status_code == 499
        assert failure.value.detail["code"] == "operational_refresh_cancelled"
        assert product_trace(boundary)[-1] == ("profit_product_authority", "cancelled")
        assert await documents(bundle_db) == before
        boundary.settled()
    finally:
        boundary.cleanup.set()
        boundary.release.set()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        boundary.client.close()

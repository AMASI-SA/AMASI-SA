"""Actual runtime reducers, forced scheduling, reference identity and errors."""
import asyncio
import copy
import time
from unittest.mock import patch

import pytest
import dashboard_cpu_budget as cpu
import product_catalog_cost_resolution as catalog
import orders_db
import dashboard_v2_ads_executive as ads
from test_dashboard_b_index_profile_correctness import CASES, products_fixture, encoded, aliases


class EveryBoundary(cpu.CPUWorkBudget):
    async def checkpoint(self):
        self.cpu = time.thread_time()-self.seconds-1
        await super().checkpoint()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES)
async def test_index_values_and_reference_graph(case):
    products = products_fixture(case)
    before = encoded(products)
    expected = catalog.index_current_catalog_products(products)
    with patch.object(cpu, 'CPUWorkBudget', EveryBoundary):
        actual = await catalog.index_current_catalog_products_cooperative(products)
    assert encoded(actual) == encoded(expected)
    assert aliases(actual) == aliases(expected)
    assert encoded(products) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('products', [None, [7], ['bad-mapping'], [[('broken',)]], [{'id':'valid'}, 7]])
async def test_index_exception_parity(products):
    with pytest.raises(Exception) as expected:
        catalog.index_current_catalog_products(products)
    with patch.object(cpu, 'CPUWorkBudget', EveryBoundary), pytest.raises(type(expected.value)) as actual:
        await catalog.index_current_catalog_products_cooperative(products)
    assert str(actual.value) == str(expected.value)


@pytest.mark.asyncio
async def test_partial_index_is_not_returned_on_cancellation():
    entered = asyncio.Event()
    class Paused(cpu.CPUWorkBudget):
        async def checkpoint(self):
            entered.set()
            await asyncio.Event().wait()
    with patch.object(cpu, 'CPUWorkBudget', Paused):
        task = asyncio.create_task(catalog.index_current_catalog_products_cooperative(products_fixture(CASES[0])))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_independent_index_results_retain_shallow_input_references():
    products = products_fixture('variant_duplicates_and_raw')
    with patch.object(cpu, 'CPUWorkBudget', EveryBoundary):
        a, b = await asyncio.gather(catalog.index_current_catalog_products_cooperative(products),
                                    catalog.index_current_catalog_products_cooperative(products))
    assert a[0]['one'] is not b[0]['one']
    assert a[0]['one']['raw_salla_details'] is products[0]['raw_salla_details']
    assert a[0]['one']['variants'][0]['options'] is products[0]['variants'][0]['options']


@pytest.mark.asyncio
@pytest.mark.parametrize('orders', [[], [None], [7], [
    {'order_number':'a','total_amount':100,'currency':'SAR','source':'snapchat','payment_method':'mada'},
    {'order_number':'a','total_amount':10.005,'currency':'SAR','source':'meta','payment_method':'mada'},
    {'order_number':'b','total_amount':22,'currency':'USD','source':'google'},
    {'order_number':'c','total_amount':0,'currency':'SAR','source':'tiktok'},
]])
async def test_parser_order_rounding_currency_and_exception_parity(orders):
    before = copy.deepcopy(orders)
    try:
        expected = orders_db.orders_to_parsed(orders)
    except Exception as error:
        with patch.object(cpu, 'CPUWorkBudget', EveryBoundary), pytest.raises(type(error)) as actual:
            await orders_db.orders_to_parsed_cooperative(orders)
        assert str(actual.value) == str(error)
    else:
        with patch.object(cpu, 'CPUWorkBudget', EveryBoundary):
            actual = await orders_db.orders_to_parsed_cooperative(orders)
        assert encoded(actual) == encoded(expected)
    assert orders == before


@pytest.mark.asyncio
async def test_ads_platform_order_currency_rounding_and_duplicates():
    orders = [{'order_number':str(i//2), 'source':provider, 'total_amount':10.005,
               'currency':'SAR' if i%2 else 'USD'}
              for i, provider in enumerate(['snapchat','meta','tiktok','google','unknown']*3)]
    spend = {'breakdown':{'snapchat':2.555,'meta':0,'tiktok':8.005,'google_transitional':1.11}}
    expected = ads.build_salla_ads_executive_breakdown(orders, spend)
    with patch.object(cpu, 'CPUWorkBudget', EveryBoundary):
        actual = await ads.build_salla_ads_executive_breakdown_cooperative(orders, spend)
    assert encoded(actual) == encoded(expected)
    assert list(actual) == list(expected)

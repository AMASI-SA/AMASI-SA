"""Opt-in cooperative order preparation; no cross-query snapshot guarantee."""
from __future__ import annotations

import asyncio
from typing import Any

BATCH_SIZE = 128


async def _close_cursor(cursor):
    close = getattr(cursor, 'close', None)
    if close is None:
        return
    future = asyncio.ensure_future(close())
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
    future.result()
    if cancelled:
        raise asyncio.CancelledError


async def cooperative_order_rows(cursor: Any, limit: int):
    """Bound driver batches and Python accumulation, preserving cursor order."""
    if not hasattr(cursor, '__aiter__'):
        # Compatibility for existing lightweight adapters; actual Motor cursors
        # support async iteration and batch_size. No latency promise here.
        return await cursor.to_list(length=limit)
    if hasattr(cursor, 'batch_size'):
        cursor = cursor.batch_size(BATCH_SIZE)
    rows = []
    try:
        async for row in cursor:
            rows.append(row)
            if len(rows) % BATCH_SIZE == 0:
                await asyncio.sleep(0)
            if len(rows) >= limit:
                break
        return rows
    finally:
        await _close_cursor(cursor)


async def prepare_order_rows(orders, projected_rows, *, include_attribution,
                             hydrate_currency, attach_attribution, matches,
                             payment_methods, shipping_companies, included_statuses):
    # Currency: last row wins even when it has no raw payload. Attribution:
    # last row with a dict payload wins. These are the original helper rules.
    currency_by_number, attribution_by_number = {}, {}
    for index, row in enumerate(projected_rows):
        if index % BATCH_SIZE == 0:
            await asyncio.sleep(0)
        number = str(row.get('order_number') or '').strip()
        if number:
            currency_by_number[number] = row
            if isinstance(row.get('raw_by_source'), dict):
                attribution_by_number[number] = row
    included = []
    for offset in range(0, len(orders), BATCH_SIZE):
        await asyncio.sleep(0)
        chunk = orders[offset:offset + BATCH_SIZE]
        numbers = {str(row.get('order_number') or '').strip() for row in chunk}
        hydrate_currency(chunk, [currency_by_number[n] for n in numbers if n in currency_by_number])
        if include_attribution:
            attach_attribution(chunk, [attribution_by_number[n] for n in numbers if n in attribution_by_number])
        included.extend(order for order in chunk
                        if matches(order.get('payment_method', ''), payment_methods)
                        and matches(order.get('shipping_company', ''), shipping_companies)
                        and matches(order.get('order_status', ''), included_statuses))
    return included

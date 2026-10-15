"""Contract of transaction-local product reads; never an invoice state cache."""
from unittest.mock import AsyncMock, MagicMock
import pytest
from supplier_receiving_read_scope import ReceivingProductReadScope


@pytest.mark.asyncio
async def test_exact_query_session_and_scope_isolation_with_defensive_copies():
    db = MagicMock()
    collection = db.__getitem__.return_value
    collection.find_one = AsyncMock(return_value={'services': [{'cost': 5}]})
    transaction = object()
    scope = ReceivingProductReadScope(db, mongo_session=transaction, collections={'products'})
    query = {'user_id': 'a', 'variant_id': 'v1'}
    first = await scope['products'].find_one(query, session=transaction)
    first['services'][0]['cost'] = 999
    assert (await scope['products'].find_one(query, session=transaction))['services'][0]['cost'] == 5
    assert collection.find_one.await_count == 1
    await scope['products'].find_one({'user_id': 'b', 'variant_id': 'v1'}, session=transaction)
    await scope['products'].find_one({'user_id': 'a', 'variant_id': 'v2'}, session=transaction)
    await scope['products'].find_one(query, session=object())
    await scope['products'].find_one(query)
    assert collection.find_one.await_count == 5
    collection.find_one.return_value = {'services': [{'cost': 7}]}
    fresh = ReceivingProductReadScope(db, mongo_session=transaction, collections={'products'})
    assert (await fresh['products'].find_one(query, session=transaction))['services'][0]['cost'] == 7
    for name in ['pieces', 'source_events', 'sessions', 'invoices', 'ledger']:
        assert scope[name] is db[name]


@pytest.mark.asyncio
async def test_read_errors_are_not_cached_or_suppressed():
    db = MagicMock()
    db.__getitem__.return_value.find_one = AsyncMock(side_effect=[RuntimeError('read failed'), {'cost': 4}])
    transaction = object()
    scope = ReceivingProductReadScope(db, mongo_session=transaction, collections={'products'})
    with pytest.raises(RuntimeError, match='read failed'):
        await scope['products'].find_one({}, session=transaction)
    assert await scope['products'].find_one({}, session=transaction) == {'cost': 4}


@pytest.mark.asyncio
async def test_cursor_cache_preserves_projection_limit_and_customization():
    db = MagicMock()
    cursor = db.__getitem__.return_value.find.return_value
    cursor.to_list = AsyncMock(return_value=[{'cost': 5}])
    cursor.sort.return_value = cursor
    transaction = object()
    scope = ReceivingProductReadScope(db, mongo_session=transaction, collections={'products'})
    first = await scope['products'].find({}, {'cost': 1}, session=transaction).to_list(10)
    first[0]['cost'] = 999
    assert await scope['products'].find({}, {'cost': 1}, session=transaction).to_list(10) == [{'cost': 5}]
    assert cursor.to_list.await_count == 1
    await scope['products'].find({}, {'cost': 1}, session=transaction).to_list(20)
    await scope['products'].find({}, {'cost': 0}, session=transaction).to_list(10)
    await scope['products'].find({}, {'cost': 1}, session=transaction).sort('cost', -1).to_list(10)
    assert cursor.to_list.await_count == 4

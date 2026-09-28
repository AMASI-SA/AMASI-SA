"""No bulk-operation back door around protected local history admission."""
import os
import pytest
from pymongo import DeleteOne, DeleteMany, ReplaceOne, UpdateOne

from mezan_special_orders.binding import transaction
from mezan_special_orders.domain import DomainError
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.tests.test_financial_integration import run

pytestmark = pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'),
                               reason='Dedicated disposable replica set required')


@pytest.mark.parametrize('kind', ['delete_one', 'delete_many', 'replace', 'update'])
def test_bulk_mutations_cannot_bypass_scoped_history_guards(kind):
    async def scenario(h):
        order = await h.new_order(partial=False)
        before = await h.doc(order)
        query = {'tenant_id': OWNER.tenant_id, 'order_id': order['order_id']}
        mutations = {'delete_one': DeleteOne(query), 'delete_many': DeleteMany(query),
            'replace': ReplaceOne(query, {'tenant_id': OWNER.tenant_id}),
            'update': UpdateOne(query, {'$set': {'synthetic_mutation': True}})}
        async def attempt(scoped):
            with pytest.raises(DomainError, match='special_bulk_write_requires_explicit_adapter'):
                await scoped.mezan_special_orders_v1.bulk_write([mutations[kind]])
        await transaction(h.db, attempt, tenant_id=OWNER.tenant_id, scopes=frozenset({'workflow'}))
        assert await h.doc(order) == before
    run(scenario)

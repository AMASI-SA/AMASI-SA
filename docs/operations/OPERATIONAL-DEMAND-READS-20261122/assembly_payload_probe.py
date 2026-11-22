"""Manual synthetic assembly payload probe; never a Production performance claim.

Run from the repository root in a separately scheduled local test invocation:

    DEMAND_READ_TEST_MONGOD=/absolute/path/to/owned/mongod \
    PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
    python -m pytest --noconftest -p no:cacheprovider -s -q \
    docs/operations/OPERATIONAL-DEMAND-READS-20261122/assembly_payload_probe.py

The existing test fixture starts and stops an isolated loopback Mongo replica.
The inputs are entirely generated, with 5,000 orders and 500 display timeline
entries per order. No provider or existing database is contacted. Both cases
use identical synthetic indexes; the second case omits only the top-level
provider display timeline and must return the complete identical board.

This is deliberately outside automatic test discovery. There is no wall-time
pass/fail assertion. Run separately from other Mongo/load tests. The initial
recorded full-payload pass includes first-use/import/GC effects; thread CPU and
phase measurements help interpret that caveat but do not prove Production size,
latency, indexes, cold startup, or the existing mobile 15-second request budget.
"""
import json
import logging
import time
from bson import BSON
import pytest
from demand_read_mongo_fixture import read_db, read_mongo_uri
import preparation_piece_operations as pieces
import read_path_diagnostics as diag
from order_engine.repository import MongoAssemblyBoardRepository, MongoOrderRepository

@pytest.mark.asyncio
async def test_payload_probe(read_db, caplog, monkeypatch):
    db = read_db
    await db.unified_orders.create_index([('user_id', 1), ('order_number', 1)])
    await db[pieces.WORKFLOWS].create_index([('user_id',1),('updated_at',1)])
    await db[pieces.PIECES].create_index([('user_id',1),('order_number',1)])
    timeline = [{'id': str(i), 'date': '2026-11-01T08:00:00Z', 'message': 'Synthetic display history ' + ('x' * 80)} for i in range(500)]
    size = 0
    for start in range(0, 5000, 50):
        orders, workflows, physical = [], [], []
        for index in range(start, start + 50):
            number = str(index + 1000)
            raw = {'id': number, 'reference_id': number, 'date': '2026-11-01T08:00:00Z', 'status': 'in_progress',
                'customer': {'full_name': 'Synthetic customer'}, 'items': [{'id': number, 'name': 'Synthetic item', 'quantity':1}],
                'timeline': timeline}
            doc = {'user_id':'owner','order_number':number,'order_date':'2026-11-01T08:00:00Z','order_status':'in_progress','raw_by_source':{'salla_direct':raw}}
            size += len(BSON.encode(doc))
            orders.append(doc)
            workflows.append({'user_id':'owner','order_number':number,'stage':'in_progress','assembly_status':'completed',
                'ready_to_ship_source':'preparation_receipt','preparation_piece_count':1,'updated_at':number})
            physical.append({'user_id':'owner','order_number':number,'piece_id':'piece-'+number,'assembly_status':'ready','status':'ready_for_assembly'})
        await db.unified_orders.insert_many(orders)
        await db[pieces.WORKFLOWS].insert_many(workflows)
        await db[pieces.PIECES].insert_many(physical)
    print('SYNTHETIC_INPUT',json.dumps({'orders':5000,'timeline_entries_per_order':500,'bytes':size}),flush=True)
    caplog.set_level(logging.INFO,logger='mezan.read_path_diagnostics')
    result = None
    for name, repository in [('full', MongoOrderRepository), ('timeline_projection', MongoAssemblyBoardRepository)]:
        monkeypatch.setattr(pieces, 'MongoAssemblyBoardRepository', repository)
        diag._last.clear()
        caplog.clear()
        started = time.perf_counter()
        value = await pieces._assembly_order_board(db,user_id='owner',state='in_progress',limit=5000,offset=0)
        elapsed = time.perf_counter() - started
        if result is not None: assert value == result
        result = value
        samples = [json.loads(row.getMessage().split('read_path_sample ',1)[1]) for row in caplog.records if row.getMessage().startswith('read_path_sample ')]
        print('SYNTHETIC_RESULT',json.dumps({'variant':name,'elapsed_seconds':elapsed,'rows':len(value['items']),'sample':samples}),flush=True)

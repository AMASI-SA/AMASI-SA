"""Run the existing five overlap scenarios against the acceptance replica set."""
import pytest
from test_preparation_piece_operations import run_overlap_mongo_verification


@pytest.mark.asyncio
async def test_review_ready_supplier_overlap_real_replica_set():
    # The existing scenarios assert stock exactly once, rollback, virtual-zero
    # inventory, and supplier closure rollback across the integrated modules.
    await run_overlap_mongo_verification()

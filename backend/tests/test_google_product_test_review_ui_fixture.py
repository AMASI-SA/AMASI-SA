import json
import pytest
from tests.google_product_test_review_ui_fixture import generate,TARGET

@pytest.mark.asyncio
async def test_ui_fixture_matches_actual_authenticated_management_writer_and_review_routes():
    assert json.loads(TARGET.read_text())==await generate()

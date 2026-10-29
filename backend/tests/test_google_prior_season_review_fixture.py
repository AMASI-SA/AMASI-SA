import json
from pathlib import Path
import pytest
from tests.google_prior_season_review_fixture import fixture

@pytest.mark.asyncio
async def test_prior_review_frontend_fixture_matches_actual_http_writer_and_reader():
    path = Path(__file__).resolve().parents[2] / "frontend/src/services/__fixtures__/googlePriorSeasonReview.json"
    assert json.loads(path.read_text()) == await fixture()

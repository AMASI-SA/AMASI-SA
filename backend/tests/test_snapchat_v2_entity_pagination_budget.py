from snapchat_v2.client import MAX_ENTITY_ROWS, MAX_PAGES, MAX_PROVIDER_CALLS


def test_entity_pagination_budget_covers_declared_row_limit():
    # Snapchat entity discovery requests up to 200 rows per page. The page
    # safety ceiling must therefore be large enough to reach MAX_ENTITY_ROWS
    # without reporting a false snapchat_pagination_incomplete error.
    assert MAX_PAGES * 200 >= MAX_ENTITY_ROWS
    assert MAX_PROVIDER_CALLS > MAX_PAGES

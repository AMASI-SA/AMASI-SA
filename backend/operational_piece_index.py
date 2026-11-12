"""Request-local lookup of preparation pieces; no financial source changes."""
from operational_demand_refresh import calculation_checkpoint


def index_preparation_pieces(pieces, item_identity):
    """Preserve the original string/item match, row order and duplicate rows.

    The caller supplies its existing physical item normalizer. Every value is
    an ordered list: duplicate pieces must still reach quantity/unit coverage
    checks, never disappear because an index deduplicated their identities.
    """
    result = {}
    for piece in pieces:
        calculation_checkpoint()
        key = str(piece.get('order_number')), item_identity(piece)
        result.setdefault(key, []).append(piece)
    calculation_checkpoint()
    return result

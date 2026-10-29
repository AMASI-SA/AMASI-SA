"""Creation-only bounded catalog projection; shared product reads stay unchanged."""
from product_inventory_proof import INVENTORY_EVIDENCE, inventory_readiness
from .google_ads_ai import PRODUCT_PROJECTION as _PRODUCT_PROJECTION

PRODUCT_PROJECTION = {**_PRODUCT_PROJECTION, INVENTORY_EVIDENCE: 1, "variants_count": 1,
                      "options_count": 1, "details_synced_at": 1}

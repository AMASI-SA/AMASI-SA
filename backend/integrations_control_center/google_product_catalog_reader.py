"""Bounded owner-scoped V2 catalog reader; no selection or provider writes.

Source verification establishes only a recently synced V2 catalog row. It does
not establish category membership, profitability, a
landing URL or permission to advertise. Optional immutable details evidence
establishes only bounded stored stock, not full remote variant coverage.
Duplicates fail closed.
"""
from datetime import datetime, timezone
import hashlib
import re

from .google_product_evidence import ProductSource
from product_inventory_proof import INVENTORY_EVIDENCE, MAX_INVENTORY_VARIANTS, inventory_readiness

COLLECTION = 'mezan_products_v2'
PROJECTION = {'_id': 0, 'user_id': 1, 'salla_product_id': 1, 'mezan_product_id': 1,
              'source': 1, 'source_revision': 1, 'last_synced_at': 1, 'status': 1,
              'archived': 1, 'quantity': 1, 'unlimited_quantity': 1, 'variants_count': 1,
              'variants': {'$slice': MAX_INVENTORY_VARIANTS + 1}, 'options_count': 1, 'details_loaded': 1,
              'details_synced_at': 1, INVENTORY_EVIDENCE: 1}


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,30}', value):
        raise ValueError('invalid product identity')
    return value


async def read_catalog_products(db, *, owner_id, product_ids, as_of, max_age_seconds):
    if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
        raise ValueError('invalid owner')
    if (not isinstance(product_ids, list) or not 1 <= len(product_ids) <= 100
        or len(set(product_ids)) != len(product_ids)):
        raise ValueError('invalid requested products')
    ids = [_identifier(value) for value in product_ids]
    if (not isinstance(as_of, datetime) or as_of.tzinfo is None
        or type(max_age_seconds) is not int or not 60 <= max_age_seconds <= 604800):
        raise ValueError('invalid freshness bound')
    rows = await db[COLLECTION].find({'user_id': owner_id, 'salla_product_id': {'$in': ids}}, PROJECTION).to_list(length=len(ids)+1)
    by_id = {}
    for row in rows:
        pid = row.get('salla_product_id')
        if row.get('user_id') != owner_id or pid not in ids or pid in by_id:
            raise ValueError('ambiguous catalog identity')
        by_id[pid] = row
    output = []
    missing = []
    inventory_reviews = []
    for pid in ids:
        row = by_id.get(pid)
        if row is None:
            missing.append(pid)
            continue
        observed = row.get('last_synced_at')
        if isinstance(observed, datetime) and observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)  # Motor default UTC decoding
        revision = row.get('source_revision')
        valid = (row.get('source') == 'salla' and row.get('mezan_product_id') == 'mpv2_'+pid
                 and isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{64}', revision)
                 and isinstance(observed, datetime) and observed.tzinfo is not None
                 and 0 <= (as_of-observed).total_seconds() <= max_age_seconds)
        if not valid:
            missing.append(pid)
            continue
        status = 'unknown'
        quantity = None
        unlimited = False
        raw_status = row.get('status')
        if row.get('archived') is True or raw_status == 'inactive':
            status = 'hidden'
        elif raw_status == 'out_of_stock':
            status = 'out_of_stock'
        # Light sync never proves purchasable stock. Only the immutable details
        # proof can establish this bounded snapshot; this is not remote coverage.
        state, digest = inventory_readiness(row, as_of, max_age_seconds)
        observed_details = None
        if state != 'unproved':
            stamp = row['details_synced_at']
            if not isinstance(stamp, datetime):
                stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            observed_details = (stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)).isoformat()
        inventory_reviews.append({'product_id': pid, 'state': state,
            'observed_at': observed_details, 'stock_digest': digest,
            'scope': 'stored_details_snapshot', 'maximum_supported_variants': MAX_INVENTORY_VARIANTS,
            'remote_variant_coverage_complete': False,
            'size_suitability_verified': False, 'preparation_time_verified': False})
        if status == 'unknown' and raw_status == 'active' and row.get('archived') is False:
            if state == 'available' and row.get('variants_count') == 0:
                status = 'available'
                unlimited = row['unlimited_quantity']
                quantity = str(row['quantity']) if row.get('quantity') is not None else None
            # Variant parent stock is not purchasable variant quantity.
            # A bounded unavailable details snapshot never proves remote
            # whole-product stockout; preserve unknown unless raw status says so.
        ref = 'catalog-v2:' + hashlib.sha256((owner_id+'\0'+pid+'\0'+revision+'\0'+observed.isoformat()).encode()).hexdigest()
        output.append(ProductSource(product_id=pid, mezan_product_id='mpv2_'+pid,
            observed_at=observed, evidence_ref=ref, verification='verified', status=status,
            available_quantity=quantity, unlimited_quantity=unlimited, cost_quality='unknown'))
    return {'products': output, 'inventory_reviews': inventory_reviews, 'missing_or_unverified_product_ids': missing,
            'category_membership_verified': False, 'variant_availability_verified': False,
            'source_writes': 0, 'execution_authorized': False}

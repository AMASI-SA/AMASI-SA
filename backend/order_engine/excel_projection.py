"""Explicit, read-only DTO projection for Salla Excel imports in Preview.

Never creates a Salla-shaped provider payload or mutates imported records.
Missing product/payment facts remain unknown; no amounts or rates are inferred.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
import json
import hashlib
from zoneinfo import ZoneInfo
from order_currency import salla_order_currency_fields

from .models import OrderDTO, OrderSourceDTO, CustomerDTO, PaymentDTO, ShippingDTO, MoneyTotalsDTO, OrderItemDTO, AddressDTO
from .mapper import OrderMappingError


EXCEL_ROOT_FIELDS = ('order_id', 'order_date_raw', 'currency', 'total_amount',
                     'subtotal', 'discount', 'shipping_cost', 'preview_excel_verification')


def discovery_source_query():
    direct = {'raw_by_source.salla_direct': {'$exists': True}}
    if os.environ.get('MEZAN_PREVIEW_EXCEL_ORDERS') != '1':
        return direct
    return {'$or': [direct, {'raw_by_source.excel': {'$type': 'object'},
                            'raw_by_source.excel.simulated': {'$exists': False}}]}


def map_excel_order(row):
    excel = row.get('raw_by_source', {}).get('excel')
    if not isinstance(excel, dict) or 'simulated' in excel:
        raise OrderMappingError('invalid Excel source')
    verification = row.get('preview_excel_verification') or {}
    if verification:
        fingerprint = hashlib.sha256(json.dumps(excel, sort_keys=True, default=str).encode()).hexdigest()
        if (verification.get('order_number') != str(row.get('order_number'))
                or verification.get('excel_fingerprint') != fingerprint
                or verification.get('source') not in {'salla_order_read', 'salla_invoice_read'}):
            raise OrderMappingError('stale or invalid Excel verification')
    verified_amounts = verification.get('amounts') or {}
    def value(key):
        return row.get(key) if row.get(key) is not None else excel.get(key)
    def amount(key):
        raw = verified_amounts.get(key, value(key))
        if raw in (None, ''):
            return 0.0
        try:
            result = Decimal(str(raw).replace(',', ''))
            if not result.is_finite():
                raise ValueError('non-finite amount')
            return float(result)
        except (InvalidOperation, ValueError) as exc:
            raise OrderMappingError('invalid Excel amount') from exc
    number = str(value('order_number') or '').strip()
    currency = str(verified_amounts.get('currency') or value('currency') or '').strip().upper()
    if not number or not currency or value('total_amount') in (None, ''):
        raise OrderMappingError('Excel order identity/currency/total missing')
    raw_date = value('order_date_raw') or value('order_date')
    try:
        created = raw_date if isinstance(raw_date, datetime) else datetime.fromisoformat(str(raw_date).strip().strip("'\"").replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=ZoneInfo('Asia/Riyadh'))
    except (TypeError, ValueError) as exc:
        raise OrderMappingError('invalid Excel original date') from exc
    items = []
    if excel.get('skus_json'):
        try:
            tuples = json.loads(excel['skus_json'])
            if not isinstance(tuples, list):
                raise ValueError('item list required')
            for index, entry in enumerate(tuples):
                if not isinstance(entry, list) or len(entry) != 5:
                    raise ValueError('unsupported Excel item tuple')
                name, quantity, sku, catalog_price, line_total = entry
                qty, total = Decimal(str(quantity)), Decimal(str(line_total))
                if not qty.is_finite() or qty <= 0 or not total.is_finite() or total < 0:
                    raise ValueError('invalid item quantity or total')
                # Export's fifth value is the net line amount. Do not treat
                # the catalogue-price column as the actual selling price.
                items.append(OrderItemDTO(
                    order_item_id=f'excel:{number}:{index}', name=str(name),
                    sku=str(sku) if sku else None, quantity=float(qty),
                    unit_price=float(total / qty), total=float(total),
                ))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise OrderMappingError('invalid Excel items') from exc
    if verification.get('items') is not None:
        if items:
            raise OrderMappingError('invoice enrichment must not replace exported items')
        items = [OrderItemDTO(**item) for item in verification['items']]
    # Reuse the accounting currency contract in memory. Only order-read
    # evidence bound to this Excel snapshot can supply a foreign exchange rate.
    currency_fields = salla_order_currency_fields({
        'currency': currency, 'total_amount': amount('total_amount'),
        'exchange_rate': (verification.get('exchange_rate_reported')
                          if verification.get('source') == 'salla_order_read' else None),
    })
    return OrderDTO(
        order_id=str(value('order_id') or number), order_number=number, created_at=created,
        status=value('order_status'), status_native=value('order_status'),
        source=OrderSourceDTO(source_order_id=str(value('order_id') or '') or None,
                              source_reference=number, source_event='excel.import'),
        customer=CustomerDTO(name=value('customer_name'), mobile=value('customer_mobile')),
        payment=PaymentDTO(method=value('payment_method'), method_native=value('payment_method'),
                           collection_status='unknown'),
        shipping=ShippingDTO(company=value('shipping_company'), address=AddressDTO(
            city=excel.get('customer_city'), country=excel.get('customer_country'),
            formatted=excel.get('customer_address'))),
        items=items,
        totals=MoneyTotalsDTO(currency=currency, total=amount('total_amount'),
                             total_sar=currency_fields['total_amount_sar'],
                             exchange_rate_to_sar=currency_fields['exchange_rate_to_sar'],
                             conversion_status=currency_fields['currency_conversion_status'],
                             conversion_source=currency_fields['currency_conversion_source'],
                             subtotal=amount('subtotal'), discount=amount('discount'),
                             shipping=amount('shipping_cost'),
                             tax_reported_by_source=float(verified_amounts.get('tax', excel.get('tax')) or 0)),
    )

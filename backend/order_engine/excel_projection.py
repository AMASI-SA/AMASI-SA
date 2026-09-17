"""Explicit, read-only DTO projection for Salla Excel imports in Preview.

Never creates a Salla-shaped provider payload or mutates imported records.
Missing product/payment facts remain unknown; no amounts or rates are inferred.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
from zoneinfo import ZoneInfo

from .models import OrderDTO, OrderSourceDTO, CustomerDTO, PaymentDTO, ShippingDTO, MoneyTotalsDTO
from .mapper import OrderMappingError


EXCEL_ROOT_FIELDS = ('order_id', 'order_date_raw', 'currency', 'total_amount',
                     'subtotal', 'discount', 'shipping_cost')


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
    def value(key):
        return row.get(key) if row.get(key) is not None else excel.get(key)
    def amount(key):
        raw = value(key)
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
    currency = str(value('currency') or '').strip().upper()
    if not number or not currency or value('total_amount') in (None, ''):
        raise OrderMappingError('Excel order identity/currency/total missing')
    raw_date = value('order_date_raw') or value('order_date')
    try:
        created = raw_date if isinstance(raw_date, datetime) else datetime.fromisoformat(str(raw_date).strip().strip("'\"").replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=ZoneInfo('Asia/Riyadh'))
    except (TypeError, ValueError) as exc:
        raise OrderMappingError('invalid Excel original date') from exc
    return OrderDTO(
        order_id=str(value('order_id') or number), order_number=number, created_at=created,
        status=value('order_status'), status_native=value('order_status'),
        source=OrderSourceDTO(source_order_id=str(value('order_id') or '') or None,
                              source_reference=number, source_event='excel.import'),
        customer=CustomerDTO(name=value('customer_name'), mobile=value('customer_mobile')),
        payment=PaymentDTO(method=value('payment_method'), method_native=value('payment_method'),
                           collection_status='unknown'),
        shipping=ShippingDTO(company=value('shipping_company')),
        totals=MoneyTotalsDTO(currency=currency, total=amount('total_amount'),
                             subtotal=amount('subtotal'), discount=amount('discount'),
                             shipping=amount('shipping_cost')),
    )

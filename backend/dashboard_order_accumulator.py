"""Dashboard-only bounded, two-pass reduction using unchanged canonical calculators.

Callers must replay the same projected order snapshot for the fee pass. This
module does not query or write a database and never retains individual orders.
"""
from copy import deepcopy
import hashlib
import json
from itertools import islice
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from excel_parser import match_settings, normalize_name, _payment_synonym_match
from payment_methods import normalize_payment_method, KNOWN_PAYMENT_SUB_KEYS, SALLA_SUB_KEYS
from orders_db import orders_to_parsed
from order_currency import order_total_sar
from shipping_cost_ssot import shipping_breakdown

_UNRESOLVED = object()


def _same_method(raw, grouped):
    # Identity matching only; monetary calculations remain in match_settings.
    if normalize_name(raw) == normalize_name(grouped):
        return True
    raw_sub = normalize_payment_method(raw)[0]
    grouped_sub = normalize_payment_method(grouped)[0]
    if raw_sub in KNOWN_PAYMENT_SUB_KEYS and grouped_sub in KNOWN_PAYMENT_SUB_KEYS:
        return raw_sub == grouped_sub
    return _payment_synonym_match(normalize_name(raw), normalize_name(grouped))


def summarize_dashboard_metadata(orders, policy, effective_cost, total_sar, store):
    """Fuse read-only metadata passes; preserve each sum's original order.

    In particular the product-cost generator is still consumed by built-in
    sum, preserving the interpreter's float summation implementation.
    """
    from dashboard_financial_pages import group_map
    result = dict(incomplete_profit_orders_count=0, no_products_orders_count=0,
                  excel_no_products_count=0, missing_cost_skus=store.set('legacy-missing-cost'),
                  monthly_sales=group_map(store, 'financial-months'),
                  monthly_unverified_currency=store.set('financial-unverified-months'),
                  src_counts=group_map(store, 'financial-source-counts'))
    for source in ('excel', 'make', 'unified'):
        result['src_counts'][source] = 0
    def costs():
        for order in orders:
            cost = effective_cost(order, policy)
            for line in (order.get('missing_product_cost_lines') or []):
                key = (line.get('sku') or line.get('product_id') or line.get('name') or '').strip().upper()
                if key:
                    result['missing_cost_skus'].add(key)
            status = (order.get('profit_status') or '').strip()
            source = (order.get('data_source') or '').strip().lower()
            if not status:
                status = ('incomplete_no_products' if not (order.get('products') or []) else
                          'incomplete_missing_cost' if order.get('missing_product_cost_lines') else 'complete')
            if status != 'complete':
                result['incomplete_profit_orders_count'] += 1
            if status == 'incomplete_no_products':
                result['no_products_orders_count'] += 1
                if source in ('excel', ''):
                    result['excel_no_products_count'] += 1
            month = (order.get('order_date') or '')[:7]
            if month:
                monthly = result['monthly_sales']
                monthly[month] = monthly.get(month, 0.0) + 0.0
                amount = total_sar(order)
                if amount is None:
                    result['monthly_unverified_currency'].add(month)
                else:
                    monthly[month] += amount
            source_key = order.get('data_source') or 'unified'
            sources = result['src_counts']
            sources[source_key] = sources.get(source_key, 0) + 1
            yield cost
    result['computed_product_cost'] = round(sum(costs()), 2)
    return result


class DashboardOrderAccumulator:
    def __init__(self, payment_settings, shipping_settings, company_configs):
        from dashboard_order_reads import dashboard_spill
        from dashboard_financial_pages import group_map
        self.store = dashboard_spill()
        self.payment_settings = payment_settings
        self.shipping_settings = shipping_settings
        self.company_configs = company_configs
        self.count = 0
        self.sales = 0.0
        self.known_sar = Decimal('0')
        self.converted = 0
        self.missing = []
        self.samples = []
        self.payments = group_map(self.store, 'financial-payments')
        self.shippings = group_map(self.store, 'financial-shipping')
        self.sources = group_map(self.store, 'financial-sources')
        self.shipping = dict(total_base=0.0,total_tax=0.0,total_with_tax=0.0,
                             orders_count=0,per_company=group_map(self.store, 'financial-carriers'))
        self.phase = 'collect'
        self.fee_count = 0
        self.fees = group_map(self.store, 'financial-fees')
        self.first_digest = hashlib.sha256()
        self.replay_digest = hashlib.sha256()
        self._same_method = lru_cache(maxsize=128)(_same_method)

    @property
    def retained_sample_count(self):
        return len(self.samples)

    @property
    def payment_group_count(self):
        return len(self.payments)

    @staticmethod
    def _fingerprint(digest, order, amount=_UNRESOLVED):
        proof = [str(order.get('order_number') or ''),
                 order_total_sar(order) if amount is _UNRESOLVED else amount, order.get('payment_method')]
        payload = json.dumps(proof, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)

    def observe(self, order, *, parsed=None, shipping=None):
        """Observe one order, optionally reusing its canonical calculations.

        Prepared values must come from this same order and request/configuration.
        They are read only; the returned pair can feed another dashboard cohort
        without recalculating currency, attribution, or canonical shipping.
        """
        if self.phase != 'collect':
            raise RuntimeError('Order collection already finalized')
        one = orders_to_parsed([order]) if parsed is None else parsed
        amount = (one['orders_individual'][0]['total_amount']
                  if one['currency_conversion']['complete'] else None)
        self._fingerprint(self.first_digest, order, amount)
        self.count += 1
        if amount is None:
            if len(self.missing)<100:
                self.missing.append(str(order.get('order_number') or 'unknown'))
        else:
            self.converted += 1
            self.known_sar += Decimal(str(amount))
        numeric = float(amount) if amount is not None else 0.0
        self.sales += numeric
        for target, key in ((self.payments,'payment_methods'),(self.shippings,'shipping_companies'),(self.sources,'order_sources')):
            entry = one[key][0]
            group=target.setdefault(entry['name'],dict(name=entry['name'],orders_count=0,**({'total_sales':0.0} if key!='shipping_companies' else {})))
            group['orders_count'] += 1
            if 'total_sales' in group:
                group['total_sales'] += numeric
        if len(self.samples)<10:
            self.samples.append(deepcopy(one['orders_sample'][0]))
        bd=shipping_breakdown(order,self.company_configs) if shipping is None else shipping
        company=(order.get('shipping_company') or '—').strip() or '—'
        shipping=self.shipping
        shipping['orders_count']+=1
        for target,source in (('total_base','base'),('total_tax','tax'),('total_with_tax','total')):
            shipping[target]+=bd[source]
        group=shipping['per_company'].setdefault(company,dict(name=company,orders_count=0,base=0.0,tax=0.0,total=0.0,vat_rate=bd['vat_rate']))
        group['orders_count']+=1
        for key in ('base','tax','total'):
            group[key]+=bd[key]
        return one, bd

    def begin_fee_pass(self):
        if self.phase != 'collect':
            raise RuntimeError('Fee pass already started')
        self.phase='fees'
        # Canonical per-order rounding requires the number of matching orders
        # to equal this exact raw display group's count. Another raw alias on
        # the same recognized rail guarantees a larger matching count, hence
        # aggregate_fallback regardless of amounts or per-alias settings.
        # Skip only these provably unused replays. Remaining groups still use
        # _same_method, including its exact-name/unknown-synonym behavior.
        rail_counts = {}
        for name, group in self.payments.items():
            rail = normalize_payment_method(name)[0]
            if rail in SALLA_SUB_KEYS:
                rail_counts[rail] = rail_counts.get(rail, 0) + group['orders_count']
        for name, group in self.payments.items():
            rail = normalize_payment_method(name)[0]
            if rail in SALLA_SUB_KEYS and group['orders_count'] == rail_counts[rail]:
                self.fees[name] = dict(count=0,base=Decimal('0'),vat=Decimal('0'))

    def observe_fees(self, order):
        self.observe_fee_batch([order])

    def observe_fee_batch(self, orders):
        """Replay at most 128 orders through the canonical per-order calculator.

        Each final display group is calculated separately: aliases may select
        different configurations and must retain the original count fallback.
        """
        if self.phase!='fees':
            raise RuntimeError('Call begin_fee_pass before replay')
        batch=list(islice(iter(orders),129))
        if len(batch)>128:
            raise ValueError('Fee batches must contain at most 128 orders')
        individuals=[]
        for order in batch:
            self.fee_count+=1
            amount=order_total_sar(order)
            self._fingerprint(self.replay_digest, order, amount)
            numeric=float(amount) if amount is not None else 0.0
            raw=(order.get('payment_method') or 'غير محدد').strip() or 'غير محدد'
            individuals.append((raw,numeric))
        for name,state in self.fees.items():
            matching=[dict(payment_method=name,total_amount=numeric)
                      for raw,numeric in individuals if self._same_method(raw,name)]
            if not matching:
                continue
            state['count']+=len(matching)
            # Use the FINAL group's configuration, not the alias's config.
            parsed={'payment_methods':[dict(name=name,orders_count=len(matching),
                                           total_sales=sum(row['total_amount'] for row in matching))],
                    'shipping_companies':[],
                    'orders_individual':matching}
            matched=match_settings(parsed,self.payment_settings,self.shipping_settings)['payment_breakdown'][0]
            state['base']+=Decimal(str(matched['base_commission']))
            state['vat']+=Decimal(str(matched['vat_amount']))

    def finish(self):
        if (self.phase!='fees' or self.fee_count!=self.count
                or self.first_digest.digest()!=self.replay_digest.digest()):
            raise RuntimeError('Complete identical order replay is required')
        if self.store is not None:
            return self._finish_bounded()
        parsed=dict(total_sales=round(self.sales,2),total_orders=self.count,
            accounting_currency='SAR',currency_conversion=dict(
                complete=self.converted==self.count,
                known_total_sar=float(self.known_sar.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)),
                unverified_orders_count=self.count-self.converted,
                missing_order_numbers=list(self.missing),unknown_is_zero=False),
            payment_methods=[dict(v,total_sales=round(v['total_sales'],2)) for v in sorted(self.payments.values(),key=lambda v:-v['total_sales'])],
            shipping_companies=sorted(self.shippings.values(),key=lambda v:-v['orders_count']),
            order_sources=[dict(v,total_sales=round(v['total_sales'],2)) for v in sorted(self.sources.values(),key=lambda v:-v['orders_count'])],
            orders_sample=deepcopy(self.samples),detected_columns={'unified':True})
        matched=match_settings(parsed,self.payment_settings,self.shipping_settings)
        for row in matched['payment_breakdown']:
            state=self.fees.get(row['name'])
            if state is None or state['count']!=row['orders_count'] or not state['count']:
                continue
            row['base_commission']=float(state['base'])
            row['vat_amount']=float(state['vat'])
            row['fee_amount']=round(row['base_commission']+row['vat_amount'],2)
            row['net_amount']=round(row['total_sales']-row['fee_amount'],2)
            row['fee_calculation_basis']='per_order_salla_rounding'
        matched['total_payment_fees']=round(sum(row['fee_amount'] for row in matched['payment_breakdown']),2)
        shipping=deepcopy(self.shipping)
        for key in ('total_base','total_tax','total_with_tax'):
            shipping[key]=round(shipping[key],2)
        for row in shipping['per_company'].values():
            for key in ('base','tax','total'):
                row[key]=round(row[key],2)
            for target,key in (('cost_per_unit','base'),('tax_per_unit','tax'),('total_per_unit','total')):
                row[target]=round(row[key]/(row['orders_count'] or 1),2)
        return dict(parsed=parsed,matched=matched,shipping=shipping)

    def _finish_bounded(self):
        from dashboard_financial_pages import FinancialRows
        payments = FinancialRows(self.store, self.payments.values(),
                                 number=lambda row: row['total_sales'], descending=True)
        shippings = FinancialRows(self.store, self.shippings.values(),
                                  number=lambda row: row['orders_count'], descending=True)
        sources = FinancialRows(self.store, self.sources.values(),
                                number=lambda row: row['orders_count'], descending=True)
        for rows in (payments, sources):
            for row in rows:
                row['total_sales'] = round(row['total_sales'], 2)
        parsed = dict(total_sales=round(self.sales,2),total_orders=self.count,
            accounting_currency='SAR',currency_conversion=dict(
                complete=self.converted==self.count,
                known_total_sar=float(self.known_sar.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)),
                unverified_orders_count=self.count-self.converted,
                missing_order_numbers=list(self.missing),unknown_is_zero=False),
            payment_methods=payments, shipping_companies=shippings, order_sources=sources,
            orders_sample=deepcopy(self.samples),detected_columns={'unified':True})
        def payment_rows():
            for payment in payments:
                row = match_settings(dict(payment_methods=[payment],shipping_companies=[]),
                                     self.payment_settings,self.shipping_settings)['payment_breakdown'][0]
                state = self.fees.get(row['name'])
                if state is not None and state['count'] == row['orders_count'] and state['count']:
                    row['base_commission'] = float(state['base'])
                    row['vat_amount'] = float(state['vat'])
                    row['fee_amount'] = round(row['base_commission']+row['vat_amount'],2)
                    row['net_amount'] = round(row['total_sales']-row['fee_amount'],2)
                    row['fee_calculation_basis'] = 'per_order_salla_rounding'
                yield row
        payment_breakdown = FinancialRows(self.store, payment_rows())
        def shipping_rows():
            for company in shippings:
                yield match_settings(dict(payment_methods=[],shipping_companies=[company]),
                                     self.payment_settings,self.shipping_settings)['shipping_breakdown'][0]
        shipping_breakdown = FinancialRows(self.store, shipping_rows())
        matched = dict(payment_breakdown=payment_breakdown,shipping_breakdown=shipping_breakdown,
            total_payment_fees=round(sum(row['fee_amount'] for row in payment_breakdown),2),
            total_shipping_cost=round(sum(row['total_cost'] for row in shipping_breakdown),2),
            deferred_shipping_cost=round(sum(row['total_cost'] for row in shipping_breakdown if row['is_deferred']),2))
        shipping = dict(self.shipping)
        for key in ('total_base','total_tax','total_with_tax'):
            shipping[key] = round(shipping[key],2)
        for row in shipping['per_company'].values():
            for key in ('base','tax','total'):
                row[key] = round(row[key],2)
            for target,key in (('cost_per_unit','base'),('tax_per_unit','tax'),('total_per_unit','total')):
                row[target] = round(row[key]/(row['orders_count'] or 1),2)
        return dict(parsed=parsed,matched=matched,shipping=shipping)

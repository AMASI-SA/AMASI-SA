"""Dashboard-only bounded, two-pass reduction using unchanged canonical calculators.

Callers must replay the same projected order snapshot for the fee pass. This
module does not query or write a database and never retains individual orders.
"""
from copy import deepcopy
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from excel_parser import match_settings, normalize_name, _payment_synonym_match
from payment_methods import normalize_payment_method, KNOWN_PAYMENT_SUB_KEYS, SALLA_SUB_KEYS
from orders_db import orders_to_parsed
from order_currency import order_total_sar
from shipping_cost_ssot import shipping_breakdown


def _same_method(raw, grouped):
    # Identity matching only; monetary calculations remain in match_settings.
    if normalize_name(raw) == normalize_name(grouped):
        return True
    raw_sub = normalize_payment_method(raw)[0]
    grouped_sub = normalize_payment_method(grouped)[0]
    if raw_sub in KNOWN_PAYMENT_SUB_KEYS and grouped_sub in KNOWN_PAYMENT_SUB_KEYS:
        return raw_sub == grouped_sub
    return _payment_synonym_match(normalize_name(raw), normalize_name(grouped))


class DashboardOrderAccumulator:
    def __init__(self, payment_settings, shipping_settings, company_configs):
        self.payment_settings = payment_settings
        self.shipping_settings = shipping_settings
        self.company_configs = company_configs
        self.count = 0
        self.sales = 0.0
        self.known_sar = Decimal('0')
        self.converted = 0
        self.missing = []
        self.samples = []
        self.payments = {}
        self.shippings = {}
        self.sources = {}
        self.shipping = dict(total_base=0.0,total_tax=0.0,total_with_tax=0.0,
                             orders_count=0,per_company={})
        self.phase = 'collect'
        self.fee_count = 0
        self.fees = {}
        self.first_digest = hashlib.sha256()
        self.replay_digest = hashlib.sha256()

    @property
    def retained_sample_count(self):
        return len(self.samples)

    @property
    def payment_group_count(self):
        return len(self.payments)

    @staticmethod
    def _fingerprint(digest, order):
        proof = [str(order.get('order_number') or ''),
                 order_total_sar(order), order.get('payment_method')]
        payload = json.dumps(proof, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)

    def observe(self, order):
        if self.phase != 'collect':
            raise RuntimeError('Order collection already finalized')
        self._fingerprint(self.first_digest, order)
        one = orders_to_parsed([order])
        amount = order_total_sar(order)
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
            self.samples.append(one['orders_sample'][0])
        bd=shipping_breakdown(order,self.company_configs)
        company=(order.get('shipping_company') or '—').strip() or '—'
        shipping=self.shipping
        shipping['orders_count']+=1
        for target,source in (('total_base','base'),('total_tax','tax'),('total_with_tax','total')):
            shipping[target]+=bd[source]
        group=shipping['per_company'].setdefault(company,dict(name=company,orders_count=0,base=0.0,tax=0.0,total=0.0,vat_rate=bd['vat_rate']))
        group['orders_count']+=1
        for key in ('base','tax','total'):
            group[key]+=bd[key]

    def begin_fee_pass(self):
        if self.phase != 'collect':
            raise RuntimeError('Fee pass already started')
        self.phase='fees'
        self.fees={name:dict(count=0,base=Decimal('0'),vat=Decimal('0'))
                   for name in self.payments
                   if normalize_payment_method(name)[0] in SALLA_SUB_KEYS}

    def observe_fees(self, order):
        if self.phase!='fees':
            raise RuntimeError('Call begin_fee_pass before replay')
        self._fingerprint(self.replay_digest, order)
        self.fee_count+=1
        amount=order_total_sar(order)
        numeric=float(amount) if amount is not None else 0.0
        raw=(order.get('payment_method') or 'غير محدد').strip() or 'غير محدد'
        for name,state in self.fees.items():
            if not _same_method(raw,name):
                continue
            state['count']+=1
            # Use the FINAL group's configuration, not the alias's config.
            parsed={'payment_methods':[dict(name=name,orders_count=1,total_sales=numeric)],
                    'shipping_companies':[],
                    'orders_individual':[dict(payment_method=name,total_amount=numeric)]}
            matched=match_settings(parsed,self.payment_settings,self.shipping_settings)['payment_breakdown'][0]
            state['base']+=Decimal(str(matched['base_commission']))
            state['vat']+=Decimal(str(matched['vat_amount']))

    def finish(self):
        if (self.phase!='fees' or self.fee_count!=self.count
                or self.first_digest.digest()!=self.replay_digest.digest()):
            raise RuntimeError('Complete identical order replay is required')
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

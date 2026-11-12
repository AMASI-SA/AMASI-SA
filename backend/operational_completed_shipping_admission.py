"""Bound newly eligible completion fees to a saved activation or explicit review.

Reading sources never writes an activation marker or grants an approval. The
first reconciliation saves the boundary; earlier new eligibility remains a
read-only candidate until the owner reviews its opening and existing payouts.
"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal

from operational_balance_store import digest
from operational_delivery_evidence import _instant
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint

MARKER = 'completed_shipping_admission'
APPROVALS = 'completed_shipping_fee_approvals'
HISTORY_REVIEW = 'operational_shipping_completion_history_review_required'


def recorded_base_fee(state, order_id, *, review_index=None):
    """A separately proven COD commission is not a previously earned base fee."""
    preparation_checkpoint()
    if review_index is not None:
        review_index.check_state(state)
        return review_index.recorded(str(order_id))
    engine = (state or {}).get('engine') or {}
    facts, obligations = engine.get('facts') or {}, engine.get('obligations') or {}
    root = 'shipping:' + str(order_id)
    base_keys = {root, root + ':base_shipping', 'shipping-prepaid:' + str(order_id), 'shipping-completed:' + str(order_id)}
    if any(key in facts for key in (root + ':base_shipping', 'completed_shipping:' + str(order_id),
                                    'prepaid_shipping:' + str(order_id), 'historical_base_fee:' + str(order_id))):
        return True
    frozen = facts.get(root) or {}
    component_mode = frozen.get('component_mode') is True or bool(facts.get('shipping_components:' + str(order_id)))
    if not component_mode and (frozen.get('cost') is not None or 'shipping_fee:' + str(order_id) in facts):
        return True
    return (any(row.get('kind') == 'shipping' and key in base_keys and Decimal(str(row.get('confirmed', 0))) != 0
                for key, row in checked_rows(obligations.items()))
            or any(a.get('obligation_id') in base_keys and Decimal(str(a.get('amount', 0))) != 0
                   for movement in checked_rows(state.get('movements', [])) for a in checked_rows(movement.get('allocations', []))))


def _base_keys(order_id):
    return {'shipping:' + order_id, 'shipping:' + order_id + ':base_shipping',
            'shipping-prepaid:' + order_id, 'shipping-completed:' + order_id}


def _obligation_order(key, row):
    for prefix in ('shipping-prepaid:', 'shipping-completed:', 'shipping:'):
        if key.startswith(prefix):
            identity = key[len(prefix):]
            component = row.get('fee_component') or row.get('component')
            if component and identity.endswith(':' + str(component)):
                return identity[:-(len(str(component)) + 1)]
            for suffix in (':base_shipping', ':cod_commission', ':cod_fixed_commission'):
                if identity.endswith(suffix):
                    return identity[:-len(suffix)]
            return identity
    return str(row['order_id']) if row.get('order_id') else None


class CompletionReviewIndex:
    """One explicit source/engine batch, never a process cache or database view."""
    def __init__(self, state):
        self.state = state
        engine = (state or {}).get('engine') or {}
        self.facts, self.obligations = engine.get('facts') or {}, engine.get('obligations') or {}
        self.base_orders, self.allocated, self.positive_allocated = set(), set(), set()
        self.fee_orders, self.movement_ids = set(), set()
        self.shipping_keys = defaultdict(set)
        self.base_keys = defaultdict(set)
        self.openings, self.payouts, self.payable_ids = defaultdict(list), defaultdict(list), defaultdict(set)
        self.linked, self.party_views = defaultdict(list), {}
        for key, row in self.obligations.items():
            preparation_checkpoint()
            party = tuple(row.get(k) for k in ('party_type', 'party_id', 'currency'))
            if row.get('direction') == 'payable':
                self.payable_ids[party].add(key)
            if row.get('kind') != 'shipping':
                continue
            oid = _obligation_order(key, row)
            if oid and key.startswith('shipping:'):
                self.shipping_keys[oid].add(key)
                if Decimal(str(row.get('confirmed', 0))) > 0:
                    self.fee_orders.add(oid)
            component = row.get('fee_component') or row.get('component')
            if component in {'cod_commission', 'cod_fixed_commission'} or any(
                    key.endswith(':' + item) for item in ('cod_commission', 'cod_fixed_commission')):
                continue
            if oid:
                self.base_keys[oid].add(key)
                if Decimal(str(row.get('confirmed', 0))) != 0:
                    self.base_orders.add(oid)
        for key, row in self.facts.items():
            preparation_checkpoint()
            if key.startswith(('completed_shipping:', 'prepaid_shipping:', 'historical_base_fee:')):
                self.base_orders.add(str(row.get('order_id') or key.split(':', 1)[1]))
            elif key.startswith('shipping:'):
                oid = key[len('shipping:'):]
                if oid.endswith(':base_shipping'):
                    self.base_orders.add(oid[:-len(':base_shipping')])
                elif row.get('cost') is not None:
                    if not row.get('component_mode') and not self.facts.get('shipping_components:' + oid):
                        self.base_orders.add(oid)
                for component in (':base_shipping', ':cod_commission', ':cod_fixed_commission'):
                    if oid.endswith(component):
                        self.fee_orders.add(oid[:-len(component)])
                        break
                else:
                    if row.get('cost') is not None:
                        self.fee_orders.add(oid)
            elif key.startswith('shipping_fee:'):
                oid = key[len('shipping_fee:'):]
                self.fee_orders.add(oid)
                if not (self.facts.get('shipping:' + oid) or {}).get('component_mode') and not self.facts.get('shipping_components:' + oid):
                    self.base_orders.add(oid)
        for row in state.get('openings', {}).values():
            preparation_checkpoint()
            party = tuple(row.get(k) for k in ('party_type', 'party_id', 'currency'))
            self.openings[party].append({k: row.get(k) for k in ('id', 'name', 'amount', 'currency', 'direction')})
            if row.get('direction') == 'for_party':
                self.payable_ids[party].add(row.get('id'))
        for row in state.get('movements', []):
            preparation_checkpoint()
            self.movement_ids.add(row.get('id'))
            self.allocated.update(a.get('obligation_id') for a in row.get('allocations', [])
                                  if Decimal(str(a.get('amount', 0))) != 0)
            self.positive_allocated.update(a.get('obligation_id') for a in row.get('allocations', [])
                                           if Decimal(str(a.get('amount', 0))) > 0)
            if row.get('direction') != 'outgoing':
                continue
            party = tuple(row.get(k) for k in ('party_type', 'party_id', 'currency'))
            self.payouts[party].append({k: deepcopy(row.get(k)) for k in ('id', 'amount', 'currency', 'kind', 'allocations')})
            if row.get('party_type') == 'courier':
                for field in ('order_id', 'order_number'):
                    if row.get(field):
                        self.linked[(field, str(row[field]))].append(row)

    def check_state(self, state):
        if state is not self.state:
            raise ValueError('operational_shipping_completion_review_snapshot_changed')

    def recorded(self, oid):
        return oid in self.base_orders or bool((self.base_keys[oid] | _base_keys(oid)) & self.allocated)

    def recorded_any_fee(self, oid):
        return oid in self.fee_orders or bool(self.shipping_keys[oid] & self.positive_allocated)

    def linked_payouts(self, event):
        return (*self.linked[('order_id', str(event['order_id']))],
                *self.linked[('order_number', str(event['order_number']))])

    def party_view(self, event):
        party = tuple(event.get(k) for k in ('party_type', 'party_id', 'currency'))
        if party not in self.party_views:
            openings = sorted(self.openings[party], key=lambda row: str(row['id']))
            payouts = sorted(self.payouts[party], key=lambda row: str(row['id']))
            invalid = False
            for payout in payouts:
                preparation_checkpoint()
                allocations = payout.get('allocations') or []
                invalid |= (payout.get('kind') != 'payment' or not allocations
                    or sum((Decimal(str(a.get('amount'))) for a in allocations), Decimal(0)) != Decimal(str(payout['amount']))
                    or any(a.get('obligation_id') not in self.payable_ids[party] for a in allocations))
            self.party_views[party] = (openings, payouts, digest([openings, payouts]), invalid)
        return self.party_views[party]

    def linked_base_payout(self, event):
        oid = str(event['order_id'])
        if (self.base_keys[oid] | _base_keys(oid)) & self.allocated:
            return True
        for movement in self.linked_payouts(event):
            preparation_checkpoint()
            allocations = movement.get('allocations') or []
            if not allocations or not all((self.obligations.get(a.get('obligation_id')) or {}).get('fee_component')
                    in {'cod_commission', 'cod_fixed_commission'} for a in allocations):
                return True
        return False

    def record(self, event, charge, movement=None):
        """Advance this batch after a successful write; never reuse an old approval basis."""
        oid = str(event['order_id'])
        self.base_orders.add(oid)
        self.base_keys[oid].add(charge['id'])
        party = tuple(charge.get(k) for k in ('party_type', 'party_id', 'currency'))
        self.payable_ids[party].add(charge['id'])
        if movement is None:
            return
        self.movement_ids.add(movement['id'])
        self.allocated.add(charge['id'])
        self.positive_allocated.add(charge['id'])
        self.payouts[party].append({k: deepcopy(movement.get(k)) for k in ('id', 'amount', 'currency', 'kind', 'allocations')})
        for field in ('order_id', 'order_number'):
            if movement.get(field):
                self.linked[(field, str(movement[field]))].append(movement)
        self.party_views.pop(party, None)


def build_review_index(state):
    return CompletionReviewIndex(state)


class CompletionReviewRequired(ValueError):
    def __init__(self, event, review):
        super().__init__(HISTORY_REVIEW)
        self.candidate = deepcopy(event)
        self.review = deepcopy(review)


def initialize_admission(state, as_of):
    """Called only by reconciliation; never repair an invalid saved marker."""
    if state.get('status') != 'active' or not state.get('owner_id'):
        return
    start, at = _instant(state['started_at']), _instant(as_of)
    if at < start:
        raise ValueError('operational_shipping_completion_activation_invalid')
    engine = state.setdefault('engine', {})
    if MARKER not in engine:
        engine[MARKER] = {'version': 1, 'owner_id': state['owner_id'],
                          'started_at': start.isoformat(), 'activated_at': at.isoformat()}


def needs_review(state, event):
    if event.get('completion_admission_required') is not True:
        return False
    marker = state.get('engine', {}).get(MARKER)
    try:
        return not (isinstance(marker, dict) and type(marker.get('version')) is int and marker['version'] == 1
                    and marker.get('owner_id') == state.get('owner_id') == event.get('owner_id')
                    and _instant(marker.get('started_at')) == _instant(state['started_at'])
                    <= _instant(marker.get('activated_at')) < _instant(event['completed_at']))
    except (ValueError, TypeError, KeyError):
        return True


def review(state, event, *, review_index=None):
    """Owner preview data only: no bank, receipt, or customer identifiers."""
    if not event or event.get('owner_id') != state.get('owner_id'):
        raise ValueError('operational_shipping_completion_owner_mismatch')
    index = review_index if review_index is not None else build_review_index(state)
    index.check_state(state)
    openings, payouts, opening_payout_hash, invalid_payouts = index.party_view(event)
    issues = []
    if index.recorded(str(event['order_id'])):
        issues.append('operational_shipping_completion_existing_fee')
    if index.linked_base_payout(event):
        issues.append('operational_shipping_completion_existing_payout')
    if invalid_payouts:
        issues.append('operational_shipping_completion_unallocated_payout')
    result = {'candidate_hash': digest(event), 'opening_payout_hash': opening_payout_hash,
              'openings': openings, 'payouts': payouts, 'issues': sorted(set(issues)),
              'requires_review': needs_review(state, event)}
    result['preview_hash'] = digest([state.get('owner_id'), state.get('started_at'),
        {key: value for key, value in result.items() if key not in {'openings', 'payouts'}}])
    return result


def approval_record(state, event, *, actor, approved_at, evidence_ref, expected_preview_hash):
    """Build a record after an authenticated owner/CAS service re-reads sources."""
    check = review(state, event)
    if (actor != state.get('owner_id') or check['issues'] or expected_preview_hash != check['preview_hash']
            or not isinstance(evidence_ref, str) or len(evidence_ref.strip()) < 3
            or _instant(approved_at) < _instant(event['completed_at'])):
        raise ValueError('operational_shipping_completion_review_invalid')
    return {'candidate': deepcopy(event), 'candidate_hash': check['candidate_hash'],
            'opening_payout_hash': check['opening_payout_hash'], 'approved_by': actor,
            'approved_at': _instant(approved_at).isoformat(), 'evidence_ref': evidence_ref.strip(),
            'started_at': _instant(state['started_at']).isoformat()}


def require_admission(state, event, *, review_index=None):
    if not needs_review(state, event):
        return
    saved = state.get(APPROVALS, {}).get(digest(str(event['order_number']))) or {}
    # The source collector needs a held candidate, not thousands of repeated
    # copies of the same party's entire payout history. Owner preview explicitly
    # calls review; only an existing approval needs this validation during a scan.
    if not saved:
        raise CompletionReviewRequired(event, {'requires_review': True, 'issues': []})
    check = review(state, event, review_index=review_index)
    try:
        approved = (saved.get('approved_by') == state.get('owner_id') and bool(saved.get('evidence_ref'))
                    and _instant(saved.get('started_at')) == _instant(state['started_at'])
                    and _instant(saved.get('approved_at')) >= _instant(event['completed_at'])
                    and saved.get('candidate_hash') == check['candidate_hash']
                    and saved.get('candidate') == event
                    and saved.get('opening_payout_hash') == check['opening_payout_hash'] and not check['issues'])
    except (ValueError, TypeError, KeyError):
        approved = False
    if not approved:
        raise CompletionReviewRequired(event, {key: value for key, value in check.items() if key not in {'openings', 'payouts'}})

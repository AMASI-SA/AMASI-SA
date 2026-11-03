"""Compare one saved operational position with one candidate reconciliation.

From the candidate checkout run
    PYTHONPATH=backend python -B scripts/preview_operational_financial_position.py --owner OWNER_ID
Set MEZAN_PREVIEW_READONLY_MONGO_URI to a credential with only the built-in Mongo read role on that database and
MEZAN_PREVIEW_DB_NAME to the intended database. No application env is loaded.
Output is aggregate financial data for the operator, never a posting input.
"""
import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
import os
import re
import sys

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import timeout
from pymongo.read_concern import ReadConcern

from operational_balance_engine import reconcile
from operational_balance_inventory import assert_no_source_collision
from operational_balance_service import active_gate
from operational_balance_sources import collect_sources, instant
from operational_balance_store import digest, now, read
from operational_customer_returns import attach_returns
from operational_financial_position import build_financial_position
from operational_open_cost_exposure import project
from operational_supplier_invoice_preview import SnapshotReads

SECONDS = 40
PUBLIC_PARTIES = {'smsa', 'imile', 'salla', 'tamara', 'tabby', 'emkan'}
BALANCE_TABLES = ('couriers', 'drivers', 'providers')


def party_label(owner, key):
    # No party names or order, account, user or employee IDs go to stdout.
    return key if key in PUBLIC_PARTIES else 'party-' + digest([owner, key])[:16]


def known_amount(rows):
    values = [Decimal(r['amount']) for r in rows if r['amount'] is not None]
    return format(sum(values), '.2f') if values else None


def summary(owner, state, exposure):
    output = {}
    for currency, data in build_financial_position(state, cost_exposure=exposure)['currencies'].items():
        if not re.fullmatch('[A-Z]{3}', currency):
            raise ValueError('invalid_currency')
        tables = {t['id']: t for t in data['tables']}
        balances = {}
        for table_id in BALANCE_TABLES:
            table = tables[table_id]
            parties = {}
            for row in table['rows']:
                key = row['id'].split(':', 1)[1].rsplit(':', 1)[0]
                parties[party_label(owner, key)] = {k: row[k] for k in ('receivable', 'payable')}
            balances[table_id] = {'complete': table['complete'], 'parties': parties}
        forecast = {}
        for table_id in ('cod_expected', 'provider_fees_expected'):
            table = tables[table_id]
            forecast[table_id] = {
                'complete': table['complete'], 'reported_total': table['totals']['amount'],
                'known_subtotal': known_amount(table['rows']),
                'orders_with_amount': sum(r['orders'] for r in table['rows'] if r['amount'] is not None),
                'rows_without_amount': sum(r['amount'] is None for r in table['rows']),
            }
        # Fee rows refer to source obligations; aggregate per platform without
        # exporting those order keys or duplicating the receivable.
        fees = {}
        obligations = state.get('engine', {}).get('obligations', {})
        for r in tables['provider_fees_expected']['rows']:
            party = party_label(owner, obligations[r['id']]['party_id'])
            fees.setdefault(party, []).append(r)
        forecast['provider_fees_expected']['known_by_platform'] = {
            party: known_amount(rows) for party, rows in fees.items()}
        recorded_fees = {}
        for movement in state['movements']:
            if (movement.get('party_type'), movement.get('direction'), movement.get('kind'),
                    movement.get('currency')) != ('provider', 'incoming', 'settlement', currency):
                continue
            party = party_label(owner, movement.get('party_id'))
            recorded_fees.setdefault(party, []).append({'amount': movement.get('actual_fee_amount')})
        output[currency] = {**balances, **forecast, 'actual_fees_on_recorded_provider_settlements': {
            party: {'known_subtotal': known_amount(rows),
                    'settlements_without_fee_amount': sum(r['amount'] is None for r in rows)}
            for party, rows in recorded_fees.items()}}
    return output


def bank_candidates(orders, recorded):
    """Model the worker's pre-claim identity and amount checks without claiming a movement."""
    by_request, by_order = {}, {}
    def index(movement, position):
        by_request.setdefault(movement.get('request_id'), set()).add(position)
        if movement.get('direction') == 'incoming':
            by_order.setdefault(movement.get('order_number'), set()).add(position)
    rows = list(recorded)
    for position, movement in enumerate(rows):
        index(movement, position)
    counts = {'prospective_new': 0, 'already_recorded': 0, 'repeated_candidate': 0,
              'conflicting': 0, 'unknown': 0}
    for order in orders:
        credit = order.get('bank_credit')
        if not credit:
            continue
        try:
            request = 'order-bank:' + str(order['id'])
            number, account, amount = order['order_number'], credit['bank_id'], Decimal(str(credit['amount']))
            if not all((order['id'], number, account)) or not amount.is_finite() or amount <= 0:
                raise ValueError
            hits = by_request.get(request, set()) | by_order.get(number, set())
            if not hits:
                m = {'request_id': request, 'order_number': number, 'direction': 'incoming',
                     'amount': amount, 'bank_id': account}
                index(m, len(rows))
                rows.append(m)
                counts['prospective_new'] += 1
            elif len(hits) > 1:
                counts['conflicting'] += 1
            else:
                pos = next(iter(hits))
                if rows[pos].get('bank_id') != account or Decimal(str(rows[pos]['amount'])) != amount:
                    counts['conflicting'] += 1
                else:
                    counts['already_recorded' if pos < len(recorded) else 'repeated_candidate'] += 1
        except (KeyError, TypeError, ValueError, InvalidOperation):
            counts['unknown'] += 1
    return counts


async def compare(scoped, owner, clock):
    state = await read(scoped, owner)
    await active_gate(scoped, owner, state)
    saved_at = (state.get('engine') or {}).get('as_of')
    if not saved_at or instant(clock) < instant(saved_at):
        raise ValueError('saved_cutoff_required')
    await assert_no_source_collision(scoped, owner, state)
    sources = await collect_sources(scoped, owner, state['started_at'], clock,
                                    baselines=state.get('source_baselines'), operational_state=state)
    sources.setdefault('supplier_returns', []).extend(state.get('supplier_returns', []))
    attach_returns(state, sources)
    proposed = reconcile(state, sources, clock)
    proposed['engine']['as_of'] = clock
    if any(state.get(k) != proposed.get(k) for k in ('openings', 'movements')):
        raise ValueError('unexpected_movement_or_opening_change')
    # Current report GET also recomputes cost exposure. Its actual balances
    # are persisted, but its forecasts are not kept as historical snapshots.
    saved_exposure = await project(scoped, owner, state, as_of=saved_at)
    next_exposure = await project(scoped, owner, proposed, as_of=clock)
    return {'schema': 1, 'posting_allowed': False, 'saved_revision': state['revision'],
            'saved_as_of': instant(saved_at).isoformat(), 'proposed_as_of': instant(clock).isoformat(),
            'basis': 'saved_booked_balances_and_forecasts_recomputed_with_candidate_code',
            'saved': summary(owner, state, saved_exposure),
            'proposed': summary(owner, proposed, next_exposure),
            'bank_movements_simulated': False,
            'bank_preclaim_candidates': bank_candidates(sources.get('orders', []), state['movements']),
            'openings_and_recorded_movements_equal': True}


async def snapshot(db, owner, clock=None):
    if not isinstance(owner, str) or not owner.strip() or owner != owner.strip():
        raise ValueError('owner_required')
    async def run():
        async with await db.client.start_session() as session:
            async with session.start_transaction(read_concern=ReadConcern('snapshot')):
                return await compare(SnapshotReads(db, owner, session), owner, clock or now())
    # Bound both server reads and the wall clock. No fallback to inconsistent or
    # partial results on timeout, transaction expiry or an unsupported topology.
    with timeout(SECONDS):
        return await asyncio.wait_for(run(), timeout=SECONDS)


async def require_read_role(client, name):
    # The server already returns inherited roles. Fail closed for broader or
    # custom credentials rather than trusting a variable labelled READONLY.
    info = (await client.admin.command('connectionStatus'))['authInfo']
    roles = info.get('authenticatedUserRoles') or []
    if (not info.get('authenticatedUsers') or not roles
            or any(r.get('role') != 'read' or r.get('db') != name for r in roles)):
        raise ValueError('dedicated_read_role_required')


async def main(owner):
    uri, name = os.environ.get('MEZAN_PREVIEW_READONLY_MONGO_URI'), os.environ.get('MEZAN_PREVIEW_DB_NAME')
    if not uri or not name:
        print('Missing read-only preview configuration.', file=sys.stderr)
        return 2
    client = None
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, appname='mezan-position-preview')
        with timeout(SECONDS):
            await require_read_role(client, name)
            result = await snapshot(client[name], owner)
    except Exception as exc:
        # Mongo errors can contain connection details, values, or query fragments.
        print(f'Preview unavailable ({type(exc).__name__}). No figures emitted.', file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--owner', required=True)
    sys.exit(asyncio.run(main(parser.parse_args().owner)))

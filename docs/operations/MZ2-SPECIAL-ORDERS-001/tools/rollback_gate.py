#!/usr/bin/env python3
"""Offline evidence check only. Never connects, pauses, deploys, restores or approves.

A PASS checks supplied facts; it cannot authenticate the collector/reviewer. The
operator must verify evidence provenance independently. Missing facts fail closed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

SURFACES = (
    'orders', 'items', 'workflows', 'preparation_pieces', 'supplier_invoices',
    'inventory', 'shipments', 'driver_assignments', 'collections', 'settlements',
    'general_ledger', 'private_evidence', 'salla_sidecars', 'idempotency', 'outbox',
    'write_control', 'write_control_audit',
)
CONTROLS = (
    'creation_paused', 'workflow_writes_quiesced', 'financial_writes_quiesced',
    'all_writers_acknowledged_epoch', 'external_effects_reconciled', 'release_guard_idle',
    'evidence_writes_quiesced', 'configuration_writes_quiesced', 'dispatch_writes_quiesced',
)
DRILLS = ('ordinary_regression', 'special_data_compatibility', 'backup_restore', 'rollback')
MAX_AGE_SECONDS = 900


def strict_json(raw: str):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError('duplicate_json_key')
            out[k] = v
        return out
    def bad_constant(_):
        raise ValueError('nonfinite_json_number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=bad_constant)


def evaluate(record: dict, *, now: datetime | None = None) -> dict:
    """Evaluate preflight or postflight facts without any external side effects."""
    errors = []
    now = now or datetime.now(timezone.utc)
    def need(condition, code):
        if not condition:
            errors.append(code)
    def obj(value):
        return value if isinstance(value, dict) else {}
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    def integer(value, minimum=0):
        return type(value) is int and value >= minimum
    def sha(value, size):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{'+str(size)+r'}', value) is not None
    def identity(value, prefix):
        value = obj(value)
        for name in ('source_sha', 'deployment_sha', 'tree_sha'):
            need(sha(value.get(name), 40), prefix+'.'+name)
        rid = value.get('release_id')
        need(isinstance(rid, str) and re.fullmatch(r'rg5-[0-9a-f]{64}', rid) is not None, prefix+'.release_id')
        return value
    def snapshot(value, prefix):
        value = obj(value)
        need(value.get('consistent') is True, prefix+'.consistent_snapshot_required')
        need(value.get('inflight_mutations') == 0 and type(value.get('inflight_mutations')) is int, prefix+'.writers_not_quiescent')
        need(integer(value.get('write_epoch')), prefix+'.write_epoch')
        try:
            when = datetime.fromisoformat(value['captured_at'])
            age = (now-when).total_seconds()
            need(when.utcoffset() is not None and 0 <= age <= MAX_AGE_SECONDS, prefix+'.stale_or_future')
        except (KeyError, TypeError, ValueError):
            errors.append(prefix+'.timestamp_required')
        need(nonempty(value.get('collector_evidence_ref')), prefix+'.collector_evidence_required')
        need(value.get('inventory_complete') is True, prefix+'.complete_inventory_required')
        surfaces = obj(value.get('surfaces'))
        for key in SURFACES:
            row = obj(surfaces.get(key))
            need(integer(row.get('rows')), prefix+'.'+key+'.row_count')
            special = row.get('special_rows')
            need(integer(special) and integer(row.get('rows')) and special <= row['rows'], prefix+'.'+key+'.special_count')
            for field in ('identities_sha256', 'content_sha256'):
                need(sha(row.get(field), 64), prefix+'.'+key+'.'+field)
        balances = value.get('balances_sar_minor')
        need(isinstance(balances, dict) and bool(balances) and all(nonempty(k) and type(v) is int for k,v in obj(balances).items()), prefix+'.exact_account_balances_required')
        debit, credit = value.get('debits_sar_minor'), value.get('credits_sar_minor')
        need(integer(debit) and integer(credit) and debit == credit, prefix+'.trial_balance_unbalanced')
        return value

    r = obj(record)
    need(r.get('schema_version') == 1 and type(r.get('schema_version')) is int, 'schema_version')
    phase = r.get('phase')
    need(phase in ('preflight', 'postflight'), 'phase')
    need(r.get('database_restore_requested') is False, 'database_restore_is_separate_disaster_recovery')
    need(r.get('evidence_class') == 'operator_collected', 'template_or_synthetic_is_not_operator_evidence')
    need(nonempty(r.get('environment_id')), 'environment_id')
    current = identity(r.get('current_release'), 'current')
    target = identity(r.get('target_release'), 'target')
    controls = obj(r.get('controls'))
    for field in CONTROLS:
        need(controls.get(field) is True, 'control.'+field)
    need(integer(controls.get('write_epoch')), 'control.write_epoch')
    approval = obj(r.get('approval'))
    need(approval.get('action') == 'rollback' and nonempty(approval.get('owner_approval_ref')), 'explicit_owner_rollback_approval_required')
    need(approval.get('target_deployment_sha') == target.get('deployment_sha') and sha(approval.get('target_deployment_sha'),40), 'approval_target_mismatch')
    need(approval.get('environment_id') == r.get('environment_id') and nonempty(r.get('environment_id')), 'approval_environment_mismatch')
    before = snapshot(r.get('before'), 'before')
    need(before.get('environment_id') == r.get('environment_id'), 'before.environment_mismatch')
    need(before.get('write_epoch') == controls.get('write_epoch'), 'before.epoch_mismatch')
    for field in ('verified','critical_hashes_match','frontend_verified'):
        need(obj(before.get('runtime')).get(field) is True, 'before.runtime_'+field)
    for field in ('source_sha','deployment_sha','tree_sha','release_id'):
        need(obj(before.get('runtime')).get(field) == current.get(field), 'before.runtime_'+field)
    schemas = before.get('special_schema_versions')
    supported = target.get('supported_special_schemas')
    for value,name in ((schemas,'observed_schema_versions'),(supported,'target_schema_versions')):
        need(isinstance(value,list) and all(integer(v,1) for v in value) and len(value)==len(set(value)), name)
    need(target.get('reads_special_data') in (True,False) and type(target.get('reads_special_data')) is bool, 'target.special_read_capability_unknown')
    special = any(integer(obj(obj(before.get('surfaces')).get(s)).get('special_rows'),1) for s in SURFACES)
    if special:
        need(target.get('reads_special_data') is True, 'legacy_target_forbidden_after_any_special_records')
        need(isinstance(schemas,list) and bool(schemas), 'special_schema_inventory_required')
        need(isinstance(schemas,list) and isinstance(supported,list) and all(v in supported for v in schemas), 'special_schema_downgrade_forbidden')
    required, clients = r.get('required_client_contracts'), target.get('supported_client_contracts')
    need(isinstance(required,list) and bool(required) and all(nonempty(v) for v in required), 'required_clients_unknown')
    need(isinstance(clients,list) and isinstance(required,list) and all(v in clients for v in required), 'client_contract_downgrade_forbidden')
    drill = obj(r.get('drill'))
    need(drill.get('current_source_sha') == current.get('source_sha') and drill.get('target_tree_sha') == target.get('tree_sha'), 'drill_not_bound_to_exact_source_and_target')
    need(nonempty(drill.get('reviewer_ref')) and sha(drill.get('evidence_sha256'),64), 'independent_drill_evidence_required')
    for field in DRILLS:
        need(drill.get(field) == 'pass', 'drill.'+field)
    if phase == 'postflight':
        after = snapshot(r.get('after'), 'after')
        for field in ('environment_id','write_epoch','surfaces','balances_sar_minor','debits_sar_minor','credits_sar_minor','special_schema_versions'):
            need(after.get(field) == before.get(field), 'postflight.changed_'+field)
        try:
            need(datetime.fromisoformat(after['captured_at']) >= datetime.fromisoformat(before['captured_at']), 'postflight.time_order')
        except (KeyError,ValueError,TypeError):
            errors.append('postflight.timestamps_required')
        runtime = obj(after.get('runtime'))
        for field in ('source_sha','deployment_sha','tree_sha','release_id'):
            need(runtime.get(field) == target.get(field), 'postflight.target_'+field)
        for field in ('verified','critical_hashes_match','frontend_verified'):
            need(runtime.get(field) is True, 'postflight.runtime_'+field)
    return {'decision':'BLOCKED' if errors else phase.upper()+'_EVIDENCE_PASS_NOT_AUTHORIZATION',
            'blockers':sorted(set(errors)), 'executes_changes':False, 'deployment_authorized':False,
            'note':'Supplied evidence must be authenticated independently. No automatic resume or rollback.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args=parser.parse_args()
    try:
        if args.evidence.is_symlink() or args.evidence.stat().st_size > 5_000_000:
            raise ValueError('invalid_evidence_file')
        result=evaluate(strict_json(args.evidence.read_text(encoding='utf-8')))
    except (OSError,ValueError,TypeError,KeyError):
        result={'decision':'BLOCKED','blockers':['invalid_evidence_file'],
                'executes_changes':False,'deployment_authorized':False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result['decision']=='BLOCKED' else 0

if __name__=='__main__':
    raise SystemExit(main())

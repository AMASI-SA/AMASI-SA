"""Synthetic evidence only; not a real restoration/redeployment rehearsal."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('rollback_gate',Path(__file__).with_name('rollback_gate.py'))
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
NOW=datetime(2026,9,28,16,0,tzinfo=timezone.utc)


def fixture(phase='preflight'):
    current={k:'a'*40 for k in ('source_sha','deployment_sha','tree_sha')}
    current['release_id']='rg5-'+'a'*64
    target={k:'b'*40 for k in ('source_sha','deployment_sha','tree_sha')}
    target.update(release_id='rg5-'+'b'*64,reads_special_data=True,supported_special_schemas=[1],
                  supported_client_contracts=['web-1','employee-1','courier-1'])
    snapshot={'environment_id':'synthetic-db-only','captured_at':NOW.isoformat(),'consistent':True,
              'inflight_mutations':0,'write_epoch':7,'inventory_complete':True,
              'collector_evidence_ref':'synthetic-unit-fixture','special_schema_versions':[1],
              'balances_sar_minor':{'bank':4000,'supplier':-2100,'driver_cod':2000},
              'debits_sar_minor':8100,'credits_sar_minor':8100,
              'runtime':{**current,'verified':True,'critical_hashes_match':True,'frontend_verified':True},
              'surfaces':{k:{'rows':3,'special_rows':1,'identities_sha256':'c'*64,'content_sha256':'d'*64} for k in gate.SURFACES}}
    after=deepcopy(snapshot)
    after['runtime']={**target,'verified':True,'critical_hashes_match':True,'frontend_verified':True}
    return {'schema_version':1,'phase':phase,'evidence_class':'operator_collected',
            'environment_id':'synthetic-db-only','database_restore_requested':False,
            'current_release':current,'target_release':target,'before':snapshot,'after':after,
            'controls':{**{k:True for k in gate.CONTROLS},'write_epoch':7},
            'required_client_contracts':['web-1','employee-1','courier-1'],
            'approval':{'action':'rollback','owner_approval_ref':'synthetic-not-real-approval',
                        'target_deployment_sha':target['deployment_sha'],'environment_id':'synthetic-db-only'},
            'drill':{**{k:'pass' for k in gate.DRILLS},'current_source_sha':current['source_sha'],
                     'target_tree_sha':target['tree_sha'],'reviewer_ref':'synthetic-reviewer','evidence_sha256':'e'*64}}


class RecoveryGateTests(unittest.TestCase):
    def result(self,data): return gate.evaluate(data,now=NOW)
    def blocked(self,data): self.assertEqual(self.result(data)['decision'],'BLOCKED')
    def test_preflight_valid_shape_never_authorizes_execution(self):
        result=self.result(fixture());self.assertNotEqual(result['decision'],'BLOCKED')
        self.assertIs(result['deployment_authorized'],False);self.assertIs(result['executes_changes'],False)
    def test_postflight_conservation(self): self.assertNotEqual(self.result(fixture('postflight'))['decision'],'BLOCKED')
    def test_empty_record(self): self.blocked({})
    def test_synthetic_labeled_record_not_evidence(self):
        d=fixture();d['evidence_class']='synthetic';self.blocked(d)
    def test_legacy_forbidden_with_even_closed_special_data(self):
        d=fixture();d['target_release']['reads_special_data']=False;self.blocked(d)
    def test_legacy_only_before_any_special_traces(self):
        d=fixture();d['target_release'].update(reads_special_data=False,supported_special_schemas=[])
        for s in d['before']['surfaces'].values():s['special_rows']=0
        d['before']['special_schema_versions']=[]
        self.assertNotEqual(self.result(d)['decision'],'BLOCKED')
    def test_non_order_special_evidence_blocks_legacy(self):
        d=fixture();d['target_release']['reads_special_data']=False
        for s in d['before']['surfaces'].values():s['special_rows']=0
        d['before']['surfaces']['private_evidence']['special_rows']=1;self.blocked(d)
    def test_database_restore_is_not_code_rollback(self):
        d=fixture();d['database_restore_requested']=True;self.blocked(d)
    def test_equal_count_different_ledger_bytes(self):
        d=fixture('postflight');d['after']['surfaces']['general_ledger']['content_sha256']='f'*64;self.blocked(d)
    def test_equal_balance_missing_order_identity(self):
        d=fixture('postflight');d['after']['surfaces']['orders']['identities_sha256']='f'*64;self.blocked(d)
    def test_account_balance_changed_one_halalah(self):
        d=fixture('postflight');d['after']['balances_sar_minor']['bank']+=1;self.blocked(d)
    def test_unbalanced_ledger(self):
        d=fixture();d['before']['credits_sar_minor']+=1;self.blocked(d)
    def test_float_money(self):
        d=fixture();d['before']['balances_sar_minor']['bank']=40.0;self.blocked(d)
    def test_bool_is_not_integer(self):
        d=fixture();d['before']['inflight_mutations']=False;self.blocked(d)
    def test_partial_inventory(self):
        d=fixture();d['before']['inventory_complete']=False;self.blocked(d)
    def test_pause_epoch_mismatch(self):
        d=fixture();d['controls']['write_epoch']+=1;self.blocked(d)
    def test_unknown_schema(self):
        d=fixture();d['before']['special_schema_versions']=[2];self.blocked(d)
    def test_unknown_client(self):
        d=fixture();d['required_client_contracts'].append('old-courier-0');self.blocked(d)
    def test_missing_independent_drill(self):
        d=fixture();d.pop('drill');self.blocked(d)
    def test_other_target_drill(self):
        d=fixture();d['drill']['target_tree_sha']='f'*40;self.blocked(d)
    def test_wrong_environment_approval(self):
        d=fixture();d['approval']['environment_id']='other-db';self.blocked(d)
    def test_wrong_target_approval(self):
        d=fixture();d['approval']['target_deployment_sha']='a'*40;self.blocked(d)
    def test_stale_snapshot(self):
        d=fixture();d['before']['captured_at']=(NOW-timedelta(seconds=901)).isoformat();self.blocked(d)
    def test_future_snapshot(self):
        d=fixture();d['before']['captured_at']=(NOW+timedelta(seconds=1)).isoformat();self.blocked(d)
    def test_current_runtime_not_verified(self):
        d=fixture();d['before']['runtime']['verified']=False;self.blocked(d)
    def test_target_runtime_not_verified(self):
        d=fixture('postflight');d['after']['runtime']['verified']=False;self.blocked(d)
    def test_wrong_target_runtime(self):
        d=fixture('postflight');d['after']['runtime']['source_sha']='a'*40;self.blocked(d)
    def test_duplicate_json(self):
        with self.assertRaises(ValueError):gate.strict_json('{"x":1,"x":2}')
    def test_nonfinite_json(self):
        with self.assertRaises(ValueError):gate.strict_json('{"x":NaN}')


def missing_control(field):
    def test(self):
        d=fixture();d['controls'].pop(field);self.blocked(d)
    return test
for key in gate.CONTROLS:setattr(RecoveryGateTests,'test_missing_'+key,missing_control(key))


def missing_surface(field):
    def test(self):
        d=fixture();d['before']['surfaces'].pop(field);self.blocked(d)
    return test
for key in gate.SURFACES:setattr(RecoveryGateTests,'test_missing_surface_'+key,missing_surface(key))

if __name__=='__main__':unittest.main()

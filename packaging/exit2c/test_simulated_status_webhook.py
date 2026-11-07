import unittest
from salla_http_simulator import Fixture, ORDER_IDS, STATUS
from simulated_status_webhook import confirmed_event, require_ingestion, signed_body


class ConfirmedEventTests(unittest.TestCase):
    def test_lifecycle_refuses_delivery_in_denied_scenarios_before_any_io(self):
        from preparation_lifecycle_acceptance import Lifecycle
        for scenario in ('deny','unavailable'):
            lifecycle=Lifecycle.__new__(Lifecycle)
            lifecycle.state={'provider_scenario':scenario}
            with self.assertRaises(AssertionError):lifecycle.deliver_status_event('EXIT2D-1001')

    def test_delivery_is_in_success_branch_after_review_confirmation(self):
        import ast
        from pathlib import Path
        import preparation_lifecycle_acceptance as prep
        tree=ast.parse(Path(prep.__file__).read_text(encoding='utf-8'))
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
                    ast.unparse(n.test)=="self.state['provider_scenario'] != 'success'")
        require_call=lambda nodes: any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and
                                       n.func.attr=='deliver_status_event' for node in nodes for n in ast.walk(node))
        self.assertFalse(require_call(branch.body))
        self.assertTrue(require_call(branch.orelse))
        self.assertTrue(isinstance(branch.orelse[0],ast.With) and
                        ast.unparse(branch.orelse[0].items[0].context_expr)=="check('REVIEW_COMPLETE')")

    def test_http200_synced_false_and_swallowed_errors_are_rejected(self):
        for result in ({'ok':True,'capture':{'order_sync':{'synced':False}}},
                       {'capture':{'order_sync':{'synced':True,'attribution_ledger':{'synced':True},
                                                'auto_fulfillment':{'error':'sensitive-marker'}},
                                   'snapchat_capi':{'reason':'capi_disabled'}}}):
            with self.assertRaisesRegex(ValueError,'^WEBHOOK_(NOT_SYNCED|ANCILLARY_FAILURE)$'):
                require_ingestion(result)

    def test_control_endpoint_is_exact_authenticated_and_not_a_provider_call(self):
        for mode in ('success','deny','unavailable'):
            f=Fixture('local-event-fixture-token-0000000000',mode)
            key=next(iter(ORDER_IDS));path='/__fixture__/status-event/'+key
            self.assertEqual(f.respond('GET',path,'Bearer '+f.token,None)[0],409)
            code,_=f.respond('POST','/admin/v2/orders/'+key+'/status','Bearer '+f.token,{'status_id':71})
            before=f.counts.copy()
            response=f.respond('GET',path,'Bearer '+f.token,None)
            self.assertEqual(response[0],200 if mode=='success' else 409)
            self.assertTrue(f.counts==before,'CONTROL_COUNTED_AS_PROVIDER')
            self.assertEqual(f.respond('GET',path,'invalid',None)[0],403)
            self.assertEqual(f.respond('POST',path,'Bearer '+f.token,None)[0],422)
            self.assertEqual(f.respond('GET',path+'?status=72','Bearer '+f.token,None)[0],422)

    def test_new_check_ids_reach_controller_and_parser_without_sensitive_output(self):
        from test_resume_diagnostics import controller,shell
        from acceptance_controller import check
        for identifier in ('WEBHOOK_EVENT_SOURCE','WEBHOOK_DELIVERY','WEBHOOK_INGESTION','WEBHOOK_INTERNAL_EFFECTS'):
            def action(state):
                state['owner_cookie']='sensitive-marker'
                with check(identifier):raise ValueError('sensitive-marker')
            code,reply=controller('prep-review',action)
            parsed=shell('prep-review',reply)
            self.assertTrue(code==parsed.returncode==1 and identifier in parsed.stdout,'CHECK_ID_MISSING')
            self.assertTrue('sensitive-marker' not in reply+parsed.stdout+parsed.stderr,'OUTPUT_LEAK')

    def test_current_runtime_guard_accepts_test_webhook_secret_without_change(self):
        import importlib.util
        import os
        from unittest.mock import patch
        from source_paths import backend_root
        spec=importlib.util.spec_from_file_location('webhook_guard_test',backend_root()/'independent_runtime.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        env={**runtime.SYNTHETIC,'MEZAN_ACCEPTANCE_PROFILE':runtime.SALLA_SIMULATOR_PROFILE,
             'SALLA_TOKEN_ENC_KEY':runtime.SALLA_SIMULATOR_KEY,
             'SALLA_API_BASE':'http://127.0.0.1:8093/admin/v2','SALLA_AUTH_BASE':'http://127.0.0.1:8093',
             'SALLA_WEBHOOK_SECRET':'local-webhook-secret-fixture-00000000',
             'MEZAN_SNAPCHAT_CAPI_ENABLED':'false'}
        # Unit-only Linux/namespace simulation; no server import and no Linux claim.
        with patch.dict(os.environ,env,clear=True),patch.object(runtime.sys,'platform','linux'), \
             patch.object(runtime.socket,'if_nameindex',return_value=[(1,'lo')]):
            runtime.validate_before_import('web')

    def test_unknown_identity_is_rejected_without_echo(self):
        f=Fixture('local-event-fixture-token-0000000000','success')
        for internal_id,merchant in [('sensitive-test-marker','exit2d-webhook-owner-store'),
                                     (next(iter(ORDER_IDS)),'sensitive-test-marker')]:
            with self.assertRaisesRegex(ValueError,'^EVENT_IDENTITY_REJECTED$'):
                confirmed_event(f,internal_id,merchant)

    def test_event_mutation_does_not_change_confirmed_provider_state(self):
        f=Fixture('local-event-fixture-token-0000000000','success')
        internal_id=next(iter(ORDER_IDS))
        f.respond('POST','/admin/v2/orders/'+internal_id+'/status','Bearer '+f.token,{'status_id':71})
        event=confirmed_event(f,internal_id)
        event['data']['status']['slug']='modified-copy'
        self.assertTrue(confirmed_event(f,internal_id)['data']['status']==STATUS[71],'CONFIRMED_STATE_MUTATED')

    def test_no_event_without_confirmation_in_each_scenario(self):
        for mode in ('success','deny','unavailable'):
            f=Fixture('local-event-fixture-token-0000000000',mode)
            for internal_id in ORDER_IDS:
                with self.assertRaisesRegex(ValueError,'^EVENT_NOT_CONFIRMED$'):
                    confirmed_event(f,internal_id)
                code,_=f.respond('POST','/admin/v2/orders/'+internal_id+'/status',
                    'Bearer '+f.token,{'status_id':71})
                if mode!='success':
                    self.assertIn(code,(403,503))
                    with self.assertRaisesRegex(ValueError,'^EVENT_NOT_CONFIRMED$'):
                        confirmed_event(f,internal_id)

    def test_event_uses_latest_confirmed_status_and_keeps_identity(self):
        f=Fixture('local-event-fixture-token-0000000000','success')
        for internal_id in ORDER_IDS:
            previous=None
            for status_id in (71,72):
                code,_=f.respond('POST','/admin/v2/orders/'+internal_id+'/status',
                    'Bearer '+f.token,{'status_id':status_id})
                self.assertEqual(code,200)
                event=confirmed_event(f,internal_id)
                self.assertTrue(event['data']['status']==STATUS[status_id],'EVENT_STATUS_MISMATCH')
                self.assertTrue(str(event['data']['id'])==internal_id,'EVENT_ORDER_MISMATCH')
                if previous:
                    self.assertTrue(previous['data']['items']==event['data']['items'],'EVENT_ITEMS_CHANGED')
                previous=event
            # Refused regression must not manufacture the requested state.
            code,_=f.respond('POST','/admin/v2/orders/'+internal_id+'/status',
                'Bearer '+f.token,{'status_id':71})
            self.assertEqual(code,422)
            self.assertTrue(confirmed_event(f,internal_id)['data']['status']==STATUS[72],'REFUSED_STATE_EMITTED')


if __name__=='__main__':unittest.main()

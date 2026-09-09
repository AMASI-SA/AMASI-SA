import unittest
from salla_http_simulator import Fixture, ORDER_IDS, STATUS
from simulated_status_webhook import confirmed_event


class ConfirmedEventTests(unittest.TestCase):
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

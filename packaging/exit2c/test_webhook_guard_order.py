"""Process-order unit tests; Linux platform simulation is NOT Linux acceptance."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class WebhookGuardOrderTests(unittest.TestCase):
    def probe(self, mode, mutation='', simulate=True):
        script = r'''
import builtins, json, os, socket, sys
import webhook_asgi_acceptance as entry
from source_paths import backend_root
sys.path.insert(0, str(backend_root()))
import independent_runtime as runtime
events=[]
validated={}
original_import=builtins.__import__
class BoundaryReached(BaseException): pass
def observed_import(name, *args, **kwargs):
    if name.split('.')[0] in ('fastapi','salla_integration','mongomock_motor'):
        if validated and dict(os.environ)!=validated:
            raise RuntimeError('POST_GUARD_ENVIRONMENT_CHANGED')
        events.append('APPLICATION_IMPORT')
        raise BoundaryReached()
    return original_import(name,*args,**kwargs)
builtins.__import__=observed_import
def profile(frame,event,arg):
    if frame.f_code is runtime.validate_before_import.__code__:
        if event=='call':
            events.append('GUARD_CALL')
            if sys.argv[3]=='yes':
                sys.platform='linux'
                socket.if_nameindex=lambda:[(1,'lo')]
            mutation=sys.argv[2]
            changes={'profile':('MEZAN_ACCEPTANCE_PROFILE','invalid'),
                     'address':('SALLA_API_BASE','https://invalid.example'),
                     'key':('SALLA_TOKEN_ENC_KEY','private-test-marker'),
                     'worker':('MEZAN_WORKER_ENABLED','1')}
            if mutation in changes: os.environ[changes[mutation][0]]=changes[mutation][1]
        elif event=='return' and os.environ.get('MEZAN_INDEPENDENT_RUNTIME')=='1':
            validated.update(os.environ)
            events.append('GUARD_ACCEPTED')
sys.setprofile(profile)
mode=sys.argv[1]
# main receives only its real command line. The profile retains test parameters.
import argparse
original_parse=argparse.ArgumentParser.parse_args
argparse.ArgumentParser.parse_args=lambda self:original_parse(self, ['--preflight-only'] if mode=='preflight' else [])
try:
    result=entry.main()
except BoundaryReached:
    result='BOUNDARY'
finally:
    sys.setprofile(None)
if 'SALLA_WEBHOOK_SECRET' in os.environ or 'SALLA_TOKEN_ENC_KEY' in os.environ:
    raise RuntimeError('SECRET_CLEANUP_FAILED')
print(json.dumps({'events':events,'result':result}))
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, mode, mutation,
                                 'yes' if simulate else 'no'],
                                cwd=Path(__file__).resolve().parent,
                                capture_output=True, text=True, timeout=20)
        output = result.stdout + result.stderr
        self.assertNotIn('private-test-marker', output, 'SECRET_LEAK')
        self.assertEqual(result.returncode, 0, 'PROBE_PROCESS_FAILURE')
        return json.loads(result.stdout.splitlines()[-1]), output

    def test_real_guard_rejects_mutations_before_application_import_both_modes(self):
        for mode in ('preflight', 'connected'):
            for mutation in ('profile', 'address', 'key', 'worker'):
                with self.subTest(mode=mode, mutation=mutation):
                    result, output = self.probe(mode, mutation)
                    self.assertEqual(result, {'events':['GUARD_CALL'], 'result':1})
                    self.assertIn('ASGI_RUNTIME_GUARD_REJECTED', output)
                    self.assertNotIn('"complete": true', output)

    def test_simulated_linux_real_guard_accepts_before_first_application_import(self):
        for mode in ('preflight', 'connected'):
            result, _ = self.probe(mode)
            self.assertEqual(result, {'events':['GUARD_CALL','GUARD_ACCEPTED','APPLICATION_IMPORT'],
                                      'result':'BOUNDARY'})

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows rejection evidence only')
    def test_native_windows_real_guard_rejects_both_modes(self):
        for mode in ('preflight', 'connected'):
            result, output = self.probe(mode, simulate=False)
            self.assertEqual(result, {'events':['GUARD_CALL'], 'result':1})
            self.assertIn('ASGI_RUNTIME_GUARD_REJECTED', output)


if __name__ == '__main__':
    unittest.main()

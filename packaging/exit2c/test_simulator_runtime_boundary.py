"""Windows unit tests: platform/interfaces simulated, real runtime guard/crypto."""
import base64
import builtins
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / 'backend'
spec = importlib.util.spec_from_file_location('sim_boundary_runtime', ROOT/'independent_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
KEY = base64.urlsafe_b64encode(bytes(range(32))).decode('ascii')
PROFILE = 'salla_http_simulator_v1'


class BoundaryTests(unittest.TestCase):
    def environment(self):
        return {**runtime.SYNTHETIC, 'MEZAN_ACCEPTANCE_PROFILE':PROFILE,
                'SALLA_TOKEN_ENC_KEY':KEY, 'SALLA_API_BASE':'http://127.0.0.1:8093/admin/v2',
                'SALLA_AUTH_BASE':'http://127.0.0.1:8093'}

    @contextlib.contextmanager
    def isolated_unit_environment(self, env, interfaces=None, dotenv=False, platform='linux'):
        with patch.dict(os.environ, env, clear=True), patch.object(runtime.sys,'platform',platform), \
             patch.object(runtime.socket,'if_nameindex',return_value=[(1,'lo')] if interfaces is None else interfaces), \
             patch.object(runtime.Path,'rglob',return_value=iter([Path('.env')] if dotenv else [])):
            yield

    def test_valid_web_fixture(self):
        for role in ('web',):
            with self.isolated_unit_environment(self.environment()):
                runtime.validate_before_import(role)
                self.assertEqual(os.environ.get('MEZAN_INDEPENDENT_RUNTIME'),'1')

    def assert_rejected_before_server(self, env, role='web', **kwargs):
        original = builtins.__import__
        attempted = []
        def imports(name,*args,**kw):
            if name == 'server':
                attempted.append(name)
                raise AssertionError('Server import was reached')
            return original(name,*args,**kw)
        output, errors = io.StringIO(), io.StringIO()
        with self.isolated_unit_environment(env, **kwargs), patch('builtins.__import__',side_effect=imports), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            before = dict(os.environ)
            with self.assertRaises(RuntimeError):
                runtime.load_server(role)
            self.assertTrue(dict(os.environ)==before, 'Rejected guard changed environment')
        self.assertFalse(attempted,'Server imported on rejection')
        self.assertFalse(output.getvalue() or errors.getvalue(),'Guard emitted sensitive output')

    def test_wrong_valid_fernet_and_missing_key(self):
        self.assert_rejected_before_server({**self.environment(),'SALLA_TOKEN_ENC_KEY':base64.urlsafe_b64encode(bytes(range(1,33))).decode()})
        env=self.environment();env.pop('SALLA_TOKEN_ENC_KEY');self.assert_rejected_before_server(env)

    def test_absent_unknown_empty_profile_and_production(self):
        for value in (None,'','unknown'):
            env=self.environment()
            if value is None: env.pop('MEZAN_ACCEPTANCE_PROFILE')
            else: env['MEZAN_ACCEPTANCE_PROFILE']=value
            self.assert_rejected_before_server(env)
        for value in ('production',None):
            env=self.environment()
            if value is None: env.pop('APP_ENV')
            else: env['APP_ENV']=value
            self.assert_rejected_before_server(env)

    def test_exact_addresses_and_all_proxy_spellings(self):
        for key in ('SALLA_API_BASE','SALLA_AUTH_BASE'):
            for value in ('https://external.example.test','http://127.0.0.1:8093@external.example.test','http://localhost:8093','http://127.0.0.1:8093/'):
                self.assert_rejected_before_server({**self.environment(),key:value})
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','hTtPs_PrOxY'):
            self.assert_rejected_before_server({**self.environment(),key:'http://external.example.test'})

    def test_platform_namespace_database_and_dotenv(self):
        self.assert_rejected_before_server(self.environment(),platform='win32')
        self.assert_rejected_before_server(self.environment(),interfaces=[(1,'lo'),(2,'eth0')])
        self.assert_rejected_before_server(self.environment(),interfaces=[])
        for key in ('DB_NAME','MONGO_URL','JWT_SECRET'):
            self.assert_rejected_before_server({**self.environment(),key:'other-synthetic-value'})
        self.assert_rejected_before_server(self.environment(),dotenv=True)

    def test_other_credentials_rotation_worker_and_arming(self):
        for key in ('QOYOD_API_KEY','OTHER_TOKEN_ENC_KEY','SALLA_TOKEN_ENC_KEY_OLD','SALLA_CLIENT_SECRET','EMAIL_OTP_SMTP_PASSWORD'):
            self.assert_rejected_before_server({**self.environment(),key:'not-a-live-value'})
        self.assert_rejected_before_server(self.environment(),role='worker')
        self.assert_rejected_before_server(self.environment(),role='migration')
        self.assert_rejected_before_server({**self.environment(),'MEZAN_WORKER_ENABLED':'1'})

    def test_legacy_test_and_normal_production_unchanged(self):
        with self.isolated_unit_environment(dict(runtime.SYNTHETIC)):
            runtime.validate_before_import('web')
        for app_env in ('production',None):
            env={'SALLA_TOKEN_ENC_KEY':KEY,'QOYOD_API_KEY':'unit-only','DB_NAME':'unit-only'}
            if app_env: env['APP_ENV']=app_env
            with self.isolated_unit_environment(env,platform='win32'):
                runtime.validate_before_import('web')

    def test_real_crypto_round_trip_without_output(self):
        crypto_spec=importlib.util.spec_from_file_location('sim_boundary_crypto',ROOT/'salla_integration/crypto.py')
        crypto=importlib.util.module_from_spec(crypto_spec);crypto_spec.loader.exec_module(crypto)
        token=secrets.token_urlsafe(32)
        output,errors=io.StringIO(),io.StringIO()
        with self.isolated_unit_environment(self.environment()), contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
            runtime.validate_before_import('web')
            encrypted=crypto.encrypt_token(token)
            self.assertTrue(crypto.decrypt_token(encrypted)==token,'Crypto round trip failed')
            self.assertTrue(token.encode() not in encrypted,'Plaintext in ciphertext')
            with self.assertRaises(ValueError): crypto.decrypt_token(encrypted[:-1]+b'!')
        self.assertFalse(output.getvalue() or errors.getvalue(),'Crypto emitted sensitive output')


if __name__ == '__main__':
    unittest.main()

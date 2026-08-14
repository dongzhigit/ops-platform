import base64
import json
from unittest import TestCase

from spug.env_settings import env_bool, env_list, load_security_settings, validate_master_key


MASTER_KEY = base64.b64encode(b'k' * 32).decode('ascii')
ROTATED_KEY = base64.b64encode(b'r' * 32).decode('ascii')


class EnvSettingsTest(TestCase):
    def test_development_defaults_remain_compatible(self):
        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ={},
        )

        self.assertEqual(result['environment'], 'development')
        self.assertTrue(result['debug'])
        self.assertEqual(result['secret_key'], 'development-secret')
        self.assertEqual(result['allowed_hosts'], ['127.0.0.1'])

    def test_production_requires_explicit_secret_and_hosts(self):
        with self.assertRaisesRegex(ValueError, 'SPUG_SECRET_KEY'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ={'SPUG_ENV': 'production'},
            )

    def test_production_rejects_debug_and_wildcard_hosts(self):
        common = {
            'SPUG_ENV': 'production',
            'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
            'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
            'SPUG_ALLOWED_HOSTS': 'ops.example.com',
        }

        with self.assertRaisesRegex(ValueError, 'SPUG_DEBUG'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=dict(common, SPUG_DEBUG='true'),
            )

        with self.assertRaisesRegex(ValueError, 'cannot contain'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=dict(common, SPUG_ALLOWED_HOSTS='*'),
            )

    def test_production_security_options(self):
        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ={
                'SPUG_ENV': 'production',
                'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
                'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
                'SPUG_ALLOWED_HOSTS': 'ops.example.com,10.0.0.10',
                'SPUG_CSRF_TRUSTED_ORIGINS': 'ops.example.com',
                'SPUG_SSL_REDIRECT': 'yes',
            },
        )

        self.assertFalse(result['debug'])
        self.assertEqual(result['allowed_hosts'], ['ops.example.com', '10.0.0.10'])
        self.assertEqual(result['csrf_trusted_origins'], ['ops.example.com'])
        self.assertTrue(result['secure_cookies'])
        self.assertTrue(result['ssl_redirect'])

    def test_boolean_and_list_parsing(self):
        self.assertTrue(env_bool('FLAG', environ={'FLAG': 'ON'}))
        self.assertFalse(env_bool('FLAG', default=True, environ={'FLAG': '0'}))
        self.assertEqual(env_list('HOSTS', environ={'HOSTS': 'a, b,,'}), ['a', 'b'])
        with self.assertRaisesRegex(ValueError, 'FLAG'):
            env_bool('FLAG', environ={'FLAG': 'sometimes'})

    def test_production_rejects_weak_secret_and_empty_hosts(self):
        with self.assertRaisesRegex(ValueError, 'at least 32'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ={
                    'SPUG_ENV': 'production',
                    'SPUG_SECRET_KEY': 'too-short',
                    'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
                    'SPUG_ALLOWED_HOSTS': 'ops.example.com',
                },
            )

        with self.assertRaisesRegex(ValueError, 'SPUG_ALLOWED_HOSTS'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ={
                    'SPUG_ENV': 'production',
                    'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
                    'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
                    'SPUG_ALLOWED_HOSTS': ', ,',
                },
            )

    def test_production_requires_valid_credential_master_key(self):
        common = {
            'SPUG_ENV': 'production',
            'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
            'SPUG_ALLOWED_HOSTS': 'ops.example.com',
        }
        with self.assertRaisesRegex(ValueError, 'SPUG_CREDENTIAL_MASTER_KEY'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=common,
            )

        with self.assertRaisesRegex(ValueError, 'exactly 32 bytes'):
            validate_master_key(base64.b64encode(b'too-short').decode('ascii'))

        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ=dict(common, SPUG_CREDENTIAL_MASTER_KEY=MASTER_KEY),
        )
        self.assertEqual(result['credential_master_key'], MASTER_KEY)

    def test_staged_credential_keyring_selects_primary_key(self):
        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ={
                'SPUG_ENV': 'production',
                'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
                'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
                'SPUG_CREDENTIAL_KEYRING': json.dumps({'v2': ROTATED_KEY}),
                'SPUG_CREDENTIAL_PRIMARY_KEY_ID': 'v2',
                'SPUG_ALLOWED_HOSTS': 'ops.example.com',
            },
        )

        self.assertEqual(result['credential_primary_key_id'], 'v2')
        self.assertEqual(result['credential_master_key'], ROTATED_KEY)
        self.assertEqual(result['credential_master_keys']['primary'], MASTER_KEY)
        self.assertEqual(result['credential_master_keys']['v2'], ROTATED_KEY)

        with self.assertRaisesRegex(ValueError, 'not present'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ={
                    'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
                    'SPUG_CREDENTIAL_PRIMARY_KEY_ID': 'missing',
                },
            )

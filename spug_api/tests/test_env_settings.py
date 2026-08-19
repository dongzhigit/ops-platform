import base64
import json
from unittest import TestCase

from spug.env_settings import (
    env_bool,
    env_list,
    load_security_settings,
    validate_guacamole_key,
    validate_master_key,
)


MASTER_KEY = base64.b64encode(b'k' * 32).decode('ascii')
ROTATED_KEY = base64.b64encode(b'r' * 32).decode('ascii')
AUDIT_KEY = 'audit-signing-key-that-is-independent-and-long'
GUACAMOLE_KEY = '00' * 16


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
        self.assertTrue(result['remote_gateway_enabled'])
        self.assertFalse(result['aiops_enabled'])
        self.assertEqual(len(result['guacamole_json_secret_key']), 32)

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
            'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
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
                'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
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
            'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
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
                'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
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

    def test_production_requires_independent_audit_signing_key(self):
        common = {
            'SPUG_ENV': 'production',
            'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
            'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
            'SPUG_ALLOWED_HOSTS': 'ops.example.com',
        }
        with self.assertRaisesRegex(ValueError, 'SPUG_AUDIT_SIGNING_KEY'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=common,
            )
        with self.assertRaisesRegex(ValueError, 'independent'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=dict(common, SPUG_AUDIT_SIGNING_KEY=common['SPUG_SECRET_KEY']),
            )

    def test_production_remote_gateway_requires_independent_explicit_key(self):
        common = {
            'SPUG_ENV': 'production',
            'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
            'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
            'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
            'SPUG_ALLOWED_HOSTS': 'ops.example.com',
            'SPUG_REMOTE_GATEWAY_ENABLED': 'true',
        }
        with self.assertRaisesRegex(ValueError, 'SPUG_GUACAMOLE_JSON_SECRET_KEY'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=common,
            )

        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ=dict(
                common,
                SPUG_GUACAMOLE_JSON_SECRET_KEY=GUACAMOLE_KEY,
                SPUG_GUACAMOLE_PUBLIC_URL='/remote/',
                SPUG_REMOTE_TICKET_TTL='45',
                SPUG_GUACAMOLE_AUTH_TTL='30',
            ),
        )
        self.assertTrue(result['remote_gateway_enabled'])
        self.assertEqual(result['guacamole_json_secret_key'], GUACAMOLE_KEY)
        self.assertEqual(result['guacamole_public_url'], '/remote')
        self.assertEqual(result['remote_ticket_ttl'], 45)
        self.assertEqual(result['guacamole_auth_ttl'], 30)

    def test_remote_gateway_rejects_unsafe_url_key_and_ttl(self):
        with self.assertRaisesRegex(ValueError, '32 hexadecimal'):
            validate_guacamole_key('not-a-key')
        for public_url in ('https://remote.example.com/guacamole', '//remote/guacamole', '/guacamole/?x=1'):
            with self.assertRaisesRegex(ValueError, 'SPUG_GUACAMOLE_PUBLIC_URL'):
                load_security_settings(
                    default_secret='development-secret',
                    default_debug=True,
                    default_allowed_hosts=['127.0.0.1'],
                    environ={'SPUG_GUACAMOLE_PUBLIC_URL': public_url},
                )
        with self.assertRaisesRegex(ValueError, 'between 15 and 300'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ={'SPUG_REMOTE_TICKET_TTL': '600'},
            )

    def test_production_observability_requires_two_independent_tokens(self):
        common = {
            'SPUG_ENV': 'production',
            'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
            'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
            'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
            'SPUG_ALLOWED_HOSTS': 'ops.example.com',
            'SPUG_OBSERVABILITY_ENABLED': 'true',
        }
        with self.assertRaisesRegex(ValueError, 'SPUG_PROMETHEUS_DISCOVERY_TOKEN'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=common,
            )

        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ=dict(
                common,
                SPUG_PROMETHEUS_DISCOVERY_TOKEN='p' * 64,
                SPUG_ALERTMANAGER_WEBHOOK_TOKEN='w' * 64,
                SPUG_PROMETHEUS_URL='http://prometheus:9090',
                SPUG_ALERTMANAGER_URL='http://alertmanager:9093',
            ),
        )
        self.assertTrue(result['observability_enabled'])
        self.assertEqual(result['prometheus_discovery_token'], 'p' * 64)
        self.assertEqual(result['alertmanager_webhook_token'], 'w' * 64)

    def test_production_aiops_requires_model_and_independent_key(self):
        common = {
            'SPUG_ENV': 'production',
            'SPUG_SECRET_KEY': 'a-production-secret-with-32-characters',
            'SPUG_CREDENTIAL_MASTER_KEY': MASTER_KEY,
            'SPUG_AUDIT_SIGNING_KEY': AUDIT_KEY,
            'SPUG_ALLOWED_HOSTS': 'ops.example.com',
            'SPUG_AIOPS_ENABLED': 'true',
            'SPUG_AIOPS_BASE_URL': 'https://model.example.com/v1',
            'SPUG_AIOPS_MODEL': 'ops-model-v1',
        }
        with self.assertRaisesRegex(ValueError, 'SPUG_AIOPS_API_KEY'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=common,
            )
        with self.assertRaisesRegex(ValueError, 'independent'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=dict(common, SPUG_AIOPS_API_KEY=AUDIT_KEY),
            )
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=dict(
                    common,
                    SPUG_AIOPS_BASE_URL='http://model.example.com/v1',
                    SPUG_AIOPS_API_KEY='independent-ai-provider-key',
                ),
            )
        result = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ=dict(common, SPUG_AIOPS_API_KEY='independent-ai-provider-key'),
        )
        self.assertTrue(result['aiops_enabled'])
        self.assertEqual(result['aiops_api_format'], 'openai')
        self.assertEqual(result['aiops_model'], 'ops-model-v1')
        self.assertEqual(result['aiops_api_key'], 'independent-ai-provider-key')

        anthropic = load_security_settings(
            default_secret='development-secret',
            default_debug=True,
            default_allowed_hosts=['127.0.0.1'],
            environ=dict(
                common,
                SPUG_AIOPS_API_FORMAT='anthropic',
                SPUG_AIOPS_API_KEY='independent-ai-provider-key',
            ),
        )
        self.assertEqual(anthropic['aiops_api_format'], 'anthropic')
        with self.assertRaisesRegex(ValueError, 'openai or anthropic'):
            load_security_settings(
                default_secret='development-secret',
                default_debug=True,
                default_allowed_hosts=['127.0.0.1'],
                environ=dict(common, SPUG_AIOPS_API_FORMAT='unsupported'),
            )

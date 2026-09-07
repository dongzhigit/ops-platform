import base64
import json
from datetime import datetime, timedelta
from io import StringIO
from unittest import TestCase as UnitTestCase
from unittest.mock import patch

from django.core.management import call_command
from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from libs import AttrDict
from libs.ssh import AuthenticationException
from apps.account.models import Role, User
from apps.account.utils import has_host_perm
from apps.assets.encryption import CredentialEncryptionError, EnvelopeCipher
from apps.assets.models import AccessGrant, AssetIdentityBinding, Credential, Identity
from apps.assets.views import CredentialView
from apps.exec.models import ExecHistory
from apps.exec.views import TaskView
from apps.host.models import Group, Host
from apps.host.views import HostView, _do_host_verify


MASTER_KEY = base64.b64encode(b'm' * 32).decode('ascii')
PRIVATE_KEY = '-----BEGIN PRIVATE KEY-----\ntest-key-material\n-----END PRIVATE KEY-----'


class EnvelopeCipherTest(UnitTestCase):
    def test_round_trip_and_tamper_detection(self):
        cipher = EnvelopeCipher(MASTER_KEY)
        payload = cipher.encrypt('high-value-secret', 'record-1')

        self.assertNotIn('high-value-secret', payload)
        self.assertEqual(cipher.decrypt(payload, 'record-1'), b'high-value-secret')

        data = json.loads(payload)
        ciphertext = bytearray(base64.b64decode(data['ciphertext']))
        ciphertext[-1] ^= 1
        data['ciphertext'] = base64.b64encode(bytes(ciphertext)).decode('ascii')
        with self.assertRaises(CredentialEncryptionError):
            cipher.decrypt(json.dumps(data), 'record-1')

    def test_record_binding_and_wrong_master_key_fail_closed(self):
        cipher = EnvelopeCipher(MASTER_KEY)
        payload = cipher.encrypt('secret', 'record-1')
        with self.assertRaises(CredentialEncryptionError):
            cipher.decrypt(payload, 'record-2')

        other = EnvelopeCipher(base64.b64encode(b'n' * 32).decode('ascii'))
        with self.assertRaises(CredentialEncryptionError):
            other.decrypt(payload, 'record-1')


class AssetAccessTest(TestCase):
    def setUp(self):
        self.admin = self.make_user('admin', is_supper=True)
        self.user = self.make_user('operator')
        self.host1 = Host.objects.create(
            name='host-1',
            hostname='10.0.0.1',
            port=22,
            username='root',
            pkey=PRIVATE_KEY,
            created_by=self.admin,
        )
        self.host2 = Host.objects.create(
            name='host-2',
            hostname='10.0.0.2',
            port=22,
            username='root',
            created_by=self.admin,
        )

    def make_user(self, username, is_supper=False):
        return User.objects.create(
            username=username,
            nickname=username,
            password_hash='unused',
            type='default',
            is_supper=is_supper,
            is_active=True,
            access_token=(username + ('x' * 32))[:32],
            token_expired=0,
            last_login='',
            last_ip='127.0.0.1',
        )

    def test_versioned_asset_route_matches_nginx_api_rewrite(self):
        match = resolve('/v1/assets/credentials/')
        self.assertIs(match.func.view_class, CredentialView)

    def make_credential(self, name='credential-1', secret=PRIVATE_KEY):
        return Credential.create_with_secret(
            secret,
            name=name,
            type='ssh_key',
            created_by=self.admin,
        )

    def test_secret_is_encrypted_and_never_serialized(self):
        credential = self.make_credential()

        self.assertNotIn(PRIVATE_KEY, credential.secret_data)
        self.assertEqual(credential.reveal_secret(), PRIVATE_KEY)
        view = credential.to_dict()
        self.assertNotIn('secret_data', view)
        self.assertNotIn('secret', view)
        self.assertTrue(view['has_secret'])

        host_view = self.host1.to_view()
        self.assertNotIn('pkey', host_view)
        self.assertTrue(host_view['has_pkey'])

    def test_default_identity_drives_host_connection_profile(self):
        credential = self.make_credential()
        identity = Identity.objects.create(
            name='production-root',
            protocol='ssh',
            username='ops',
            credential=credential,
            created_by=self.admin,
        )
        AssetIdentityBinding.bind(
            host=self.host2,
            identity=identity,
            created_by=self.admin,
            is_default=True,
        )

        profile = self.host2.get_connection_profile()
        self.assertEqual(profile['username'], 'ops')
        self.assertEqual(profile['secret'], PRIVATE_KEY)
        self.assertEqual(self.host2.private_key, PRIVATE_KEY)
        self.assertNotIn('pkey', self.host2.to_view())
        self.assertEqual(self.host2.to_view()['credential_source'], 'identity')

    def test_password_identity_builds_password_ssh_profile(self):
        credential = self.make_credential(name='password-credential', secret='strong-password')
        credential.type = 'password'
        credential.save(update_fields=('type',))
        identity = Identity.objects.create(
            name='password-identity',
            protocol='ssh',
            username='deployer',
            credential=credential,
            created_by=self.admin,
        )
        AssetIdentityBinding.bind(
            host=self.host2,
            identity=identity,
            created_by=self.admin,
            is_default=True,
        )

        ssh = self.host2.get_ssh()
        self.assertEqual(ssh.arguments['username'], 'deployer')
        self.assertEqual(ssh.arguments['password'], 'strong-password')
        self.assertIsNone(ssh.arguments['pkey'])

    def test_host_password_retry_bypasses_existing_connection_profile(self):
        group = Group.objects.create(name='production')
        group.hosts.add(self.host2)
        captured = {}

        def fake_verify(form, connection_override=None):
            captured['connection_override'] = connection_override
            captured['password'] = form.password
            form.pop('password')
            return True

        request = RequestFactory().post(
            '/api/host/',
            data=json.dumps({
                'id': self.host2.id,
                'group_ids': [group.id],
                'name': self.host2.name,
                'username': self.host2.username,
                'hostname': self.host2.hostname,
                'port': self.host2.port,
                'desc': self.host2.desc,
                'password': 'temporary-password',
            }),
            content_type='application/json',
        )
        request.user = self.admin

        with patch('apps.host.views._do_host_verify', fake_verify):
            response = HostView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.content.decode('utf-8'))['error'])
        self.assertIsNone(captured['connection_override'])
        self.assertEqual(captured['password'], 'temporary-password')

    def test_unmanaged_key_auth_failure_prompts_password_retry(self):
        class FailingSSH:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                raise AuthenticationException('bad key')

            def __exit__(self, exc_type, exc, tb):
                return False

        form = AttrDict(
            hostname='10.0.0.2',
            port=22,
            username='root',
            pkey='',
            password='',
        )
        profile = {
            'identity_id': None,
            'username': 'root',
            'credential_type': 'ssh_key',
            'secret': PRIVATE_KEY,
        }

        with patch('apps.host.views.SSH', FailingSSH):
            self.assertFalse(_do_host_verify(form, connection_override=profile))

    def test_managed_identity_host_rejects_password_retry(self):
        group = Group.objects.create(name='managed')
        group.hosts.add(self.host2)
        credential = self.make_credential(name='managed-key')
        identity = Identity.objects.create(
            name='managed-identity',
            protocol='ssh',
            username='ops',
            credential=credential,
            created_by=self.admin,
        )
        AssetIdentityBinding.bind(
            host=self.host2,
            identity=identity,
            created_by=self.admin,
            is_default=True,
        )
        request = RequestFactory().post(
            '/api/host/',
            data=json.dumps({
                'id': self.host2.id,
                'group_ids': [group.id],
                'name': self.host2.name,
                'username': self.host2.username,
                'hostname': self.host2.hostname,
                'port': self.host2.port,
                'desc': self.host2.desc,
                'password': 'temporary-password',
            }),
            content_type='application/json',
        )
        request.user = self.admin

        with patch('apps.host.views._do_host_verify') as verify:
            response = HostView.as_view()(request)

        verify.assert_not_called()
        data = json.loads(response.content.decode('utf-8'))
        self.assertIn('托管身份', data['error'])

    def test_identity_scoped_grant_follows_default_binding(self):
        credential = self.make_credential()
        identity1 = Identity.objects.create(
            name='identity-1', protocol='ssh', username='ops1',
            credential=credential, created_by=self.admin,
        )
        identity2 = Identity.objects.create(
            name='identity-2', protocol='ssh', username='ops2',
            credential=credential, created_by=self.admin,
        )
        AssetIdentityBinding.bind(self.host2, identity1, self.admin, is_default=True)
        AssetIdentityBinding.bind(self.host2, identity2, self.admin, is_default=False)
        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host2,
            identity=identity1,
            effect='allow',
            actions=json.dumps(['ssh.connect']),
            created_by=self.admin,
        )

        self.assertTrue(has_host_perm(self.user, self.host2.id, action='ssh.connect'))
        AssetIdentityBinding.bind(self.host2, identity2, self.admin, is_default=True)
        self.assertFalse(has_host_perm(self.user, self.host2.id, action='ssh.connect'))

    def test_explicit_grants_extend_and_deny_legacy_group_access(self):
        group = Group.objects.create(name='production')
        group.hosts.add(self.host1)
        role = Role.objects.create(
            name='operator-role',
            group_perms=json.dumps([group.id]),
            created_by=self.admin,
        )
        self.user.roles.add(role)

        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host2,
            effect='allow',
            actions=json.dumps(['ssh.connect']),
            created_by=self.admin,
        )
        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host1,
            effect='deny',
            actions=json.dumps(['exec.run']),
            created_by=self.admin,
        )
        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host2,
            effect='allow',
            actions=json.dumps(['file.write']),
            valid_until=datetime.now() - timedelta(minutes=1),
            created_by=self.admin,
        )

        self.assertTrue(has_host_perm(self.user, self.host1.id, action='file.read'))
        self.assertFalse(has_host_perm(self.user, self.host1.id, action='exec.run'))
        self.assertTrue(has_host_perm(self.user, self.host1.id, action='host.view'))
        self.assertTrue(has_host_perm(self.user, self.host2.id, action='ssh.connect'))
        self.assertFalse(has_host_perm(self.user, self.host2.id, action='file.write'))

    def test_credential_api_response_does_not_contain_secret(self):
        request = RequestFactory().post(
            '/api/v1/assets/credentials/',
            data=json.dumps({
                'name': 'api-key',
                'type': 'ssh_key',
                'secret': PRIVATE_KEY,
                'description': 'created by API',
            }),
            content_type='application/json',
        )
        request.user = self.admin
        response = CredentialView.as_view()(request)
        body = response.content.decode('utf-8')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(PRIVATE_KEY, body)
        self.assertNotIn('secret_data', body)
        self.assertTrue(json.loads(body)['data']['has_secret'])

    def test_exec_queue_contains_references_not_decrypted_credentials(self):
        task = ExecHistory.objects.create(
            user=self.admin,
            digest='queue-secret-test',
            interpreter='sh',
            command='id',
            host_ids=json.dumps([self.host1.id]),
            params='{}',
        )
        request = RequestFactory().patch(
            '/exec/task/',
            data=json.dumps({'token': task.digest}),
            content_type='application/json',
        )
        request.user = self.admin

        queued = []

        class RedisStub:
            def rpush(self, key, value):
                queued.append((key, value))

        with patch('apps.exec.views.get_redis_connection', return_value=RedisStub()):
            response = TaskView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queued), 1)
        payload = queued[0][1]
        self.assertNotIn(PRIVATE_KEY, payload)
        self.assertNotIn('pkey', payload)
        self.assertEqual(json.loads(payload)['host_id'], self.host1.id)

    def test_legacy_host_key_migration_verifies_before_clearing(self):
        output = StringIO()
        call_command(
            'migrate_host_credentials',
            execute=True,
            clear_legacy=True,
            stdout=output,
        )
        self.host1.refresh_from_db()
        binding = self.host1.asset_identity_bindings.select_related(
            'identity__credential'
        ).get(is_default=True)

        self.assertIsNone(self.host1.pkey)
        self.assertEqual(binding.identity.credential.reveal_secret(), PRIVATE_KEY)
        self.assertIn('已迁移 1 台主机', output.getvalue())

    def test_master_key_rotation_reencrypts_and_verifies_credentials(self):
        credential = self.make_credential()
        rotated_key = base64.b64encode(b'r' * 32).decode('ascii')
        output = StringIO()

        with override_settings(
            SPUG_CREDENTIAL_MASTER_KEYS={
                'primary': settings.SPUG_CREDENTIAL_MASTER_KEYS['primary'],
                'v2': rotated_key,
            },
            SPUG_CREDENTIAL_PRIMARY_KEY_ID='v2',
        ):
            call_command(
                'rotate_credential_master_key',
                target_key_id='v2',
                execute=True,
                stdout=output,
            )
            credential.refresh_from_db()
            self.assertEqual(credential.key_id, 'v2')
            self.assertEqual(credential.reveal_secret(), PRIVATE_KEY)
        self.assertIn('已轮换 1 条凭据', output.getvalue())

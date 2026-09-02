import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from apps.account.models import Role, User
from apps.assets.models import AccessGrant, AssetIdentityBinding, Credential, Identity
from apps.audit.models import AuditEvent
from apps.gateway.models import RemoteAccessEndpoint, RemoteAccessSession
from apps.gateway.services import (
    encrypt_guacamole_document,
    expire_remote_access_sessions,
    ticket_digest,
)
from apps.gateway.views import EndpointView, SessionLaunchView, SessionView
from apps.host.models import Host


GUACAMOLE_KEY = '00' * 16


def response_body(response):
    return json.loads(response.content.decode('utf-8'))


def decrypt_guacamole_data(value, key=GUACAMOLE_KEY):
    secret = bytes.fromhex(key)
    decryptor = Cipher(
        algorithms.AES(secret), modes.CBC(b'\0' * 16)
    ).decryptor()
    padded = decryptor.update(base64.b64decode(value)) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    signed = unpadder.update(padded) + unpadder.finalize()
    signature, plaintext = signed[:32], signed[32:]
    if not hmac.compare_digest(signature, hmac.new(secret, plaintext, hashlib.sha256).digest()):
        raise AssertionError('Guacamole JSON signature mismatch')
    return json.loads(plaintext.decode('utf-8'))


@override_settings(
    SPUG_REMOTE_GATEWAY_ENABLED=True,
    SPUG_GUACAMOLE_JSON_SECRET_KEY=GUACAMOLE_KEY,
    SPUG_GUACAMOLE_PUBLIC_URL='/guacamole',
    SPUG_REMOTE_TICKET_TTL=60,
    SPUG_GUACAMOLE_AUTH_TTL=60,
)
class RemoteGatewayTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = self.make_user('admin', is_supper=True)
        self.user = self.make_user('operator')
        role = Role.objects.create(
            name='remote-operator',
            page_perms=json.dumps({'host': {'console': ['remote']}}),
            created_by=self.admin,
        )
        self.user.roles.add(role)
        self.host = Host.objects.create(
            name='windows-01',
            hostname='10.20.30.40',
            port=22,
            username='legacy',
            created_by=self.admin,
        )
        self.credential = Credential.create_with_secret(
            'Never-Return-This-Password!',
            name='windows-password',
            type='password',
            created_by=self.admin,
        )
        self.identity = Identity.objects.create(
            name='windows-admin',
            protocol='rdp',
            username='Administrator',
            credential=self.credential,
            created_by=self.admin,
        )
        AssetIdentityBinding.bind(
            self.host, self.identity, self.admin, is_default=True
        )
        self.endpoint = RemoteAccessEndpoint.objects.create(
            host=self.host,
            protocol='rdp',
            port=3389,
            identity=self.identity,
            display_name='Windows 01',
            settings=json.dumps({
                'security': 'nla',
                'ignore-cert': False,
                'resize-method': 'display-update',
            }),
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

    def grant_remote(self, user, effect='allow'):
        return AccessGrant.objects.create(
            subject_user=user,
            host=self.host,
            identity=self.identity,
            effect=effect,
            actions=json.dumps(['rdp.connect']),
            created_by=self.admin,
        )

    def issue(self, user=None):
        request = self.factory.post(
            '/v1/gateway/sessions/',
            data=json.dumps({'endpoint_id': self.endpoint.id}),
            content_type='application/json',
        )
        request.user = user or self.admin
        return SessionView.as_view()(request)

    def launch(self, launch_url):
        parsed = urlsplit(launch_url)
        request = self.factory.get(parsed.path, parse_qs(parsed.query))
        session_id = parsed.path.split('/')[-3]
        return SessionLaunchView.as_view()(request, session_id=session_id)

    def test_versioned_gateway_routes_and_anonymous_launch_exclusion(self):
        self.assertIs(resolve('/v1/gateway/endpoints/').func.view_class, EndpointView)
        self.assertIs(resolve('/v1/gateway/sessions/').func.view_class, SessionView)
        self.assertIs(
            resolve('/v1/gateway/sessions/11111111-1111-1111-1111-111111111111/launch/').func.view_class,
            SessionLaunchView,
        )
        anonymous = self.client.get(
            '/v1/gateway/sessions/11111111-1111-1111-1111-111111111111/launch/',
            {'ticket': 'a' * 43},
        )
        self.assertEqual(anonymous.status_code, 404)
        self.assertNotEqual(anonymous.status_code, 401)

    def test_official_json_auth_format_is_signed_then_aes_cbc_encrypted(self):
        document = {
            'username': 'ops-platform-user-1',
            'expires': 1787000000000,
            'connections': {},
        }
        encrypted = encrypt_guacamole_document(document, GUACAMOLE_KEY)

        self.assertNotIn('ops-platform-user-1', encrypted)
        self.assertEqual(decrypt_guacamole_data(encrypted), document)

    def test_endpoint_rejects_parameter_override_and_unbound_identity(self):
        self.endpoint.settings = json.dumps({'hostname': 'attacker.example'})
        with self.assertRaisesRegex(Exception, '不支持的远程桌面参数'):
            self.endpoint.full_clean()

        other_host = Host.objects.create(
            name='other', hostname='10.20.30.41', port=22, username='root',
            created_by=self.admin,
        )
        self.endpoint.host = other_host
        self.endpoint.settings = '{}'
        with self.assertRaisesRegex(Exception, '尚未绑定'):
            self.endpoint.full_clean()

    def test_admin_config_api_never_serializes_password(self):
        request = self.factory.get(
            '/v1/gateway/endpoints/', {'host_id': self.host.id}
        )
        request.user = self.admin
        response = EndpointView.as_view()(request)
        body = response.content.decode('utf-8')

        self.assertFalse(response_body(response)['error'])
        self.assertNotIn('Never-Return-This-Password!', body)
        self.assertNotIn('secret_data', body)
        self.assertEqual(response_body(response)['data'][0]['protocol'], 'rdp')

    def test_ticket_is_hashed_one_time_and_launch_payload_is_short_lived(self):
        issued = self.issue()
        issued_data = response_body(issued)['data']
        parsed = urlsplit(issued_data['launch_url'])
        ticket = parse_qs(parsed.query)['ticket'][0]
        session = RemoteAccessSession.objects.get(pk=issued_data['id'])

        self.assertNotEqual(session.ticket_hash, ticket)
        self.assertEqual(session.ticket_hash, ticket_digest(ticket))
        self.assertNotIn(ticket, json.dumps(session.to_view(), default=str))
        self.assertNotIn('Never-Return-This-Password!', issued.content.decode('utf-8'))
        self.assertEqual(AuditEvent.objects.get(result='issued').resource_id, str(self.host.id))

        launched = self.launch(issued_data['launch_url'])
        self.assertEqual(launched.status_code, 302)
        self.assertEqual(launched['Cache-Control'], 'no-store, max-age=0')
        guacamole_query = parse_qs(urlsplit(launched['Location']).query)
        document = decrypt_guacamole_data(guacamole_query['data'][0])
        connection = document['connections']['Windows 01']

        self.assertEqual(document['username'], 'ops-platform-user-%s' % self.admin.id)
        self.assertGreater(document['expires'], int(datetime.now().timestamp() * 1000))
        self.assertLessEqual(
            document['expires'], int((datetime.now() + timedelta(seconds=61)).timestamp() * 1000)
        )
        self.assertEqual(connection['protocol'], 'rdp')
        self.assertEqual(connection['parameters']['hostname'], self.host.hostname)
        self.assertEqual(connection['parameters']['username'], 'Administrator')
        self.assertEqual(connection['parameters']['password'], 'Never-Return-This-Password!')
        self.assertEqual(connection['parameters']['ignore-cert'], 'false')

        session.refresh_from_db()
        self.assertEqual(session.status, 'launched')
        replayed = self.launch(issued_data['launch_url'])
        self.assertEqual(replayed.status_code, 403)
        session.refresh_from_db()
        self.assertEqual(session.status, 'launched')
        self.assertEqual(AuditEvent.objects.filter(result='launched').count(), 1)

        audit_dump = json.dumps(
            [item.to_view() for item in AuditEvent.objects.all()], default=str
        )
        self.assertNotIn('Never-Return-This-Password!', audit_dump)
        self.assertNotIn(ticket, audit_dump)

    def test_wrong_ticket_does_not_consume_valid_ticket(self):
        issued_data = response_body(self.issue())['data']
        parsed = urlsplit(issued_data['launch_url'])
        wrong_url = '%s?ticket=wrong-ticket' % parsed.path

        self.assertEqual(self.launch(wrong_url).status_code, 403)
        session = RemoteAccessSession.objects.get(pk=issued_data['id'])
        self.assertEqual(session.status, 'issued')
        self.assertEqual(self.launch(issued_data['launch_url']).status_code, 302)

    def test_permission_is_rechecked_after_ticket_issue(self):
        self.grant_remote(self.user)
        issued = self.issue(self.user)
        issued_data = response_body(issued)['data']
        self.assertTrue(issued_data)

        self.grant_remote(self.user, effect='deny')
        launched = self.launch(issued_data['launch_url'])
        self.assertEqual(launched.status_code, 403)
        session = RemoteAccessSession.objects.get(pk=issued_data['id'])
        self.assertEqual(session.status, 'failed')
        self.assertIn('没有该主机', session.failure_reason)
        self.assertEqual(AuditEvent.objects.filter(result='denied').count(), 1)

    def test_endpoint_identity_change_invalidates_existing_ticket(self):
        issued_data = response_body(self.issue())['data']
        replacement_credential = Credential.create_with_secret(
            'Replacement-Password!',
            name='replacement-password',
            type='password',
            created_by=self.admin,
        )
        replacement_identity = Identity.objects.create(
            name='replacement-admin',
            protocol='rdp',
            username='OtherAdministrator',
            credential=replacement_credential,
            created_by=self.admin,
        )
        AssetIdentityBinding.bind(
            self.host, replacement_identity, self.admin, is_default=True
        )
        self.endpoint.identity = replacement_identity
        self.endpoint.save(update_fields=('identity',))

        launched = self.launch(issued_data['launch_url'])
        self.assertEqual(launched.status_code, 403)
        session = RemoteAccessSession.objects.get(pk=issued_data['id'])
        self.assertEqual(session.status, 'failed')
        self.assertIn('配置已变更', session.failure_reason)

    def test_expired_ticket_fails_closed(self):
        issued_data = response_body(self.issue())['data']
        RemoteAccessSession.objects.filter(pk=issued_data['id']).update(
            expires_at=datetime.now() - timedelta(seconds=1)
        )

        launched = self.launch(issued_data['launch_url'])
        self.assertEqual(launched.status_code, 403)
        session = RemoteAccessSession.objects.get(pk=issued_data['id'])
        self.assertEqual(session.status, 'expired')
        self.assertEqual(AuditEvent.objects.filter(result='expired').count(), 1)

    def test_cleanup_expires_unused_tickets_with_audit(self):
        issued_data = response_body(self.issue())['data']
        expired_at = datetime.now() - timedelta(seconds=1)
        RemoteAccessSession.objects.filter(pk=issued_data['id']).update(expires_at=expired_at)

        result = expire_remote_access_sessions(now=datetime.now())

        self.assertEqual(result, {'eligible': 1, 'expired': 1})
        session = RemoteAccessSession.objects.get(pk=issued_data['id'])
        self.assertEqual(session.status, 'expired')
        event = AuditEvent.objects.get(result='expired')
        self.assertEqual(event.detail_data['reason'], 'ticket_expired_cleanup')

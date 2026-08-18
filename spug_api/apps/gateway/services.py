import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.db import transaction

from apps.account.utils import has_host_perm
from apps.assets.models import AssetIdentityBinding


REMOTE_PAGE_PERMISSION = 'host.console.remote'


class RemoteAccessDenied(Exception):
    pass


def ticket_digest(ticket):
    return hashlib.sha256(str(ticket).encode('utf-8')).hexdigest()


def valid_ticket_format(ticket):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{43}', str(ticket)))


def ensure_remote_access(user, endpoint):
    if not settings.SPUG_REMOTE_GATEWAY_ENABLED:
        raise RemoteAccessDenied('远程桌面网关尚未启用')
    if not user or not user.is_active:
        raise RemoteAccessDenied('用户已停用或不存在')
    if not user.has_perms((REMOTE_PAGE_PERMISSION,)):
        raise RemoteAccessDenied('缺少远程桌面页面权限')
    if not endpoint.is_active:
        raise RemoteAccessDenied('远程桌面端点已停用')
    identity = endpoint.identity
    credential = identity.credential
    if not identity.is_active or not credential.is_active:
        raise RemoteAccessDenied('远程桌面身份或凭据已停用')
    if identity.protocol != endpoint.protocol or credential.type != 'password':
        raise RemoteAccessDenied('远程桌面端点与身份配置不一致')
    if not AssetIdentityBinding.objects.filter(
            host_id=endpoint.host_id, identity_id=identity.id).exists():
        raise RemoteAccessDenied('远程桌面身份未绑定到该主机')
    action = '%s.connect' % endpoint.protocol
    if not has_host_perm(user, endpoint.host_id, action=action, identity_id=identity.id):
        raise RemoteAccessDenied('没有该主机的 %s 连接权限' % endpoint.protocol.upper())


def ensure_session_access(user, session):
    endpoint = session.endpoint
    if (
        endpoint.host_id != session.host_id
        or endpoint.identity_id != session.identity_id
        or endpoint.protocol != session.protocol
    ):
        raise RemoteAccessDenied('票据签发后远程桌面端点配置已变更')
    ensure_remote_access(user, endpoint)


def _guacamole_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def build_guacamole_document(session, now=None):
    now = now or datetime.now()
    endpoint = session.endpoint
    identity = endpoint.identity
    parameters = {
        'hostname': endpoint.host.hostname,
        'port': str(endpoint.port),
        'username': identity.username,
        'password': identity.credential.reveal_secret(),
    }
    parameters.update({
        key: _guacamole_value(value)
        for key, value in endpoint.setting_data.items()
    })
    connection_name = endpoint.display_name or '%s %s' % (
        endpoint.host.name, endpoint.protocol.upper()
    )
    return {
        'username': 'spug-user-%s' % session.requester_id,
        'expires': int((now + timedelta(seconds=settings.SPUG_GUACAMOLE_AUTH_TTL)).timestamp() * 1000),
        'connections': {
            connection_name: {
                'id': str(session.id),
                'protocol': endpoint.protocol,
                'parameters': parameters,
            },
        },
    }


def encrypt_guacamole_document(document, secret_key=None):
    secret_key = secret_key or settings.SPUG_GUACAMOLE_JSON_SECRET_KEY
    key = bytes.fromhex(secret_key)
    plaintext = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode('utf-8')
    signed = hmac.new(key, plaintext, hashlib.sha256).digest() + plaintext
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(signed) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b'\0' * 16)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode('ascii')


def build_guacamole_data(session, now=None):
    return encrypt_guacamole_document(build_guacamole_document(session, now=now))


def expire_remote_access_sessions(now=None, limit=500):
    from apps.audit.services import record_event
    from .models import RemoteAccessSession

    now = now or datetime.now()
    session_ids = list(
        RemoteAccessSession.objects.filter(
            status='issued', expires_at__lte=now
        ).order_by('expires_at').values_list('id', flat=True)[:limit]
    )
    expired = 0
    for session_id in session_ids:
        with transaction.atomic():
            session = RemoteAccessSession.objects.select_for_update().select_related(
                'requester'
            ).filter(pk=session_id, status='issued', expires_at__lte=now).first()
            if not session:
                continue
            session.status = 'expired'
            session.ended_at = now
            session.failure_reason = '启动票据已过期'
            session.save(update_fields=('status', 'ended_at', 'failure_reason'))
            record_event(
                correlation_id=session.correlation_id,
                actor=session.requester,
                action='%s.connect' % session.protocol,
                resource_type='host',
                resource_id=session.host_id,
                result='expired',
                details={
                    'session_id': str(session.id),
                    'endpoint_id': session.endpoint_id,
                    'reason': 'ticket_expired_cleanup',
                },
            )
            expired += 1
    return {'eligible': len(session_ids), 'expired': expired}

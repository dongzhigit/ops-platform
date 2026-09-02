import json
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models

from libs import ModelMixin, human_datetime
from apps.assets.models import AssetIdentityBinding


RDP_SETTING_RULES = {
    'security': {'any', 'nla', 'nla-ext', 'tls', 'rdp'},
    'ignore-cert': {True, False},
    'resize-method': {'display-update', 'reconnect'},
    'color-depth': {8, 16, 24, 32},
    'read-only': {True, False},
}
VNC_SETTING_RULES = {
    'color-depth': {8, 16, 24, 32},
    'cursor': {'remote', 'local'},
    'read-only': {True, False},
    'swap-red-blue': {True, False},
}


def validate_endpoint_settings(protocol, value):
    if not isinstance(value, dict):
        raise ValidationError('远程桌面参数必须是 JSON 对象')
    rules = RDP_SETTING_RULES if protocol == 'rdp' else VNC_SETTING_RULES
    unsupported = set(value).difference(rules)
    if unsupported:
        raise ValidationError('不支持的远程桌面参数: %s' % ', '.join(sorted(unsupported)))
    for key, item in value.items():
        if item not in rules[key]:
            raise ValidationError('远程桌面参数 %s 的值不受支持' % key)
    return value


class RemoteAccessEndpoint(models.Model, ModelMixin):
    PROTOCOLS = (('rdp', 'RDP'), ('vnc', 'VNC'))

    host = models.ForeignKey('host.Host', models.CASCADE, related_name='remote_access_endpoints')
    protocol = models.CharField(max_length=10, choices=PROTOCOLS)
    port = models.PositiveIntegerField()
    identity = models.ForeignKey(
        'assets.Identity', models.PROTECT, related_name='remote_access_endpoints'
    )
    display_name = models.CharField(max_length=100)
    settings = models.TextField(default='{}')
    is_active = models.BooleanField(default=True)
    created_at = models.CharField(max_length=20, default=human_datetime)
    updated_at = models.CharField(max_length=20, default=human_datetime)
    created_by = models.ForeignKey('account.User', models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )

    @property
    def setting_data(self):
        value = json.loads(self.settings or '{}')
        return validate_endpoint_settings(self.protocol, value)

    def clean(self):
        if not 1 <= self.port <= 65535:
            raise ValidationError('远程桌面端口必须在 1 到 65535 之间')
        if self.identity_id:
            if self.identity.protocol != self.protocol:
                raise ValidationError('远程桌面端点与身份协议不一致')
            if self.identity.credential.type != 'password':
                raise ValidationError('RDP/VNC 端点必须使用密码凭据')
            if not self.identity.is_active or not self.identity.credential.is_active:
                raise ValidationError('远程桌面身份或凭据已停用')
            if not AssetIdentityBinding.objects.filter(
                    host_id=self.host_id, identity_id=self.identity_id).exists():
                raise ValidationError('远程桌面身份尚未绑定到该主机')
        try:
            value = json.loads(self.settings or '{}')
        except (TypeError, ValueError):
            raise ValidationError('远程桌面参数必须是有效 JSON')
        validate_endpoint_settings(self.protocol, value)

    def to_view(self):
        return {
            'id': self.id,
            'host': {
                'id': self.host_id,
                'name': self.host.name,
                'hostname': self.host.hostname,
            },
            'protocol': self.protocol,
            'port': self.port,
            'identity': {
                'id': self.identity_id,
                'name': self.identity.name,
                'username': self.identity.username,
            },
            'display_name': self.display_name,
            'settings': self.setting_data,
            'is_active': self.is_active,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'remote_access_endpoints'
        ordering = ('host_id', 'protocol')
        unique_together = ('host', 'protocol')


class RemoteAccessSession(models.Model, ModelMixin):
    STATUSES = (
        ('issued', 'Issued'),
        ('launched', 'Launched'),
        ('closed', 'Closed'),
        ('expired', 'Expired'),
        ('failed', 'Failed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requester = models.ForeignKey(
        'account.User', models.PROTECT, related_name='remote_access_sessions'
    )
    endpoint = models.ForeignKey(
        RemoteAccessEndpoint, models.PROTECT, related_name='sessions'
    )
    host = models.ForeignKey('host.Host', models.PROTECT, related_name='+')
    identity = models.ForeignKey('assets.Identity', models.PROTECT, related_name='+')
    protocol = models.CharField(max_length=10, choices=RemoteAccessEndpoint.PROTOCOLS)
    ticket_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=STATUSES, default='issued', db_index=True)
    issued_at = models.DateTimeField(default=datetime.now, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    launched_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_ip = models.CharField(max_length=64, null=True, blank=True)
    failure_reason = models.CharField(max_length=255, null=True, blank=True)

    def to_view(self):
        return {
            'id': str(self.id),
            'requester_id': self.requester_id,
            'endpoint_id': self.endpoint_id,
            'host': {'id': self.host_id, 'name': self.host.name},
            'identity_id': self.identity_id,
            'protocol': self.protocol,
            'status': self.status,
            'issued_at': self.issued_at,
            'expires_at': self.expires_at,
            'launched_at': self.launched_at,
            'ended_at': self.ended_at,
            'correlation_id': str(self.correlation_id),
            'failure_reason': self.failure_reason,
        }

    class Meta:
        db_table = 'remote_access_sessions'
        ordering = ('-issued_at',)
        index_together = (
            ('requester', 'status'),
            ('host', 'status'),
        )

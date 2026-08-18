import json
import re
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models

from libs import ModelMixin, human_datetime


class MetricTarget(models.Model, ModelMixin):
    SCHEMES = (('http', 'HTTP'), ('https', 'HTTPS'))

    host = models.OneToOneField(
        'host.Host', models.CASCADE, related_name='metric_target'
    )
    exporter_address = models.CharField(max_length=255)
    exporter_port = models.PositiveIntegerField(default=9100)
    scheme = models.CharField(max_length=8, choices=SCHEMES, default='http')
    metrics_path = models.CharField(max_length=255, default='/metrics')
    notify_grp = models.TextField(default='[]')
    notify_mode = models.TextField(default='[]')
    is_active = models.BooleanField(default=True)
    created_at = models.CharField(max_length=20, default=human_datetime)
    updated_at = models.CharField(max_length=20, default=human_datetime)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )

    @property
    def notify_group_ids(self):
        return json.loads(self.notify_grp or '[]')

    @property
    def notify_modes(self):
        return json.loads(self.notify_mode or '[]')

    def clean(self):
        if not 1 <= self.exporter_port <= 65535:
            raise ValidationError('Exporter 端口必须在 1 到 65535 之间')
        if not self.metrics_path.startswith('/') or '?' in self.metrics_path or '#' in self.metrics_path:
            raise ValidationError('指标路径必须是不带查询参数的绝对路径')
        if not re.match(
                r'^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,253}[A-Za-z0-9])?$',
                self.exporter_address or ''):
            raise ValidationError('Exporter 地址必须是单独的主机名或 IP 地址')
        for field_name, value in (
                ('notify_grp', self.notify_grp), ('notify_mode', self.notify_mode)):
            try:
                decoded = json.loads(value or '[]')
            except (TypeError, ValueError):
                raise ValidationError('%s 必须是有效 JSON 数组' % field_name)
            if not isinstance(decoded, list):
                raise ValidationError('%s 必须是有效 JSON 数组' % field_name)

    def to_view(self):
        return {
            'id': self.id,
            'host': {
                'id': self.host_id,
                'name': self.host.name,
                'hostname': self.host.hostname,
            },
            'exporter_address': self.exporter_address,
            'exporter_port': self.exporter_port,
            'scheme': self.scheme,
            'metrics_path': self.metrics_path,
            'notify_grp': self.notify_group_ids,
            'notify_mode': self.notify_modes,
            'is_active': self.is_active,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'metric_targets'
        ordering = ('host_id',)


class AlertEvent(models.Model, ModelMixin):
    STATUSES = (('firing', '告警中'), ('resolved', '已恢复'))
    SEVERITIES = (
        ('info', '提示'), ('warning', '警告'), ('critical', '严重')
    )

    fingerprint = models.CharField(max_length=128, db_index=True)
    episode = models.PositiveIntegerField(default=1)
    correlation_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    host = models.ForeignKey(
        'host.Host', models.SET_NULL, related_name='metric_alerts',
        null=True, blank=True
    )
    alert_name = models.CharField(max_length=255)
    severity = models.CharField(
        max_length=16, choices=SEVERITIES, default='warning', db_index=True
    )
    status = models.CharField(
        max_length=16, choices=STATUSES, default='firing', db_index=True
    )
    instance = models.CharField(max_length=255, null=True, blank=True)
    summary = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    labels = models.TextField(default='{}')
    annotations = models.TextField(default='{}')
    starts_at = models.DateTimeField(default=datetime.now, db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(default=datetime.now)
    occurrence_count = models.PositiveIntegerField(default=1)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='claimed_metric_alerts',
        null=True, blank=True
    )
    silenced_until = models.DateTimeField(null=True, blank=True)
    silenced_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='silenced_metric_alerts',
        null=True, blank=True
    )
    silence_reason = models.CharField(max_length=255, null=True, blank=True)
    alertmanager_silence_id = models.CharField(
        max_length=255, null=True, blank=True
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)

    @property
    def label_data(self):
        return json.loads(self.labels or '{}')

    @property
    def annotation_data(self):
        return json.loads(self.annotations or '{}')

    @property
    def is_silenced(self):
        return bool(self.silenced_until and self.silenced_until > datetime.now())

    def to_view(self):
        return {
            'id': self.id,
            'fingerprint': self.fingerprint,
            'episode': self.episode,
            'correlation_id': str(self.correlation_id),
            'host': ({
                'id': self.host_id,
                'name': self.host.name,
                'hostname': self.host.hostname,
            } if self.host_id else None),
            'alert_name': self.alert_name,
            'severity': self.severity,
            'status': self.status,
            'instance': self.instance,
            'summary': self.summary,
            'description': self.description,
            'labels': self.label_data,
            'annotations': self.annotation_data,
            'starts_at': self.starts_at,
            'ends_at': self.ends_at,
            'last_seen_at': self.last_seen_at,
            'occurrence_count': self.occurrence_count,
            'claimed_at': self.claimed_at,
            'claimed_by': ({
                'id': self.claimed_by_id,
                'name': self.claimed_by.nickname,
            } if self.claimed_by_id else None),
            'silenced_until': self.silenced_until,
            'silenced_by': ({
                'id': self.silenced_by_id,
                'name': self.silenced_by.nickname,
            } if self.silenced_by_id else None),
            'silence_reason': self.silence_reason,
            'is_silenced': self.is_silenced,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'metric_alert_events'
        ordering = ('-last_seen_at', '-id')
        unique_together = ('fingerprint', 'episode')
        index_together = (
            ('host', 'status'), ('status', 'severity'),
        )


class AlertTransition(models.Model, ModelMixin):
    TYPES = (
        ('firing', '告警发生'),
        ('resolved', '故障恢复'),
        ('claimed', '已认领'),
        ('unclaimed', '取消认领'),
        ('silenced', '已静默'),
        ('unsilenced', '取消静默'),
    )

    alert = models.ForeignKey(
        AlertEvent, models.CASCADE, related_name='transitions'
    )
    type = models.CharField(max_length=16, choices=TYPES)
    actor = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )
    details = models.TextField(default='{}')
    created_at = models.DateTimeField(default=datetime.now, db_index=True)

    @property
    def detail_data(self):
        return json.loads(self.details or '{}')

    def to_view(self):
        return {
            'id': self.id,
            'type': self.type,
            'actor': ({
                'id': self.actor_id,
                'name': self.actor.nickname,
            } if self.actor_id else None),
            'details': self.detail_data,
            'created_at': self.created_at,
        }

    class Meta:
        db_table = 'metric_alert_transitions'
        ordering = ('created_at', 'id')

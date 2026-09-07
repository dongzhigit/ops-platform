import json
import uuid
from datetime import datetime

from django.db import models

from libs import ModelMixin


def _json(value, fallback):
    try:
        return json.loads(value or '')
    except (TypeError, ValueError):
        return fallback


class IncidentRoom(models.Model, ModelMixin):
    STATUSES = (
        ('open', '处理中'),
        ('mitigated', '已缓解'),
        ('resolved', '已恢复'),
        ('closed', '已关闭'),
    )
    SEVERITIES = (
        ('info', '提示'),
        ('warning', '警告'),
        ('critical', '严重'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(max_length=200)
    severity = models.CharField(
        max_length=16, choices=SEVERITIES, default='warning', db_index=True
    )
    status = models.CharField(
        max_length=16, choices=STATUSES, default='open', db_index=True
    )
    alert = models.ForeignKey(
        'observability.AlertEvent', models.SET_NULL,
        related_name='incident_rooms', null=True, blank=True
    )
    topology_node_ids = models.TextField(default='[]')
    ai_investigation_ids = models.TextField(default='[]')
    owner = models.ForeignKey(
        'account.User', models.SET_NULL,
        related_name='owned_incident_rooms', null=True, blank=True
    )
    postmortem_draft = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_incident_rooms'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)
    closed_at = models.DateTimeField(null=True, blank=True)

    @property
    def topology_node_id_list(self):
        value = _json(self.topology_node_ids, [])
        return value if isinstance(value, list) else []

    @property
    def ai_investigation_id_list(self):
        value = _json(self.ai_investigation_ids, [])
        return value if isinstance(value, list) else []

    def to_view(self, include_detail=False):
        data = {
            'id': str(self.id),
            'correlation_id': str(self.correlation_id),
            'title': self.title,
            'severity': self.severity,
            'status': self.status,
            'alert_id': self.alert_id,
            'alert': self.alert.to_view() if self.alert_id else None,
            'topology_node_ids': self.topology_node_id_list,
            'ai_investigation_ids': self.ai_investigation_id_list,
            'owner': ({
                'id': self.owner_id,
                'name': self.owner.nickname or self.owner.username,
            } if self.owner_id else None),
            'created_by': {
                'id': self.created_by_id,
                'name': self.created_by.nickname or self.created_by.username,
            },
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'closed_at': self.closed_at,
        }
        if include_detail:
            data['postmortem_draft'] = self.postmortem_draft or ''
        return data

    class Meta:
        db_table = 'incident_rooms'
        ordering = ('-updated_at', '-created_at')
        index_together = (
            ('status', 'severity'),
            ('owner', 'status'),
        )


class IncidentTimeline(models.Model, ModelMixin):
    TYPES = (
        ('created', '创建事件'),
        ('note', '人工备注'),
        ('status', '状态变更'),
        ('owner', '负责人变更'),
        ('postmortem', '复盘草稿'),
        ('ai', 'AI 调查关联'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(
        IncidentRoom, models.CASCADE, related_name='timeline'
    )
    type = models.CharField(max_length=32, choices=TYPES, db_index=True)
    message = models.TextField()
    details = models.TextField(default='{}')
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_incident_timeline'
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)

    @property
    def detail_data(self):
        value = _json(self.details, {})
        return value if isinstance(value, dict) else {}

    def to_view(self):
        return {
            'id': str(self.id),
            'room_id': str(self.room_id),
            'type': self.type,
            'message': self.message,
            'details': self.detail_data,
            'created_by': {
                'id': self.created_by_id,
                'name': self.created_by.nickname or self.created_by.username,
            },
            'created_at': self.created_at,
        }

    class Meta:
        db_table = 'incident_timeline'
        ordering = ('created_at', 'id')

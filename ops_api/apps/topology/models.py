import json
import re
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models

from libs import ModelMixin


class TopologyNode(models.Model, ModelMixin):
    TYPES = (
        ('host', '服务器'),
        ('application', '应用'),
        ('service', '服务'),
        ('process', '进程'),
        ('port', '监听端口'),
        ('database', '数据库'),
        ('middleware', '中间件'),
        ('external', '外部接口'),
        ('monitor_target', '监控目标'),
    )
    SOURCE_TYPES = (
        ('manual', '人工维护'),
        ('host', '主机资产'),
        ('application', '应用'),
        ('service', '配置服务'),
        ('metric_target', '监控采集目标'),
        ('runtime', '运行态快照'),
        ('external', '外部系统'),
    )
    STATUSES = (
        ('healthy', '正常'),
        ('info', '提示'),
        ('warning', '警告'),
        ('critical', '严重'),
        ('offline', '离线'),
        ('disabled', '停用'),
        ('unknown', '未知'),
    )
    STATUS_SOURCES = (
        ('manual', '人工维护'),
        ('alert', '告警事件'),
        ('metrics', '监控指标'),
        ('probe', '探测结果'),
        ('runtime', '运行态快照'),
        ('inherited', '拓扑继承'),
        ('unknown', '未知'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=128, unique=True)
    type = models.CharField(max_length=32, choices=TYPES)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, null=True, blank=True)
    source_type = models.CharField(
        max_length=32, choices=SOURCE_TYPES, default='manual'
    )
    source_id = models.CharField(max_length=64, null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUSES, default='unknown', db_index=True
    )
    status_source = models.CharField(
        max_length=16, choices=STATUS_SOURCES, default='manual'
    )
    metadata = models.TextField(default='{}')
    position = models.TextField(default='{}')
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_topology_nodes'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )

    @property
    def metadata_data(self):
        return json.loads(self.metadata or '{}')

    @property
    def position_data(self):
        return json.loads(self.position or '{}')

    def clean(self):
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$', self.key or ''):
            raise ValidationError('拓扑节点标识只能包含字母、数字、下划线、点、冒号和中划线')
        if self.source_type in (
                'host', 'application', 'service', 'metric_target') and not self.source_id:
            raise ValidationError('关联已有对象的拓扑节点必须记录来源对象 ID')
        for field_name in ('metadata', 'position'):
            value = getattr(self, field_name)
            try:
                decoded = json.loads(value or '{}')
            except (TypeError, ValueError):
                raise ValidationError('%s 必须是有效 JSON 对象' % field_name)
            if not isinstance(decoded, dict):
                raise ValidationError('%s 必须是有效 JSON 对象' % field_name)

    def to_view(self):
        return {
            'id': str(self.id),
            'key': self.key,
            'type': self.type,
            'name': self.name,
            'description': self.description or '',
            'source': {
                'type': self.source_type,
                'id': self.source_id,
            },
            'status': self.status,
            'status_source': self.status_source,
            'metadata': self.metadata_data,
            'position': self.position_data,
            'is_active': self.is_active,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'topology_nodes'
        ordering = ('type', 'name', 'id')
        index_together = (
            ('source_type', 'source_id'),
            ('type', 'status'),
        )


class TopologyEdge(models.Model, ModelMixin):
    TYPES = (
        ('deployed_on', '部署于'),
        ('listens_on', '监听'),
        ('calls', '调用'),
        ('depends_on', '依赖'),
        ('collects', '采集'),
        ('alerts_on', '告警关联'),
        ('release_affects', '发布关联'),
    )
    STATUSES = TopologyNode.STATUSES
    STATUS_SOURCES = TopologyNode.STATUS_SOURCES

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        TopologyNode, models.CASCADE, related_name='outgoing_edges'
    )
    target = models.ForeignKey(
        TopologyNode, models.CASCADE, related_name='incoming_edges'
    )
    type = models.CharField(max_length=32, choices=TYPES)
    label = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUSES, default='unknown', db_index=True
    )
    status_source = models.CharField(
        max_length=16, choices=STATUS_SOURCES, default='manual'
    )
    probed = models.BooleanField(default=False)
    metadata = models.TextField(default='{}')
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_topology_edges'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )

    @property
    def metadata_data(self):
        return json.loads(self.metadata or '{}')

    def clean(self):
        if self.source_id and self.target_id and self.source_id == self.target_id:
            raise ValidationError('拓扑连接不能指向自身')
        try:
            decoded = json.loads(self.metadata or '{}')
        except (TypeError, ValueError):
            raise ValidationError('metadata 必须是有效 JSON 对象')
        if not isinstance(decoded, dict):
            raise ValidationError('metadata 必须是有效 JSON 对象')

    def to_view(self):
        return {
            'id': str(self.id),
            'source': str(self.source_id),
            'target': str(self.target_id),
            'type': self.type,
            'label': self.label or '',
            'status': self.status,
            'status_source': self.status_source,
            'probed': self.probed,
            'metadata': self.metadata_data,
            'is_active': self.is_active,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'topology_edges'
        ordering = ('source_id', 'target_id', 'type')
        unique_together = ('source', 'target', 'type')
        index_together = (
            ('type', 'status'),
        )

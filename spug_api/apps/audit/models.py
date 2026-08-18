import json
import uuid
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import models

from libs import ModelMixin


def approval_request_expiry():
    return datetime.now() + timedelta(hours=24)


class ApprovalRequest(models.Model, ModelMixin):
    RISK_LEVELS = (
        ('low', '低风险'),
        ('medium', '中风险'),
        ('high', '高风险'),
        ('critical', '严重风险'),
    )
    STATUSES = (
        ('pending', '待审批'),
        ('approved', '已批准'),
        ('rejected', '已驳回'),
        ('cancelled', '已撤销'),
        ('consumed', '已使用'),
        ('expired', '已过期'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requester = models.ForeignKey('account.User', models.PROTECT, related_name='approval_requests')
    action = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=64)
    resource_ids = models.TextField(default='[]')
    risk_level = models.CharField(max_length=16, choices=RISK_LEVELS)
    summary = models.CharField(max_length=255)
    rollback_plan = models.TextField(null=True, blank=True)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=STATUSES, default='pending', db_index=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=approval_request_expiry)
    approved_until = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='approval_decisions', null=True, blank=True
    )
    decision_comment = models.CharField(max_length=255, null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    execution_ref = models.CharField(max_length=100, null=True, blank=True, db_index=True)

    @property
    def resource_id_list(self):
        try:
            value = json.loads(self.resource_ids)
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

    def refresh_expiry_status(self, at=None):
        at = at or datetime.now()
        deadline = self.approved_until if self.status == 'approved' else self.expires_at
        if self.status in ('pending', 'approved') and deadline and deadline <= at:
            self.status = 'expired'
            self.save(update_fields=('status',))
            return True
        return False

    def to_view(self):
        return {
            'id': str(self.id),
            'correlation_id': str(self.correlation_id),
            'requester_id': self.requester_id,
            'requester_name': self.requester.nickname if self.requester_id else None,
            'action': self.action,
            'resource_type': self.resource_type,
            'resource_ids': self.resource_id_list,
            'risk_level': self.risk_level,
            'summary': self.summary,
            'rollback_plan': self.rollback_plan,
            'payload_hash': self.payload_hash,
            'status': self.status,
            'requested_at': self.requested_at,
            'expires_at': self.expires_at,
            'approved_until': self.approved_until,
            'decided_at': self.decided_at,
            'decided_by_id': self.decided_by_id,
            'decided_by_name': self.decided_by.nickname if self.decided_by_id else None,
            'decision_comment': self.decision_comment,
            'consumed_at': self.consumed_at,
            'execution_ref': self.execution_ref,
        }

    def to_dict(self, *args, **kwargs):
        return self.to_view()

    class Meta:
        db_table = 'approval_requests'
        ordering = ('-requested_at',)


class AuditChain(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_sequence = models.BigIntegerField(default=0)
    last_hash = models.CharField(max_length=64, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'audit_chain'


class AppendOnlyAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('审计事件只允许追加，不能更新')

    def delete(self):
        raise ValidationError('审计事件只允许追加，不能删除')


class AuditEvent(models.Model, ModelMixin):
    RESULTS = (
        ('requested', '已申请'),
        ('approved', '已批准'),
        ('rejected', '已驳回'),
        ('cancelled', '已撤销'),
        ('denied', '已拒绝'),
        ('queued', '已入队'),
        ('issued', '已签发'),
        ('started', '已开始'),
        ('launched', '已启动'),
        ('closed', '已关闭'),
        ('expired', '已过期'),
        ('succeeded', '成功'),
        ('failed', '失败'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sequence = models.BigIntegerField(unique=True)
    correlation_id = models.UUIDField(db_index=True)
    actor = models.ForeignKey('account.User', models.PROTECT, related_name='+', null=True, blank=True)
    actor_name = models.CharField(max_length=100, null=True, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=64)
    resource_id = models.CharField(max_length=100, null=True, blank=True)
    result = models.CharField(max_length=16, choices=RESULTS, db_index=True)
    source_ip = models.CharField(max_length=64, null=True, blank=True)
    request_method = models.CharField(max_length=12, null=True, blank=True)
    request_path = models.CharField(max_length=255, null=True, blank=True)
    details = models.TextField(default='{}')
    previous_hash = models.CharField(max_length=64, default='')
    event_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=datetime.now, db_index=True, editable=False)

    objects = models.Manager.from_queryset(AppendOnlyAuditQuerySet)()

    @property
    def detail_data(self):
        try:
            value = json.loads(self.details)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {'value': value}

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('审计事件只允许追加，不能更新')
        return super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        raise ValidationError('审计事件只允许追加，不能删除')

    def to_view(self):
        return {
            'id': str(self.id),
            'sequence': self.sequence,
            'correlation_id': str(self.correlation_id),
            'actor_id': self.actor_id,
            'actor_name': self.actor_name,
            'action': self.action,
            'resource_type': self.resource_type,
            'resource_id': self.resource_id,
            'result': self.result,
            'source_ip': self.source_ip,
            'request_method': self.request_method,
            'request_path': self.request_path,
            'details': self.detail_data,
            'previous_hash': self.previous_hash,
            'event_hash': self.event_hash,
            'created_at': self.created_at,
        }

    def to_dict(self, *args, **kwargs):
        return self.to_view()

    class Meta:
        db_table = 'audit_events'
        ordering = ('-sequence',)

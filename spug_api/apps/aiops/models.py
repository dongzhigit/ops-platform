import json
import uuid
from datetime import datetime

from django.db import models

from libs import ModelMixin


def _json(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class AIInvestigation(models.Model, ModelMixin):
    STATUSES = (
        ('running', '调查中'),
        ('completed', '已完成'),
        ('failed', '失败'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='ai_investigations'
    )
    question = models.TextField()
    requested_host_ids = models.TextField(default='[]')
    alert = models.ForeignKey(
        'observability.AlertEvent', models.SET_NULL,
        related_name='ai_investigations', null=True, blank=True
    )
    status = models.CharField(
        max_length=16, choices=STATUSES, default='running', db_index=True
    )
    provider = models.CharField(max_length=32, default='openai-compatible')
    model = models.CharField(max_length=100)
    prompt_version = models.CharField(max_length=32)
    evidence = models.TextField(default='[]')
    citations = models.TextField(default='[]')
    result = models.TextField(null=True, blank=True)
    error = models.CharField(max_length=500, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    @property
    def host_id_list(self):
        value = _json(self.requested_host_ids, [])
        return value if isinstance(value, list) else []

    @property
    def evidence_data(self):
        value = _json(self.evidence, [])
        return value if isinstance(value, list) else []

    @property
    def citation_list(self):
        value = _json(self.citations, [])
        return value if isinstance(value, list) else []

    @property
    def result_data(self):
        if not self.result:
            return None
        value = _json(self.result, None)
        return value if isinstance(value, dict) else None

    def to_view(self, include_detail=False):
        data = {
            'id': str(self.id),
            'correlation_id': str(self.correlation_id),
            'created_by': {'id': self.created_by_id, 'name': self.created_by.nickname},
            'question': self.question,
            'requested_host_ids': self.host_id_list,
            'alert_id': self.alert_id,
            'status': self.status,
            'provider': self.provider,
            'model': self.model,
            'prompt_version': self.prompt_version,
            'error': self.error,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'created_at': self.created_at,
            'completed_at': self.completed_at,
            'read_only': True,
        }
        if include_detail:
            data['evidence'] = self.evidence_data
            data['citations'] = self.citation_list
            data['result'] = self.result_data
            data['action_plan'] = (
                self.action_plan.to_view() if hasattr(self, 'action_plan') else None
            )
        return data

    class Meta:
        db_table = 'ai_investigations'
        ordering = ('-created_at',)
        index_together = (('created_by', 'status'),)


class AIActionPlan(models.Model, ModelMixin):
    RISK_LEVELS = (
        ('low', '低风险'),
        ('medium', '中风险'),
        ('high', '高风险'),
        ('critical', '严重风险'),
    )
    STATUSES = (
        ('proposed', '仅建议'),
        ('dismissed', '已忽略'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    investigation = models.OneToOneField(
        AIInvestigation, models.CASCADE, related_name='action_plan'
    )
    title = models.CharField(max_length=200)
    summary = models.TextField()
    risk_level = models.CharField(max_length=16, choices=RISK_LEVELS)
    steps = models.TextField(default='[]')
    rollback_plan = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUSES, default='proposed', db_index=True
    )
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='ai_action_plans'
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)

    @property
    def step_list(self):
        value = _json(self.steps, [])
        return value if isinstance(value, list) else []

    def to_view(self):
        return {
            'id': str(self.id),
            'investigation_id': str(self.investigation_id),
            'title': self.title,
            'summary': self.summary,
            'risk_level': self.risk_level,
            'steps': self.step_list,
            'rollback_plan': self.rollback_plan,
            'status': self.status,
            'created_at': self.created_at,
            'executable': False,
        }

    class Meta:
        db_table = 'ai_action_plans'
        ordering = ('-created_at',)

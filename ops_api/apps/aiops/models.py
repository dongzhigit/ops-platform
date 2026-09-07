import json
import uuid
from datetime import datetime

from django.conf import settings
from django.db import models

from apps.assets.encryption import get_default_cipher
from libs import ModelMixin


def _json(value, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class AIProviderConfig(models.Model, ModelMixin):
    API_FORMATS = (
        ('openai', 'OpenAI Chat Completions'),
        ('anthropic', 'Anthropic Messages'),
    )

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    api_format = models.CharField(max_length=16, choices=API_FORMATS, default='openai')
    base_url = models.CharField(max_length=500)
    model = models.CharField(max_length=100, blank=True)
    key_id = models.CharField(max_length=50, default='primary')
    api_key_data = models.TextField(null=True, blank=True)
    json_mode = models.BooleanField(default=True)
    request_timeout = models.PositiveIntegerField(default=30)
    max_hosts = models.PositiveIntegerField(default=20)
    knowledge_limit = models.PositiveIntegerField(default=8)
    max_output_tokens = models.PositiveIntegerField(default=2000)
    max_response_bytes = models.PositiveIntegerField(default=1048576)
    rate_limit_per_minute = models.PositiveIntegerField(default=5)
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='updated_ai_provider_configs'
    )
    created_at = models.DateTimeField(default=datetime.now)
    updated_at = models.DateTimeField(default=datetime.now)

    @property
    def has_api_key(self):
        return bool(self.api_key_data)

    def set_api_key(self, value, key_id=None):
        value = str(value or '').strip()
        if not value:
            raise ValueError('模型 API Key 不能为空')
        if len(value.encode('utf-8')) > 8192:
            raise ValueError('模型 API Key 不能超过 8192 字节')
        self.key_id = key_id or settings.SPUG_CREDENTIAL_PRIMARY_KEY_ID
        self.api_key_data = get_default_cipher(self.key_id).encrypt(
            value, 'ai-provider:%s' % self.id
        )

    def reveal_api_key(self):
        if not self.api_key_data:
            return ''
        value = get_default_cipher(self.key_id).decrypt(
            self.api_key_data, 'ai-provider:%s' % self.id
        )
        return value.decode('utf-8')

    class Meta:
        db_table = 'ai_provider_config'


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


class AIRemediationProposal(models.Model, ModelMixin):
    STATUSES = (
        ('draft', '待提交'),
        ('approval_pending', '待审批'),
        ('approved', '已批准'),
        ('rejected', '已驳回'),
        ('cancelled', '已取消'),
        ('expired', '已过期'),
        ('executing', '执行登记中'),
        ('succeeded', '已验证成功'),
        ('failed', '验证失败'),
        ('rolled_back', '已回滚'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action_plan = models.ForeignKey(
        AIActionPlan, models.PROTECT, related_name='remediation_proposals'
    )
    incident_room = models.ForeignKey(
        'incident.IncidentRoom', models.SET_NULL,
        related_name='remediation_proposals', null=True, blank=True
    )
    approval = models.ForeignKey(
        'audit.ApprovalRequest', models.SET_NULL,
        related_name='ai_remediation_proposals', null=True, blank=True
    )
    title = models.CharField(max_length=200)
    summary = models.TextField()
    proposed_action = models.CharField(max_length=64)
    target_refs = models.TextField(default='[]')
    risk_level = models.CharField(max_length=16, choices=AIActionPlan.RISK_LEVELS)
    validation_plan = models.TextField()
    rollback_plan = models.TextField(null=True, blank=True)
    citation_refs = models.TextField(default='[]')
    status = models.CharField(
        max_length=16, choices=STATUSES, default='draft', db_index=True
    )
    execution_enabled = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='ai_remediation_proposals'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)

    @property
    def target_ref_list(self):
        value = _json(self.target_refs, [])
        return value if isinstance(value, list) else []

    @property
    def citation_ref_list(self):
        value = _json(self.citation_refs, [])
        return value if isinstance(value, list) else []

    def approval_payload(self):
        return {
            'proposal_id': str(self.id),
            'action_plan_id': str(self.action_plan_id),
            'incident_room_id': str(self.incident_room_id) if self.incident_room_id else None,
            'proposed_action': self.proposed_action,
            'risk_level': self.risk_level,
            'target_refs': self.target_ref_list,
            'summary': self.summary,
            'validation_plan': self.validation_plan,
            'rollback_plan': self.rollback_plan or '',
            'citation_refs': self.citation_ref_list,
            'execution_enabled': False,
        }

    def to_view(self):
        return {
            'id': str(self.id),
            'action_plan_id': str(self.action_plan_id),
            'investigation_id': str(self.action_plan.investigation_id),
            'incident_room_id': str(self.incident_room_id) if self.incident_room_id else None,
            'approval_id': str(self.approval_id) if self.approval_id else None,
            'approval': self.approval.to_view() if self.approval_id else None,
            'title': self.title,
            'summary': self.summary,
            'proposed_action': self.proposed_action,
            'target_refs': self.target_ref_list,
            'risk_level': self.risk_level,
            'validation_plan': self.validation_plan,
            'rollback_plan': self.rollback_plan,
            'citation_refs': self.citation_ref_list,
            'status': self.status,
            'execution_enabled': False,
            'executions': [
                item.to_view()
                for item in self.executions.select_related('created_by', 'updated_by')
            ],
            'created_by': {
                'id': self.created_by_id,
                'name': self.created_by.nickname or self.created_by.username,
            },
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'ai_remediation_proposals'
        ordering = ('-updated_at', '-created_at')
        index_together = (
            ('created_by', 'status'),
            ('action_plan', 'status'),
            ('incident_room', 'status'),
        )


class AIRemediationExecution(models.Model, ModelMixin):
    STATUSES = (
        ('running', '执行登记中'),
        ('succeeded', '已验证成功'),
        ('failed', '验证失败'),
        ('rolled_back', '已回滚'),
    )
    MODES = (
        ('manual_record', '人工执行记录'),
        ('platform_reference', '平台执行引用'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(
        AIRemediationProposal, models.PROTECT, related_name='executions'
    )
    approval = models.ForeignKey(
        'audit.ApprovalRequest', models.PROTECT, related_name='ai_remediation_executions'
    )
    mode = models.CharField(max_length=32, choices=MODES, default='manual_record')
    platform_action = models.CharField(max_length=64, null=True, blank=True)
    execution_ref = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    platform_record = models.TextField(default='{}')
    execution_summary = models.TextField()
    validation_result = models.TextField(null=True, blank=True)
    rollback_result = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUSES, default='running', db_index=True
    )
    started_at = models.DateTimeField(default=datetime.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='ai_remediation_executions'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)

    def to_view(self):
        return {
            'id': str(self.id),
            'proposal_id': str(self.proposal_id),
            'approval_id': str(self.approval_id),
            'mode': self.mode,
            'platform_action': self.platform_action,
            'execution_ref': self.execution_ref,
            'platform_record': self.platform_record_data,
            'execution_summary': self.execution_summary,
            'validation_result': self.validation_result,
            'rollback_result': self.rollback_result,
            'status': self.status,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'created_by': {
                'id': self.created_by_id,
                'name': self.created_by.nickname or self.created_by.username,
            },
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @property
    def platform_record_data(self):
        value = _json(self.platform_record, {})
        return value if isinstance(value, dict) else {}

    class Meta:
        db_table = 'ai_remediation_executions'
        ordering = ('-started_at', '-created_at')
        index_together = (
            ('proposal', 'status'),
            ('created_by', 'status'),
        )

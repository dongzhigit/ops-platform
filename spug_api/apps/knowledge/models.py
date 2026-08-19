import hashlib
import json
import re
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models

from libs import ModelMixin


def _json_list(value):
    try:
        result = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    return result if isinstance(result, list) else []


class KnowledgeSpace(models.Model, ModelMixin):
    VISIBILITIES = (
        ('private', '仅成员可见'),
        ('internal', '组织内可见'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=64, unique=True)
    description = models.TextField(null=True, blank=True)
    visibility = models.CharField(
        max_length=16, choices=VISIBILITIES, default='private', db_index=True
    )
    retention_days = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_knowledge_spaces'
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now)

    def clean(self):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}', self.slug or ''):
            raise ValidationError({'slug': '空间标识只能包含小写字母、数字、下划线和连字符'})
        if self.retention_days > 3650:
            raise ValidationError({'retention_days': '保留天数不能超过 3650 天'})

    def to_view(self, role=None):
        return {
            'id': str(self.id),
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'visibility': self.visibility,
            'retention_days': self.retention_days,
            'is_active': self.is_active,
            'role': role,
            'created_by': {
                'id': self.created_by_id,
                'name': self.created_by.nickname,
            },
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'knowledge_spaces'
        ordering = ('name', 'id')


class KnowledgeMembership(models.Model, ModelMixin):
    ROLES = (
        ('viewer', '查看者'),
        ('editor', '编辑者'),
        ('manager', '管理员'),
    )

    space = models.ForeignKey(
        KnowledgeSpace, models.CASCADE, related_name='memberships'
    )
    user = models.ForeignKey(
        'account.User', models.CASCADE, related_name='knowledge_memberships'
    )
    role = models.CharField(max_length=16, choices=ROLES, default='viewer')
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_knowledge_memberships'
    )
    created_at = models.DateTimeField(default=datetime.now)

    def to_view(self):
        return {
            'id': self.id,
            'space_id': str(self.space_id),
            'user': {'id': self.user_id, 'name': self.user.nickname},
            'role': self.role,
            'created_at': self.created_at,
        }

    class Meta:
        db_table = 'knowledge_memberships'
        ordering = ('space_id', 'user__nickname')
        unique_together = ('space', 'user')


class KnowledgeDocument(models.Model, ModelMixin):
    KINDS = (
        ('article', '文档'),
        ('runbook', 'Runbook'),
        ('postmortem', '故障复盘'),
        ('asset', '资产说明'),
    )
    STATUSES = (
        ('draft', '草稿'),
        ('review', '待审核'),
        ('published', '已发布'),
        ('archived', '已归档'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(
        KnowledgeSpace, models.PROTECT, related_name='documents'
    )
    title = models.CharField(max_length=200)
    slug = models.CharField(max_length=100)
    kind = models.CharField(
        max_length=16, choices=KINDS, default='article', db_index=True
    )
    status = models.CharField(
        max_length=16, choices=STATUSES, default='draft', db_index=True
    )
    summary = models.CharField(max_length=1000, null=True, blank=True)
    content = models.TextField(default='')
    tags = models.TextField(default='[]')
    version = models.PositiveIntegerField(default=1)
    content_hash = models.CharField(max_length=64)
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='created_knowledge_documents'
    )
    updated_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='updated_knowledge_documents'
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)
    updated_at = models.DateTimeField(default=datetime.now, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='+', null=True, blank=True
    )

    @property
    def tag_list(self):
        return _json_list(self.tags)

    @property
    def citation(self):
        return 'knowledge://%s/%s?v=%s' % (
            self.space.slug, self.id, self.version
        )

    def clean(self):
        if not self.title.strip():
            raise ValidationError({'title': '文档标题不能为空'})
        if len(self.content.encode('utf-8')) > 1024 * 1024:
            raise ValidationError({'content': '单篇文档不能超过 1 MiB'})
        tags = self.tag_list
        if len(tags) > 50 or any(not isinstance(x, str) or len(x) > 50 for x in tags):
            raise ValidationError({'tags': '标签最多 50 个且每个不能超过 50 个字符'})
        digest = hashlib.sha256(self.content.encode('utf-8')).hexdigest()
        if self.content_hash and len(self.content_hash) != len(digest):
            raise ValidationError({'content_hash': '文档摘要格式错误'})

    def to_view(self, include_content=True):
        result = {
            'id': str(self.id),
            'space': {
                'id': str(self.space_id),
                'name': self.space.name,
                'slug': self.space.slug,
            },
            'title': self.title,
            'slug': self.slug,
            'kind': self.kind,
            'status': self.status,
            'summary': self.summary,
            'tags': self.tag_list,
            'version': self.version,
            'content_hash': self.content_hash,
            'citation': self.citation,
            'published_at': self.published_at,
            'created_by': {'id': self.created_by_id, 'name': self.created_by.nickname},
            'updated_by': {'id': self.updated_by_id, 'name': self.updated_by.nickname},
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }
        if include_content:
            result['content'] = self.content
        return result

    class Meta:
        db_table = 'knowledge_documents'
        ordering = ('-updated_at', '-created_at')
        unique_together = ('space', 'slug')
        index_together = (('space', 'status'), ('space', 'kind'))


class KnowledgeDocumentRevision(models.Model, ModelMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument, models.CASCADE, related_name='revisions'
    )
    version = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=KnowledgeDocument.KINDS)
    status = models.CharField(max_length=16, choices=KnowledgeDocument.STATUSES)
    summary = models.CharField(max_length=1000, null=True, blank=True)
    content = models.TextField(default='')
    tags = models.TextField(default='[]')
    content_hash = models.CharField(max_length=64)
    change_note = models.CharField(max_length=255, null=True, blank=True)
    created_by = models.ForeignKey(
        'account.User', models.PROTECT, related_name='knowledge_revisions'
    )
    created_at = models.DateTimeField(default=datetime.now, db_index=True)

    @property
    def tag_list(self):
        return _json_list(self.tags)

    def to_view(self, include_content=False):
        result = {
            'id': str(self.id),
            'document_id': str(self.document_id),
            'version': self.version,
            'title': self.title,
            'kind': self.kind,
            'status': self.status,
            'summary': self.summary,
            'tags': self.tag_list,
            'content_hash': self.content_hash,
            'change_note': self.change_note,
            'created_by': {'id': self.created_by_id, 'name': self.created_by.nickname},
            'created_at': self.created_at,
        }
        if include_content:
            result['content'] = self.content
        return result

    class Meta:
        db_table = 'knowledge_document_revisions'
        ordering = ('-version',)
        unique_together = ('document', 'version')

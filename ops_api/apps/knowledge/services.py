import hashlib
import json
import re
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from .models import (
    KnowledgeDocument,
    KnowledgeDocumentRevision,
    KnowledgeMembership,
    KnowledgeSpace,
)


ROLE_ORDER = {'viewer': 1, 'editor': 2, 'manager': 3}


def normalize_tags(tags):
    result = []
    for item in tags or []:
        value = str(item).strip()
        if value and value not in result:
            result.append(value)
    if len(result) > 50 or any(len(item) > 50 for item in result):
        raise ValueError('标签最多 50 个且每个不能超过 50 个字符')
    return result


def document_digest(title, kind, status, summary, content, tags):
    material = json.dumps({
        'title': title,
        'kind': kind,
        'status': status,
        'summary': summary or '',
        'content': content,
        'tags': tags,
    }, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(material.encode('utf-8')).hexdigest()


def accessible_spaces(user):
    spaces = KnowledgeSpace.objects.filter(is_active=True).select_related('created_by')
    if user.is_supper:
        return spaces
    return spaces.filter(
        Q(visibility='internal') | Q(created_by=user) | Q(memberships__user=user)
    ).distinct()


def space_role(user, space):
    if user.is_supper or space.created_by_id == user.id:
        return 'manager'
    membership = KnowledgeMembership.objects.filter(space=space, user=user).first()
    if membership:
        return membership.role
    if space.visibility == 'internal':
        return 'viewer'
    return None


def can_access_space(user, space, minimum='viewer'):
    role = space_role(user, space)
    return bool(role and ROLE_ORDER[role] >= ROLE_ORDER[minimum])


def accessible_documents(user, published_only=False):
    documents = KnowledgeDocument.objects.filter(
        deleted_at__isnull=True,
        space__in=accessible_spaces(user),
    ).select_related('space', 'created_by', 'updated_by')
    if published_only:
        return documents.filter(status='published')
    if user.is_supper:
        return documents
    editable = Q(space__created_by=user) | Q(
        space__memberships__user=user,
        space__memberships__role__in=('editor', 'manager'),
    )
    return documents.filter(Q(status='published') | editable).distinct()


def make_document_slug(space, title, requested=None, exclude_id=None):
    base = slugify(requested or title)[:80].strip('-')
    if not base:
        base = 'document'
    base = re.sub(r'[^a-z0-9_-]+', '-', base).strip('-') or 'document'
    candidate = base
    index = 2
    query = KnowledgeDocument.objects.filter(space=space)
    if exclude_id:
        query = query.exclude(pk=exclude_id)
    while query.filter(slug=candidate).exists():
        candidate = '%s-%s' % (base[:92], index)
        index += 1
    return candidate


def _snapshot(document, actor, change_note=None):
    return KnowledgeDocumentRevision.objects.create(
        document=document,
        version=document.version,
        title=document.title,
        kind=document.kind,
        status=document.status,
        summary=document.summary,
        content=document.content,
        tags=document.tags,
        content_hash=document.content_hash,
        change_note=(change_note or '')[:255] or None,
        created_by=actor,
    )


def create_document(*, space, actor, title, kind='article', status='draft',
                    summary=None, content='', tags=None, slug=None, change_note=None):
    tags = normalize_tags(tags)
    document = KnowledgeDocument(
        space=space,
        title=title.strip(),
        slug=make_document_slug(space, title, slug),
        kind=kind,
        status=status,
        summary=(summary or '').strip() or None,
        content=content,
        tags=json.dumps(tags, ensure_ascii=False),
        created_by=actor,
        updated_by=actor,
        published_at=datetime.now() if status == 'published' else None,
    )
    document.content_hash = document_digest(
        document.title, document.kind, document.status, document.summary,
        document.content, tags,
    )
    document.full_clean()
    with transaction.atomic():
        document.save()
        _snapshot(document, actor, change_note or '创建文档')
    return document


def update_document(*, document, actor, values, change_note=None, force_revision=False):
    with transaction.atomic():
        document = KnowledgeDocument.objects.select_for_update().select_related(
            'space', 'created_by', 'updated_by'
        ).get(pk=document.pk, deleted_at__isnull=True)
        before = document.content_hash
        if 'title' in values:
            document.title = values['title'].strip()
        if 'slug' in values:
            document.slug = make_document_slug(
                document.space, document.title, values['slug'], document.id
            )
        for field in ('kind', 'status', 'summary', 'content'):
            if field in values:
                setattr(document, field, values[field])
        if document.summary is not None:
            document.summary = document.summary.strip() or None
        tags = normalize_tags(values.get('tags', document.tag_list))
        document.tags = json.dumps(tags, ensure_ascii=False)
        digest = document_digest(
            document.title, document.kind, document.status, document.summary,
            document.content, tags,
        )
        if digest == before and not force_revision:
            return document, False
        if document.status == 'published' and not document.published_at:
            document.published_at = datetime.now()
        elif document.status != 'published':
            document.published_at = None
        document.version += 1
        document.content_hash = digest
        document.updated_by = actor
        document.updated_at = datetime.now()
        document.full_clean()
        document.save()
        _snapshot(document, actor, change_note or '更新文档')
    return document, True


def restore_revision(*, document, revision, actor, change_note=None):
    values = {
        'title': revision.title,
        'kind': revision.kind,
        'status': revision.status,
        'summary': revision.summary,
        'content': revision.content,
        'tags': revision.tag_list,
    }
    return update_document(
        document=document,
        actor=actor,
        values=values,
        change_note=change_note or '从版本 %s 恢复' % revision.version,
        force_revision=True,
    )

import json
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.views.generic import View

from apps.account.models import User
from apps.audit.services import record_event
from libs import Argument, JsonParser, auth, json_response

from .models import (
    KnowledgeDocument,
    KnowledgeDocumentRevision,
    KnowledgeMembership,
    KnowledgeSpace,
)
from .services import (
    accessible_documents,
    accessible_spaces,
    can_access_space,
    create_document,
    restore_revision,
    space_role,
    update_document,
)


def _validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(
            '%s: %s' % (field, ', '.join(messages))
            for field, messages in exc.message_dict.items()
        )
    return '; '.join(exc.messages)


def _audit(request, action, resource_type, resource_id, details=None):
    correlation_id = uuid.uuid4()
    record_event(
        correlation_id=correlation_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result='succeeded',
        actor=request.user,
        details=details or {},
        request=request,
    )
    return str(correlation_id)


def _space_view(space, user):
    result = space.to_view(role=space_role(user, space))
    result['member_count'] = space.memberships.count()
    result['document_count'] = space.documents.filter(deleted_at__isnull=True).count()
    result['can_manage'] = can_access_space(user, space, 'manager')
    result['can_edit'] = can_access_space(user, space, 'editor')
    return result


def _document_view(document, user, include_content=True):
    result = document.to_view(include_content=include_content)
    role = space_role(user, document.space)
    result['space_role'] = role
    result['can_edit'] = bool(
        can_access_space(user, document.space, 'editor')
        and user.has_perms(['knowledge.document.edit'])
    )
    result['can_publish'] = bool(
        can_access_space(user, document.space, 'editor')
        and user.has_perms(['knowledge.document.publish'])
    )
    result['can_delete'] = bool(
        can_access_space(user, document.space, 'manager')
        and user.has_perms(['knowledge.document.edit'])
    )
    return result


class KnowledgeSpaceView(View):
    @auth('knowledge.space.view|knowledge.document.view')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        spaces = accessible_spaces(request.user)
        if form.id:
            space = spaces.filter(pk=form.id).first()
            if not space:
                return json_response(error='未找到可访问的知识空间')
            return json_response(_space_view(space, request.user))
        return json_response([_space_view(item, request.user) for item in spaces])

    @auth('knowledge.space.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument('name', handler=str.strip, help='请输入空间名称'),
            Argument('slug', handler=lambda x: x.strip().lower(), help='请输入空间标识'),
            Argument('description', type=str, required=False),
            Argument(
                'visibility', default='private',
                filter=lambda x: x in dict(KnowledgeSpace.VISIBILITIES),
                help='不支持的空间可见范围',
            ),
            Argument('retention_days', type=int, default=0),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        space = KnowledgeSpace.objects.filter(pk=form.id, is_active=True).first() if form.id else None
        if form.id and not space:
            return json_response(error='未找到指定知识空间')
        if space and not can_access_space(request.user, space, 'manager'):
            return json_response(error='只有空间管理员可以修改知识空间')
        try:
            with transaction.atomic():
                if not space:
                    space = KnowledgeSpace(
                        name=form.name,
                        slug=form.slug,
                        description=form.description,
                        visibility=form.visibility,
                        retention_days=form.retention_days,
                        created_by=request.user,
                    )
                    space.full_clean()
                    space.save()
                    KnowledgeMembership.objects.create(
                        space=space,
                        user=request.user,
                        role='manager',
                        created_by=request.user,
                    )
                    operation = 'created'
                else:
                    if form.slug != space.slug and space.documents.exists():
                        return json_response(
                            error='空间已有文档，为保证历史引用稳定不能修改空间标识'
                        )
                    space.name = form.name
                    space.slug = form.slug
                    space.description = form.description
                    space.visibility = form.visibility
                    space.retention_days = form.retention_days
                    space.updated_at = datetime.now()
                    space.full_clean()
                    space.save()
                    operation = 'updated'
        except (ValidationError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                return json_response(error=_validation_error(exc))
            return json_response(error='空间标识已存在')
        _audit(
            request, 'knowledge.space.write', 'knowledge_space', space.id,
            {'operation': operation, 'slug': space.slug},
        )
        return json_response(_space_view(space, request.user))

    @auth('knowledge.space.manage')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, help='请指定知识空间'),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        space = KnowledgeSpace.objects.filter(pk=form.id, is_active=True).first()
        if not space:
            return json_response(error='未找到指定知识空间')
        if not can_access_space(request.user, space, 'manager'):
            return json_response(error='只有空间管理员可以停用知识空间')
        if space.documents.filter(deleted_at__isnull=True).exists():
            return json_response(error='空间仍包含文档，请先归档并删除文档')
        space.is_active = False
        space.updated_at = datetime.now()
        space.save(update_fields=('is_active', 'updated_at'))
        _audit(
            request, 'knowledge.space.write', 'knowledge_space', space.id,
            {'operation': 'disabled', 'slug': space.slug},
        )
        return json_response()


class KnowledgeMembershipView(View):
    @auth('knowledge.space.manage')
    def get(self, request, space_id):
        space = KnowledgeSpace.objects.filter(pk=space_id, is_active=True).first()
        if not space or not can_access_space(request.user, space, 'manager'):
            return json_response(error='只有空间管理员可以查看成员')
        members = space.memberships.select_related('user')
        return json_response([item.to_view() for item in members])

    @auth('knowledge.space.manage')
    def post(self, request, space_id):
        form, error = JsonParser(
            Argument('user_id', type=int, help='请选择用户'),
            Argument(
                'role', filter=lambda x: x in dict(KnowledgeMembership.ROLES),
                help='不支持的空间角色',
            ),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        space = KnowledgeSpace.objects.filter(pk=space_id, is_active=True).first()
        if not space or not can_access_space(request.user, space, 'manager'):
            return json_response(error='只有空间管理员可以管理成员')
        user = User.objects.filter(pk=form.user_id, is_active=True, deleted_at__isnull=True).first()
        if not user:
            return json_response(error='未找到有效用户')
        if user.id == space.created_by_id and form.role != 'manager':
            return json_response(error='空间创建人必须保留管理员角色')
        membership, created = KnowledgeMembership.objects.get_or_create(
            space=space,
            user=user,
            defaults={'role': form.role, 'created_by': request.user},
        )
        if not created and membership.role != form.role:
            membership.role = form.role
            membership.save(update_fields=('role',))
        _audit(
            request, 'knowledge.membership.write', 'knowledge_space', space.id,
            {'operation': 'upsert', 'user_id': user.id, 'role': form.role},
        )
        return json_response(membership.to_view())

    @auth('knowledge.space.manage')
    def delete(self, request, space_id):
        form, error = JsonParser(
            Argument('user_id', type=int, help='请选择用户'),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        space = KnowledgeSpace.objects.filter(pk=space_id, is_active=True).first()
        if not space or not can_access_space(request.user, space, 'manager'):
            return json_response(error='只有空间管理员可以管理成员')
        if form.user_id == space.created_by_id:
            return json_response(error='不能移除空间创建人')
        deleted, _ = KnowledgeMembership.objects.filter(
            space=space, user_id=form.user_id
        ).delete()
        if not deleted:
            return json_response(error='未找到指定成员')
        _audit(
            request, 'knowledge.membership.write', 'knowledge_space', space.id,
            {'operation': 'removed', 'user_id': form.user_id},
        )
        return json_response()


class KnowledgeSpaceUserView(View):
    @auth('knowledge.space.manage')
    def get(self, request, space_id):
        form, error = JsonParser(
            Argument(
                'q', type=str, required=False,
                filter=lambda x: len(x) <= 200,
                help='检索词不能超过 200 个字符',
            ),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        space = KnowledgeSpace.objects.filter(pk=space_id, is_active=True).first()
        if not space or not can_access_space(request.user, space, 'manager'):
            return json_response(error='只有空间管理员可以选择成员')
        users = User.objects.filter(is_active=True, deleted_at__isnull=True)
        if form.q:
            query = form.q.strip()
            users = users.filter(
                Q(username__icontains=query) | Q(nickname__icontains=query)
            )
        return json_response([
            {'id': user.id, 'username': user.username, 'nickname': user.nickname}
            for user in users.order_by('nickname', 'id')[:200]
        ])


class KnowledgeDocumentView(View):
    @auth('knowledge.document.view')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument('space_id', type=uuid.UUID, required=False),
            Argument('q', type=str, required=False),
            Argument(
                'kind', required=False,
                filter=lambda x: x in dict(KnowledgeDocument.KINDS),
                help='不支持的文档类型',
            ),
            Argument(
                'status', required=False,
                filter=lambda x: x in dict(KnowledgeDocument.STATUSES),
                help='不支持的文档状态',
            ),
            Argument('limit', type=int, default=100, filter=lambda x: 1 <= x <= 200),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        documents = accessible_documents(request.user)
        if form.id:
            document = documents.filter(pk=form.id).first()
            if not document:
                return json_response(error='未找到可访问的文档')
            return json_response(_document_view(document, request.user))
        if form.space_id:
            documents = documents.filter(space_id=form.space_id)
        if form.kind:
            documents = documents.filter(kind=form.kind)
        if form.status:
            documents = documents.filter(status=form.status)
        if form.q:
            query = form.q.strip()
            documents = documents.filter(
                Q(title__icontains=query) | Q(summary__icontains=query)
                | Q(content__icontains=query) | Q(tags__icontains=query)
            )
        documents = documents[:form.limit]
        return json_response([
            _document_view(item, request.user, include_content=False)
            for item in documents
        ])

    @auth('knowledge.document.edit')
    def post(self, request):
        form, error = JsonParser(
            Argument('space_id', type=uuid.UUID, help='请选择知识空间'),
            Argument('title', handler=str.strip, help='请输入文档标题'),
            Argument('slug', required=False),
            Argument(
                'kind', default='article',
                filter=lambda x: x in dict(KnowledgeDocument.KINDS),
                help='不支持的文档类型',
            ),
            Argument(
                'status', default='draft',
                filter=lambda x: x in dict(KnowledgeDocument.STATUSES),
                help='不支持的文档状态',
            ),
            Argument('summary', type=str, required=False),
            Argument('content', type=str, default=''),
            Argument('tags', type=list, default=[]),
            Argument('change_note', type=str, required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        space = KnowledgeSpace.objects.filter(pk=form.space_id, is_active=True).first()
        if not space or not can_access_space(request.user, space, 'editor'):
            return json_response(error='只有空间编辑者可以创建文档')
        if form.status == 'published' and not request.user.has_perms(['knowledge.document.publish']):
            return json_response(error='缺少发布文档权限')
        try:
            document = create_document(
                space=space,
                actor=request.user,
                title=form.title,
                slug=form.slug,
                kind=form.kind,
                status=form.status,
                summary=form.summary,
                content=form.content,
                tags=form.tags,
                change_note=form.change_note,
            )
        except (ValidationError, ValueError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                return json_response(error=_validation_error(exc))
            return json_response(error=str(exc) if isinstance(exc, ValueError) else '文档标识已存在')
        _audit(
            request, 'knowledge.document.write', 'knowledge_document', document.id,
            {'operation': 'created', 'space_id': str(space.id), 'version': document.version},
        )
        return json_response(_document_view(document, request.user))

    @auth('knowledge.document.edit')
    def patch(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, help='请指定文档'),
            Argument('title', handler=str.strip, required=False),
            Argument('slug', required=False),
            Argument(
                'kind', required=False,
                filter=lambda x: x in dict(KnowledgeDocument.KINDS),
                help='不支持的文档类型',
            ),
            Argument(
                'status', required=False,
                filter=lambda x: x in dict(KnowledgeDocument.STATUSES),
                help='不支持的文档状态',
            ),
            Argument('summary', type=str, required=False),
            Argument('content', type=str, required=False),
            Argument('tags', type=list, required=False),
            Argument('change_note', type=str, required=False),
        ).parse(request.body, clear=True)
        if error:
            return json_response(error=error)
        document = KnowledgeDocument.objects.select_related('space').filter(
            pk=form.id, deleted_at__isnull=True, space__is_active=True
        ).first()
        if not document or not can_access_space(request.user, document.space, 'editor'):
            return json_response(error='只有空间编辑者可以修改文档')
        if (
            document.status == 'published' or form.get('status') == 'published'
        ) and not request.user.has_perms(['knowledge.document.publish']):
            return json_response(error='已发布文档的变更需要发布权限')
        values = dict(form)
        values.pop('id')
        change_note = values.pop('change_note', None)
        try:
            document, changed = update_document(
                document=document,
                actor=request.user,
                values=values,
                change_note=change_note,
            )
        except (ValidationError, ValueError, IntegrityError) as exc:
            if isinstance(exc, ValidationError):
                return json_response(error=_validation_error(exc))
            return json_response(error=str(exc) if isinstance(exc, ValueError) else '文档标识已存在')
        if changed:
            _audit(
                request, 'knowledge.document.write', 'knowledge_document', document.id,
                {'operation': 'updated', 'version': document.version, 'status': document.status},
            )
        return json_response(_document_view(document, request.user))

    @auth('knowledge.document.edit')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, help='请指定文档'),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        document = KnowledgeDocument.objects.select_related('space').filter(
            pk=form.id, deleted_at__isnull=True
        ).first()
        if not document or not can_access_space(request.user, document.space, 'manager'):
            return json_response(error='只有空间管理员可以删除文档')
        document.deleted_at = datetime.now()
        document.deleted_by = request.user
        document.save(update_fields=('deleted_at', 'deleted_by'))
        _audit(
            request, 'knowledge.document.write', 'knowledge_document', document.id,
            {'operation': 'soft_deleted', 'version': document.version},
        )
        return json_response()


class KnowledgeDocumentRevisionView(View):
    @auth('knowledge.document.view')
    def get(self, request, document_id):
        form, error = JsonParser(
            Argument('version', type=int, required=False),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        document = accessible_documents(request.user).filter(pk=document_id).first()
        if not document:
            return json_response(error='未找到可访问的文档')
        revisions = document.revisions.select_related('created_by')
        if form.version:
            revision = revisions.filter(version=form.version).first()
            if not revision:
                return json_response(error='未找到指定版本')
            return json_response(revision.to_view(include_content=True))
        return json_response([item.to_view() for item in revisions])

    @auth('knowledge.document.edit')
    def post(self, request, document_id):
        form, error = JsonParser(
            Argument('version', type=int, help='请选择恢复版本'),
            Argument('change_note', type=str, required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        document = KnowledgeDocument.objects.select_related('space').filter(
            pk=document_id, deleted_at__isnull=True
        ).first()
        if not document or not can_access_space(request.user, document.space, 'editor'):
            return json_response(error='只有空间编辑者可以恢复文档版本')
        revision = KnowledgeDocumentRevision.objects.filter(
            document=document, version=form.version
        ).first()
        if not revision:
            return json_response(error='未找到指定版本')
        if (
            document.status == 'published' or revision.status == 'published'
        ) and not request.user.has_perms(['knowledge.document.publish']):
            return json_response(error='恢复已发布文档或版本需要发布权限')
        try:
            document, changed = restore_revision(
                document=document,
                revision=revision,
                actor=request.user,
                change_note=form.change_note,
            )
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                return json_response(error=_validation_error(exc))
            return json_response(error=str(exc))
        if changed:
            _audit(
                request, 'knowledge.document.restore', 'knowledge_document', document.id,
                {'from_version': revision.version, 'version': document.version},
            )
        return json_response(_document_view(document, request.user))


class KnowledgeDocumentSearchView(View):
    @auth('knowledge.document.view')
    def get(self, request):
        form, error = JsonParser(
            Argument('q', handler=str.strip, filter=lambda x: 2 <= len(x) <= 200,
                     help='检索词长度必须在 2 到 200 个字符之间'),
            Argument('space_id', type=uuid.UUID, required=False),
            Argument('limit', type=int, default=20, filter=lambda x: 1 <= x <= 50),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        documents = accessible_documents(request.user, published_only=True)
        if form.space_id:
            documents = documents.filter(space_id=form.space_id)
        documents = documents.filter(
            Q(title__icontains=form.q) | Q(summary__icontains=form.q)
            | Q(content__icontains=form.q) | Q(tags__icontains=form.q)
        )[:200]
        query = form.q.lower()
        results = []
        for document in documents:
            lowered_title = document.title.lower()
            lowered_content = document.content.lower()
            position = lowered_content.find(query)
            start = max(0, position - 120) if position >= 0 else 0
            excerpt = document.content[start:start + 320].strip()
            score = 10 if query in lowered_title else 1
            if document.summary and query in document.summary.lower():
                score += 4
            results.append({
                'document': _document_view(document, request.user, include_content=False),
                'excerpt': excerpt,
                'score': score,
                'citation': document.citation,
            })
        results.sort(key=lambda item: (-item['score'], item['document']['title']))
        return json_response(results[:form.limit])

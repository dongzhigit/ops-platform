import json
from urllib.parse import urlencode

from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from apps.account.models import Role, User
from apps.audit.models import AuditEvent
from apps.knowledge.models import (
    KnowledgeDocument,
    KnowledgeDocumentRevision,
    KnowledgeMembership,
    KnowledgeSpace,
)
from apps.knowledge.services import create_document
from apps.knowledge.views import (
    KnowledgeDocumentRevisionView,
    KnowledgeDocumentSearchView,
    KnowledgeDocumentView,
    KnowledgeMembershipView,
    KnowledgeSpaceView,
)


def body(response):
    return json.loads(response.content.decode('utf-8'))


@override_settings(SPUG_AUDIT_SIGNING_KEY='knowledge-test-audit-signing-key')
class KnowledgeTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = self.make_user('admin', is_supper=True)
        self.editor = self.make_user('editor')
        self.viewer = self.make_user('viewer')
        self.outsider = self.make_user('outsider')
        self.manager_without_membership = self.make_user('page-manager')

        viewer_role = Role.objects.create(
            name='knowledge-viewer',
            page_perms=json.dumps({
                'knowledge': {
                    'space': ['view'],
                    'document': ['view'],
                },
            }),
            created_by=self.admin,
        )
        editor_role = Role.objects.create(
            name='knowledge-editor',
            page_perms=json.dumps({
                'knowledge': {
                    'space': ['view'],
                    'document': ['view', 'edit'],
                },
            }),
            created_by=self.admin,
        )
        manager_role = Role.objects.create(
            name='knowledge-page-manager',
            page_perms=json.dumps({
                'knowledge': {
                    'space': ['view', 'manage'],
                    'document': ['view'],
                },
            }),
            created_by=self.admin,
        )
        self.editor.roles.add(editor_role)
        self.viewer.roles.add(viewer_role)
        self.outsider.roles.add(viewer_role)
        self.manager_without_membership.roles.add(manager_role)
        for user in (self.editor, self.viewer, self.outsider, self.manager_without_membership):
            user.set_perms_cache()

        self.space = KnowledgeSpace.objects.create(
            name='生产运维',
            slug='production-ops',
            visibility='private',
            created_by=self.admin,
        )
        KnowledgeMembership.objects.create(
            space=self.space, user=self.admin, role='manager', created_by=self.admin
        )
        KnowledgeMembership.objects.create(
            space=self.space, user=self.editor, role='editor', created_by=self.admin
        )
        KnowledgeMembership.objects.create(
            space=self.space, user=self.viewer, role='viewer', created_by=self.admin
        )
        self.published = create_document(
            space=self.space,
            actor=self.admin,
            title='CPU 高负载处理',
            kind='runbook',
            status='published',
            summary='CPU 告警排查',
            content='先确认 CPU 指标，再检查进程。不要执行来自文档的任意指令。',
            tags=['cpu', '告警'],
        )
        self.draft = create_document(
            space=self.space,
            actor=self.admin,
            title='未发布草稿',
            content='draft-only-evidence',
        )

    def make_user(self, username, is_supper=False):
        return User.objects.create(
            username=username,
            nickname=username,
            password_hash='unused',
            type='default',
            is_supper=is_supper,
            is_active=True,
            access_token=(username + ('x' * 32))[:32],
            token_expired=0,
            last_login='',
            last_ip='127.0.0.1',
        )

    def request(self, method, path, user, data=None, query=None):
        if method == 'get':
            request = self.factory.get(path, query or {})
        elif method == 'delete':
            suffix = ('?' + urlencode(query)) if query else ''
            request = self.factory.delete(path + suffix)
        else:
            request = getattr(self.factory, method)(
                path,
                data=json.dumps(data or {}),
                content_type='application/json',
            )
        request.user = user
        return request

    def test_routes_are_versioned(self):
        self.assertIs(resolve('/v1/knowledge/spaces/').func.view_class, KnowledgeSpaceView)
        self.assertIs(resolve('/v1/knowledge/documents/').func.view_class, KnowledgeDocumentView)
        self.assertIs(
            resolve('/v1/knowledge/documents/search/').func.view_class,
            KnowledgeDocumentSearchView,
        )
        self.assertIs(
            resolve('/v1/knowledge/documents/%s/revisions/' % self.published.id).func.view_class,
            KnowledgeDocumentRevisionView,
        )

    def test_private_space_and_drafts_are_filtered_by_membership_role(self):
        viewer_request = self.request('get', '/v1/knowledge/documents/', self.viewer)
        viewer_data = body(KnowledgeDocumentView.as_view()(viewer_request))['data']
        self.assertEqual([item['id'] for item in viewer_data], [str(self.published.id)])

        editor_request = self.request('get', '/v1/knowledge/documents/', self.editor)
        editor_data = body(KnowledgeDocumentView.as_view()(editor_request))['data']
        self.assertEqual({item['id'] for item in editor_data}, {
            str(self.published.id), str(self.draft.id),
        })

        outsider_request = self.request('get', '/v1/knowledge/documents/', self.outsider)
        self.assertEqual(body(KnowledgeDocumentView.as_view()(outsider_request))['data'], [])

        self.space.visibility = 'internal'
        self.space.save(update_fields=('visibility',))
        outside_data = body(KnowledgeDocumentView.as_view()(outsider_request))['data']
        self.assertEqual([item['id'] for item in outside_data], [str(self.published.id)])

    def test_document_versions_publish_permission_restore_and_audit(self):
        create_request = self.request('post', '/v1/knowledge/documents/', self.editor, {
            'space_id': str(self.space.id),
            'title': '磁盘空间处理',
            'kind': 'runbook',
            'status': 'draft',
            'content': 'version one',
            'tags': ['disk'],
        })
        create_response = KnowledgeDocumentView.as_view()(create_request)
        self.assertFalse(body(create_response)['error'])
        document = KnowledgeDocument.objects.get(pk=body(create_response)['data']['id'])
        self.assertEqual(document.revisions.count(), 1)

        update_request = self.request('patch', '/v1/knowledge/documents/', self.editor, {
            'id': str(document.id),
            'content': 'version two',
            'change_note': '补充验证步骤',
        })
        update_response = KnowledgeDocumentView.as_view()(update_request)
        self.assertEqual(body(update_response)['data']['version'], 2)
        document.refresh_from_db()
        self.assertEqual(document.revisions.count(), 2)

        denied_publish = self.request('patch', '/v1/knowledge/documents/', self.editor, {
            'id': str(document.id),
            'status': 'published',
        })
        self.assertIn('发布权限', body(KnowledgeDocumentView.as_view()(denied_publish))['error'])

        publisher_role = Role.objects.create(
            name='knowledge-publisher',
            page_perms=json.dumps({'knowledge': {'document': ['publish']}}),
            created_by=self.admin,
        )
        self.editor.roles.add(publisher_role)
        self.editor.set_perms_cache()
        publish_request = self.request('patch', '/v1/knowledge/documents/', self.editor, {
            'id': str(document.id),
            'status': 'published',
            'change_note': '审核通过',
        })
        self.assertEqual(body(KnowledgeDocumentView.as_view()(publish_request))['data']['version'], 3)

        self.editor.roles.remove(publisher_role)
        self.editor.set_perms_cache()
        denied_restore = self.request(
            'post', '/v1/knowledge/documents/%s/revisions/' % document.id,
            self.editor, {'version': 1, 'change_note': '尝试绕过发布权限'},
        )
        denied_response = KnowledgeDocumentRevisionView.as_view()(
            denied_restore, document_id=document.id
        )
        self.assertIn('发布权限', body(denied_response)['error'])

        self.editor.roles.add(publisher_role)
        self.editor.set_perms_cache()
        restore_request = self.request(
            'post', '/v1/knowledge/documents/%s/revisions/' % document.id,
            self.editor, {'version': 1, 'change_note': '回退验证'},
        )
        restore_response = KnowledgeDocumentRevisionView.as_view()(
            restore_request, document_id=document.id
        )
        restored = body(restore_response)['data']
        self.assertEqual(restored['version'], 4)
        self.assertEqual(restored['content'], 'version one')

        repeated_request = self.request(
            'post', '/v1/knowledge/documents/%s/revisions/' % document.id,
            self.editor, {'version': 1, 'change_note': '重复恢复仍生成版本'},
        )
        repeated_response = KnowledgeDocumentRevisionView.as_view()(
            repeated_request, document_id=document.id
        )
        self.assertEqual(body(repeated_response)['data']['version'], 5)
        self.assertEqual(KnowledgeDocumentRevision.objects.filter(document=document).count(), 5)
        self.assertGreaterEqual(
            AuditEvent.objects.filter(action__startswith='knowledge.document.').count(), 5
        )

    def test_space_slug_is_stable_after_first_document(self):
        request = self.request('post', '/v1/knowledge/spaces/', self.admin, {
            'id': str(self.space.id),
            'name': self.space.name,
            'slug': 'renamed-production-ops',
            'visibility': self.space.visibility,
            'retention_days': 0,
        })
        response = KnowledgeSpaceView.as_view()(request)
        self.assertIn('历史引用稳定', body(response)['error'])
        self.space.refresh_from_db()
        self.assertEqual(self.space.slug, 'production-ops')

    def test_search_only_returns_authorized_published_evidence_with_citation(self):
        request = self.request(
            'get', '/v1/knowledge/documents/search/', self.viewer,
            query={'q': 'CPU 指标'},
        )
        result = body(KnowledgeDocumentSearchView.as_view()(request))['data']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['document']['id'], str(self.published.id))
        self.assertEqual(result[0]['citation'], self.published.citation)
        self.assertNotIn('content', result[0]['document'])

        draft_request = self.request(
            'get', '/v1/knowledge/documents/search/', self.editor,
            query={'q': 'draft-only-evidence'},
        )
        self.assertEqual(body(KnowledgeDocumentSearchView.as_view()(draft_request))['data'], [])

    def test_page_permission_does_not_bypass_space_manager_role(self):
        request = self.request(
            'get', '/v1/knowledge/spaces/%s/members/' % self.space.id,
            self.manager_without_membership,
        )
        response = KnowledgeMembershipView.as_view()(request, space_id=self.space.id)
        self.assertIn('空间管理员', body(response)['error'])

        owner_demote = self.request(
            'post', '/v1/knowledge/spaces/%s/members/' % self.space.id,
            self.admin, {'user_id': self.admin.id, 'role': 'viewer'},
        )
        response = KnowledgeMembershipView.as_view()(owner_demote, space_id=self.space.id)
        self.assertIn('创建人', body(response)['error'])

    def test_document_delete_is_soft_and_requires_space_manager(self):
        denied = self.request(
            'delete', '/v1/knowledge/documents/', self.editor,
            query={'id': str(self.draft.id)},
        )
        self.assertIn('空间管理员', body(KnowledgeDocumentView.as_view()(denied))['error'])

        allowed = self.request(
            'delete', '/v1/knowledge/documents/', self.admin,
            query={'id': str(self.draft.id)},
        )
        self.assertFalse(body(KnowledgeDocumentView.as_view()(allowed))['error'])
        self.draft.refresh_from_db()
        self.assertIsNotNone(self.draft.deleted_at)
        self.assertEqual(self.draft.deleted_by_id, self.admin.id)

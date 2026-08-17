import json
import hashlib
import errno
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import resolve

from apps.account.models import User
from apps.audit.models import ApprovalRequest, AuditEvent
from apps.audit.services import (
    ApprovalError,
    assess_risk,
    authorize_operation,
    create_approval,
    decide_approval,
    record_event,
    verify_audit_chain,
)
from apps.audit.views import OperationPreviewView
from apps.exec.models import ExecHistory
from apps.exec.views import TaskView
from apps.file.models import FileTransfer
from apps.file.views import (
    ObjectView,
    UploadChunkView,
    UploadCompleteView,
    UploadSessionDetailView,
    UploadSessionView,
)
from apps.host.models import Host
from apps.schedule.models import Task
from apps.schedule.views import Schedule
from libs.ssh import SSH


class AuditApprovalTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.requester = self.make_user('requester', is_supper=True)
        self.approver = self.make_user('approver', is_supper=True)

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

    def preview_operation(self, user, operation):
        request = self.factory.post(
            '/audit/preview/',
            data=json.dumps(operation),
            content_type='application/json',
        )
        request.user = user
        response = OperationPreviewView.as_view()(request)
        return json.loads(response.content.decode('utf-8'))

    def test_server_side_risk_classification_cannot_be_downgraded(self):
        self.assertEqual(assess_risk('exec.run', {'command': 'uptime'}), 'medium')
        self.assertEqual(assess_risk('exec.run', {'command': 'sudo systemctl restart nginx'}), 'high')
        self.assertEqual(assess_risk('exec.run', {'command': 'rm -rf /srv/app'}), 'critical')
        self.assertEqual(
            assess_risk('exec.run', {'command': 'uptime', 'host_ids': list(range(50))}),
            'critical',
        )

    def test_versioned_audit_route_matches_nginx_api_rewrite(self):
        match = resolve('/v1/audit/preview/')
        self.assertIs(match.func.view_class, OperationPreviewView)

    def test_operation_preview_classifies_risk_and_finds_exact_approval(self):
        medium_operation = {
            'action': 'exec.run',
            'resource_type': 'host',
            'resource_ids': [2, 1],
            'payload': {
                'host_ids': [1, 2],
                'command': 'uptime',
                'interpreter': 'sh',
                'template_id': None,
                'params': {},
            },
        }
        medium = self.preview_operation(self.requester, medium_operation)
        self.assertFalse(medium['error'])
        self.assertEqual(medium['data']['risk_level'], 'medium')
        self.assertFalse(medium['data']['approval_required'])
        self.assertEqual(medium['data']['matching_approvals'], [])

        high_operation = dict(medium_operation)
        high_operation['payload'] = dict(
            medium_operation['payload'], command='sudo systemctl restart nginx'
        )
        high = self.preview_operation(self.requester, high_operation)
        self.assertFalse(high['error'])
        self.assertEqual(high['data']['risk_level'], 'high')
        self.assertTrue(high['data']['approval_required'])
        self.assertEqual(high['data']['matching_approvals'], [])

        approval = create_approval(
            requester=self.requester,
            action=high_operation['action'],
            resource_type=high_operation['resource_type'],
            resource_ids=high_operation['resource_ids'],
            payload=high_operation['payload'],
            summary='重启 nginx',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )
        approved = self.preview_operation(self.requester, high_operation)
        self.assertEqual(
            [item['id'] for item in approved['data']['matching_approvals']],
            [str(approval.id)],
        )

        changed_operation = dict(high_operation)
        changed_operation['payload'] = dict(high_operation['payload'], params={'service': 'sshd'})
        changed = self.preview_operation(self.requester, changed_operation)
        self.assertEqual(changed['data']['matching_approvals'], [])

        ApprovalRequest.objects.filter(pk=approval.id).update(
            approved_until=datetime.now() - timedelta(seconds=1)
        )
        expired = self.preview_operation(self.requester, high_operation)
        self.assertEqual(expired['data']['matching_approvals'], [])
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'expired')

    def test_operation_preview_rejects_unauthorized_host_and_local_scope(self):
        user = self.make_user('limited-user')
        operation = {
            'action': 'exec.run',
            'resource_type': 'host',
            'resource_ids': [99],
            'payload': {'host_ids': [99], 'command': 'uptime'},
        }
        with patch('apps.audit.views.has_host_perm', return_value=False):
            denied = self.preview_operation(user, operation)
        self.assertIn('无权为目标主机申请', denied['error'])

        local_operation = {
            'action': 'schedule.write',
            'resource_type': 'host',
            'resource_ids': ['local'],
            'payload': {'host_ids': [], 'targets': ['local'], 'command': 'uptime'},
        }
        denied_local = self.preview_operation(user, local_operation)
        self.assertIn('只有系统管理员', denied_local['error'])

    def test_approval_is_two_person_payload_bound_and_single_use(self):
        payload = {'host_ids': [3, 2], 'command': 'sudo systemctl restart nginx'}
        approval = create_approval(
            requester=self.requester,
            action='exec.run',
            resource_type='host',
            resource_ids=[3, 2],
            payload=payload,
            summary='滚动重启 nginx',
        )

        with self.assertRaisesRegex(ApprovalError, '不能是同一用户'):
            decide_approval(
                approval_id=approval.id,
                approver=self.requester,
                is_pass=True,
            )

        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
            ttl_minutes=15,
        )
        gate = authorize_operation(
            requester=self.requester,
            action='exec.run',
            resource_type='host',
            resource_ids=[2, 3],
            payload=payload,
            approval_id=approval.id,
            execution_ref='exec-1',
        )
        self.assertEqual(str(gate['approval_id']), str(approval.id))
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'consumed')
        self.assertEqual(approval.execution_ref, 'exec-1')

        with self.assertRaisesRegex(ApprovalError, '已经使用'):
            authorize_operation(
                requester=self.requester,
                action='exec.run',
                resource_type='host',
                resource_ids=[2, 3],
                payload=payload,
                approval_id=approval.id,
                execution_ref='exec-2',
            )

    def test_payload_tampering_and_expired_approval_fail_closed(self):
        payload = {'host_ids': [1], 'dst_dir': '/srv/app'}
        approval = create_approval(
            requester=self.requester,
            action='file.distribute',
            resource_type='host',
            resource_ids=[1],
            payload=payload,
            summary='分发构建产物',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )
        with self.assertRaisesRegex(ApprovalError, '参数不匹配'):
            authorize_operation(
                requester=self.requester,
                action='file.distribute',
                resource_type='host',
                resource_ids=[1],
                payload={'host_ids': [1], 'dst_dir': '/etc'},
                approval_id=approval.id,
            )

        ApprovalRequest.objects.filter(pk=approval.id).update(
            approved_until=datetime.now() - timedelta(seconds=1)
        )
        with self.assertRaisesRegex(ApprovalError, '过期'):
            authorize_operation(
                requester=self.requester,
                action='file.distribute',
                resource_type='host',
                resource_ids=[1],
                payload=payload,
                approval_id=approval.id,
            )

    def test_critical_approval_requires_rollback_plan(self):
        with self.assertRaisesRegex(ApprovalError, '回滚方案'):
            create_approval(
                requester=self.requester,
                action='file.delete',
                resource_type='host',
                resource_ids=[1],
                payload={'host_ids': [1], 'file': '/etc/app.conf'},
                summary='删除配置文件',
            )

    def test_file_upload_and_delete_use_exact_single_use_approvals(self):
        host = Host.objects.create(
            name='file-host', hostname='10.0.0.3', port=22, username='root',
            created_by=self.requester,
        )
        content = b'checked-content'
        upload_payload = {
            'host_ids': [host.id],
            'path': '/tmp',
            'filename': 'app.conf',
            'size': len(content),
        }
        denied_request = self.factory.post(
            '/file/object/',
            data={
                'id': host.id,
                'token': 'upload-denied',
                'path': '/tmp',
                'file': SimpleUploadedFile('app.conf', content),
            },
        )
        denied_request.user = self.requester
        denied = ObjectView.as_view()(denied_request)
        self.assertIn('需要先创建并通过审批', json.loads(denied.content.decode('utf-8'))['error'])

        upload_approval = create_approval(
            requester=self.requester,
            action='file.write',
            resource_type='host',
            resource_ids=[host.id],
            payload=upload_payload,
            summary='上传应用配置',
        )
        decide_approval(
            approval_id=upload_approval.id,
            approver=self.approver,
            is_pass=True,
        )
        uploaded = []
        removed = []

        class RedisStub:
            def publish(self, channel, value):
                return 1

        class SSHStub:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

            def put_file_by_fl(self, file_obj, path, callback=None):
                uploaded.append((path, file_obj.read()))
                if callback:
                    callback(file_obj.size)

            def remove_file(self, path):
                removed.append(path)

        upload_request = self.factory.post(
            '/file/object/',
            data={
                'id': host.id,
                'token': 'upload-approved',
                'path': '/tmp',
                'approval_id': str(upload_approval.id),
                'file': SimpleUploadedFile('app.conf', content),
            },
        )
        upload_request.user = self.requester
        with patch('apps.file.views.get_redis_connection', return_value=RedisStub()), \
                patch('apps.file.views.Host.get_ssh', return_value=SSHStub()):
            uploaded_response = ObjectView.as_view()(upload_request)
        self.assertFalse(json.loads(uploaded_response.content.decode('utf-8'))['error'])
        self.assertEqual(uploaded, [('/tmp/app.conf', content)])
        upload_approval.refresh_from_db()
        self.assertEqual(upload_approval.status, 'consumed')

        delete_payload = {'host_ids': [host.id], 'file': '/tmp/app.conf'}
        delete_approval = create_approval(
            requester=self.requester,
            action='file.delete',
            resource_type='host',
            resource_ids=[host.id],
            payload=delete_payload,
            summary='删除旧应用配置',
            rollback_plan='从配置仓库恢复 app.conf',
        )
        decide_approval(
            approval_id=delete_approval.id,
            approver=self.approver,
            is_pass=True,
        )
        delete_request = self.factory.delete(
            '/file/object/?id=%s&file=/tmp/app.conf&approval_id=%s' % (
                host.id, delete_approval.id
            )
        )
        delete_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=SSHStub()):
            deleted_response = ObjectView.as_view()(delete_request)
        self.assertFalse(json.loads(deleted_response.content.decode('utf-8'))['error'])
        self.assertEqual(removed, ['/tmp/app.conf'])
        delete_approval.refresh_from_db()
        self.assertEqual(delete_approval.status, 'consumed')
        self.assertTrue(AuditEvent.objects.filter(action='file.write', result='succeeded').exists())
        self.assertTrue(AuditEvent.objects.filter(action='file.delete', result='succeeded').exists())

    def test_chunked_file_upload_resumes_and_verifies_remote_sha256(self):
        host = Host.objects.create(
            name='chunk-host', hostname='10.0.0.4', port=22, username='root',
            created_by=self.requester,
        )
        chunk_size = 256 * 1024
        content = (b'spug-resumable-upload-' * 14000) + b'final'
        expected_sha256 = hashlib.sha256(content).hexdigest()
        payload = {
            'host_ids': [host.id],
            'path': '/tmp',
            'filename': 'large.bin',
            'size': len(content),
            'sha256': expected_sha256,
            'chunk_size': chunk_size,
            'conflict_strategy': 'overwrite',
        }
        approval = create_approval(
            requester=self.requester,
            action='file.write',
            resource_type='host',
            resource_ids=[host.id],
            payload=payload,
            summary='分片上传大文件',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )

        init_request = self.factory.post(
            '/file/uploads/',
            data=json.dumps(dict(
                payload,
                id=host.id,
                approval_id=str(approval.id),
            )),
            content_type='application/json',
        )
        init_request.user = self.requester
        init_response = UploadSessionView.as_view()(init_request)
        init_body = json.loads(init_response.content.decode('utf-8'))
        self.assertFalse(init_body['error'])
        upload_id = init_body['data']['id']
        transfer = FileTransfer.objects.get(pk=upload_id)
        self.assertEqual(transfer.offset, 0)
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'consumed')
        self.assertEqual(approval.execution_ref, upload_id)

        remote_files = {}

        class ChunkSSHStub:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

            def write_file_chunk(self, file_obj, path, offset):
                current = remote_files.get(path, b'')
                if len(current) != offset:
                    raise ValueError('offset mismatch')
                remote_files[path] = current + file_obj.read()
                return len(remote_files[path])

            def remote_file_size(self, path):
                return len(remote_files.get(path, b''))

            def remote_file_sha256(self, path):
                return hashlib.sha256(remote_files[path]).hexdigest()

            def replace_file(self, source, destination, overwrite=True):
                if not overwrite and destination in remote_files:
                    raise FileExistsError(destination)
                remote_files[destination] = remote_files.pop(source)

            def create_empty_file(self, path):
                remote_files[path] = b''

        ssh_stub = ChunkSSHStub()
        first_chunk = content[:chunk_size]
        bad_chunk_request = self.factory.post(
            '/file/uploads/%s/chunk/' % upload_id,
            data={
                'offset': 0,
                'sha256': '0' * 64,
                'chunk': SimpleUploadedFile('chunk.bin', first_chunk),
            },
        )
        bad_chunk_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=ssh_stub):
            bad_chunk_response = UploadChunkView.as_view()(
                bad_chunk_request, upload_id=upload_id
            )
        self.assertIn(
            'SHA-256 校验失败',
            json.loads(bad_chunk_response.content.decode('utf-8'))['error'],
        )
        transfer.refresh_from_db()
        self.assertEqual(transfer.offset, 0)
        self.assertEqual(remote_files, {})

        first_request = self.factory.post(
            '/file/uploads/%s/chunk/' % upload_id,
            data={
                'offset': 0,
                'sha256': hashlib.sha256(first_chunk).hexdigest(),
                'chunk': SimpleUploadedFile('chunk.bin', first_chunk),
            },
        )
        first_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=ssh_stub):
            first_response = UploadChunkView.as_view()(
                first_request, upload_id=upload_id
            )
        first_body = json.loads(first_response.content.decode('utf-8'))
        self.assertFalse(first_body['error'])
        self.assertEqual(first_body['data']['offset'], chunk_size)

        FileTransfer.objects.filter(pk=upload_id).update(offset=0)
        reconcile_request = self.factory.post(
            '/file/uploads/%s/chunk/' % upload_id,
            data={
                'offset': 0,
                'sha256': hashlib.sha256(first_chunk).hexdigest(),
                'chunk': SimpleUploadedFile('chunk.bin', first_chunk),
            },
        )
        reconcile_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=ssh_stub):
            reconcile_response = UploadChunkView.as_view()(
                reconcile_request, upload_id=upload_id
            )
        self.assertIn(
            '恢复上传偏移',
            json.loads(reconcile_response.content.decode('utf-8'))['error'],
        )
        transfer.refresh_from_db()
        self.assertEqual(transfer.offset, chunk_size)

        status_request = self.factory.get('/file/uploads/%s/' % upload_id)
        status_request.user = self.requester
        status_response = UploadSessionDetailView.as_view()(
            status_request, upload_id=upload_id
        )
        self.assertEqual(
            json.loads(status_response.content.decode('utf-8'))['data']['offset'],
            chunk_size,
        )

        duplicate_request = self.factory.post(
            '/file/uploads/%s/chunk/' % upload_id,
            data={
                'offset': 0,
                'sha256': hashlib.sha256(first_chunk).hexdigest(),
                'chunk': SimpleUploadedFile('chunk.bin', first_chunk),
            },
        )
        duplicate_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=ssh_stub):
            duplicate_response = UploadChunkView.as_view()(
                duplicate_request, upload_id=upload_id
            )
        self.assertIn(
            '偏移量已变化',
            json.loads(duplicate_response.content.decode('utf-8'))['error'],
        )

        second_chunk = content[chunk_size:]
        second_request = self.factory.post(
            '/file/uploads/%s/chunk/' % upload_id,
            data={
                'offset': chunk_size,
                'sha256': hashlib.sha256(second_chunk).hexdigest(),
                'chunk': SimpleUploadedFile('chunk.bin', second_chunk),
            },
        )
        second_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=ssh_stub):
            second_response = UploadChunkView.as_view()(
                second_request, upload_id=upload_id
            )
        self.assertFalse(json.loads(second_response.content.decode('utf-8'))['error'])

        complete_request = self.factory.post('/file/uploads/%s/complete/' % upload_id)
        complete_request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=ssh_stub):
            complete_response = UploadCompleteView.as_view()(
                complete_request, upload_id=upload_id
            )
        complete_body = json.loads(complete_response.content.decode('utf-8'))
        self.assertFalse(complete_body['error'])
        self.assertEqual(complete_body['data']['status'], 'completed')
        self.assertEqual(complete_body['data']['actual_sha256'], expected_sha256)
        self.assertEqual(remote_files['/tmp/large.bin'], content)
        self.assertNotIn(transfer.temporary_path, remote_files)
        self.assertTrue(AuditEvent.objects.filter(
            correlation_id=approval.correlation_id,
            action='file.write',
            result='succeeded',
        ).exists())

    def test_chunked_upload_approval_binds_digest_and_routes_resolve(self):
        host = Host.objects.create(
            name='digest-host', hostname='10.0.0.5', port=22, username='root',
            created_by=self.requester,
        )
        payload = {
            'host_ids': [host.id],
            'path': '/tmp',
            'filename': 'artifact.bin',
            'size': 1,
            'sha256': hashlib.sha256(b'a').hexdigest(),
            'chunk_size': 256 * 1024,
            'conflict_strategy': 'overwrite',
        }
        approval = create_approval(
            requester=self.requester,
            action='file.write',
            resource_type='host',
            resource_ids=[host.id],
            payload=payload,
            summary='上传指定制品',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )
        changed = dict(payload, sha256=hashlib.sha256(b'b').hexdigest())
        request = self.factory.post(
            '/file/uploads/',
            data=json.dumps(dict(
                changed,
                id=host.id,
                approval_id=str(approval.id),
            )),
            content_type='application/json',
        )
        request.user = self.requester
        response = UploadSessionView.as_view()(request)
        self.assertIn(
            '参数不匹配',
            json.loads(response.content.decode('utf-8'))['error'],
        )
        self.assertFalse(FileTransfer.objects.exists())
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'approved')

        self.assertIs(
            resolve('/file/uploads/%s/' % uuid.uuid4()).func.view_class,
            UploadSessionDetailView,
        )
        self.assertIs(
            resolve('/file/uploads/%s/chunk/' % uuid.uuid4()).func.view_class,
            UploadChunkView,
        )
        self.assertIs(
            resolve('/file/uploads/%s/complete/' % uuid.uuid4()).func.view_class,
            UploadCompleteView,
        )

    def test_chunked_upload_hash_mismatch_never_replaces_target(self):
        host = Host.objects.create(
            name='hash-host', hostname='10.0.0.6', port=22, username='root',
            created_by=self.requester,
        )
        expected = b'approved-content'
        approval = create_approval(
            requester=self.requester,
            action='file.write',
            resource_type='host',
            resource_ids=[host.id],
            payload={
                'host_ids': [host.id],
                'path': '/tmp',
                'filename': 'guarded.bin',
                'size': len(expected),
                'sha256': hashlib.sha256(expected).hexdigest(),
                'chunk_size': 256 * 1024,
                'conflict_strategy': 'overwrite',
            },
            summary='上传受校验保护的制品',
        )
        transfer = FileTransfer.objects.create(
            correlation_id=approval.correlation_id,
            uploader=self.requester,
            host=host,
            approval=approval,
            directory='/tmp',
            filename='guarded.bin',
            remote_path='/tmp/guarded.bin',
            temporary_path='/tmp/.spug-upload-hash.part',
            size=len(expected),
            offset=len(expected),
            chunk_size=256 * 1024,
            expected_sha256=hashlib.sha256(expected).hexdigest(),
            conflict_strategy='overwrite',
        )
        remote_files = {
            transfer.temporary_path: b'corrupt-content!',
            transfer.remote_path: b'original-content',
        }
        replacements = []

        class CorruptSSHStub:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

            def remote_file_sha256(self, path):
                return hashlib.sha256(remote_files[path]).hexdigest()

            def replace_file(self, source, destination, overwrite=True):
                replacements.append((source, destination))

        request = self.factory.post('/file/uploads/%s/complete/' % transfer.id)
        request.user = self.requester
        with patch('apps.file.views.Host.get_ssh', return_value=CorruptSSHStub()):
            response = UploadCompleteView.as_view()(request, upload_id=transfer.id)
        body = json.loads(response.content.decode('utf-8'))
        self.assertIn('SHA-256 校验失败', body['error'])
        self.assertEqual(replacements, [])
        self.assertEqual(remote_files[transfer.remote_path], b'original-content')
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'failed')
        self.assertTrue(AuditEvent.objects.filter(
            correlation_id=approval.correlation_id,
            action='file.write',
            result='failed',
        ).exists())

    def test_sftp_replace_fallback_preserves_destination_until_rename(self):
        class SFTPStub:
            def __init__(self, posix_error):
                self.files = {
                    '/tmp/source.part': b'new',
                    '/tmp/target.bin': b'old',
                }
                self.posix_error = posix_error

            def posix_rename(self, source, destination):
                raise self.posix_error

            def stat(self, path):
                if path not in self.files:
                    raise IOError(errno.ENOENT, 'not found')
                return object()

            def rename(self, source, destination):
                self.files[destination] = self.files.pop(source)

            def remove(self, path):
                self.files.pop(path)

        unsupported = SFTPStub(IOError(errno.EOPNOTSUPP, 'Operation unsupported'))
        ssh = SSH.__new__(SSH)
        ssh.sftp = unsupported
        ssh.replace_file('/tmp/source.part', '/tmp/target.bin', overwrite=True)
        self.assertEqual(unsupported.files, {'/tmp/target.bin': b'new'})

        denied = SFTPStub(IOError(errno.EACCES, 'Permission denied'))
        ssh.sftp = denied
        with self.assertRaises(IOError):
            ssh.replace_file('/tmp/source.part', '/tmp/target.bin', overwrite=True)
        self.assertEqual(denied.files['/tmp/source.part'], b'new')
        self.assertEqual(denied.files['/tmp/target.bin'], b'old')

    def test_audit_chain_redacts_secrets_and_rejects_mutation(self):
        correlation_id = create_approval(
            requester=self.requester,
            action='file.write',
            resource_type='host',
            resource_ids=[1],
            payload={'host_ids': [1], 'path': '/tmp'},
            summary='上传文件',
        ).correlation_id
        event = record_event(
            correlation_id=correlation_id,
            actor=self.requester,
            action='file.write',
            resource_type='host',
            resource_id=1,
            result='started',
            details={'password': 'plain-secret', 'nested': {'token': 'api-token'}},
        )
        self.assertNotIn('plain-secret', event.details)
        self.assertNotIn('api-token', event.details)
        valid, message = verify_audit_chain()
        self.assertTrue(valid, message)
        with self.assertRaises(ValidationError):
            AuditEvent.objects.filter(pk=event.id).update(result='failed')
        with self.assertRaises(ValidationError):
            event.delete()

    def test_high_risk_exec_requires_and_rechecks_approval(self):
        host = Host.objects.create(
            name='host-1', hostname='10.0.0.1', port=22, username='root',
            created_by=self.requester,
        )
        command = 'sudo systemctl restart nginx'
        payload = {
            'host_ids': [host.id],
            'command': command,
            'interpreter': 'sh',
            'template_id': None,
            'params': {},
        }

        denied_request = self.factory.post(
            '/exec/do/',
            data=json.dumps(dict(payload, approval_id=None)),
            content_type='application/json',
        )
        denied_request.user = self.requester
        denied = TaskView.as_view()(denied_request)
        denied_body = json.loads(denied.content.decode('utf-8'))
        self.assertIn('需要先创建并通过审批', denied_body['error'])
        self.assertFalse(ExecHistory.objects.exists())

        approval = create_approval(
            requester=self.requester,
            action='exec.run',
            resource_type='host',
            resource_ids=[host.id],
            payload=payload,
            summary='重启 nginx',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )
        create_request = self.factory.post(
            '/exec/do/',
            data=json.dumps(dict(payload, approval_id=str(approval.id))),
            content_type='application/json',
        )
        create_request.user = self.requester
        created = TaskView.as_view()(create_request)
        token = json.loads(created.content.decode('utf-8'))['data']
        task = ExecHistory.objects.get(digest=token)
        self.assertEqual(task.approval_id, approval.id)

        queued = []

        class RedisStub:
            def rpush(self, key, value):
                queued.append(json.loads(value))

        dispatch_request = self.factory.patch(
            '/exec/do/',
            data=json.dumps({'token': token}),
            content_type='application/json',
        )
        dispatch_request.user = self.requester
        with patch('apps.exec.views.get_redis_connection', return_value=RedisStub()):
            response = TaskView.as_view()(dispatch_request)
        self.assertFalse(json.loads(response.content.decode('utf-8'))['error'])
        self.assertEqual(queued[0]['approval_id'], str(approval.id))
        self.assertEqual(queued[0]['correlation_id'], str(approval.correlation_id))

    def test_schedule_configuration_and_activation_use_separate_approvals(self):
        host = Host.objects.create(
            name='schedule-host', hostname='10.0.0.2', port=22, username='root',
            created_by=self.requester,
        )
        config_payload = {
            'host_ids': [host.id],
            'targets': [host.id],
            'name': 'check-service',
            'interpreter': 'sh',
            'command': 'systemctl is-active nginx',
            'trigger': 'interval',
            'trigger_args': '60',
        }
        config_approval = create_approval(
            requester=self.requester,
            action='schedule.write',
            resource_type='host',
            resource_ids=[host.id],
            payload=config_payload,
            summary='创建服务检查计划',
        )
        decide_approval(
            approval_id=config_approval.id,
            approver=self.approver,
            is_pass=True,
        )
        create_request = self.factory.post(
            '/schedule/',
            data=json.dumps({
                'type': 'service',
                'name': config_payload['name'],
                'interpreter': config_payload['interpreter'],
                'command': config_payload['command'],
                'rst_notify': {},
                'targets': config_payload['targets'],
                'trigger': config_payload['trigger'],
                'trigger_args': config_payload['trigger_args'],
                'approval_id': str(config_approval.id),
            }),
            content_type='application/json',
        )
        create_request.user = self.requester
        created = Schedule.as_view()(create_request)
        self.assertFalse(json.loads(created.content.decode('utf-8'))['error'])
        task = Task.objects.get(name='check-service')
        self.assertFalse(task.is_active)
        self.assertEqual(task.approval_id, config_approval.id)

        activation_payload = {
            'host_ids': [host.id],
            'task_id': task.id,
            'interpreter': task.interpreter,
            'command': task.command,
            'targets': [host.id],
            'trigger': task.trigger,
            'trigger_args': task.trigger_args,
        }
        activation = create_approval(
            requester=self.requester,
            action='schedule.run',
            resource_type='host',
            resource_ids=[host.id],
            payload=activation_payload,
            summary='启用服务检查计划',
        )
        decide_approval(
            approval_id=activation.id,
            approver=self.approver,
            is_pass=True,
        )

        class RedisStub:
            def lpush(self, key, value):
                return 1

        activate_request = self.factory.patch(
            '/schedule/',
            data=json.dumps({
                'id': task.id,
                'is_active': True,
                'approval_id': str(activation.id),
            }),
            content_type='application/json',
        )
        activate_request.user = self.requester
        with patch('apps.schedule.views.get_redis_connection', return_value=RedisStub()):
            activated = Schedule.as_view()(activate_request)
        self.assertFalse(json.loads(activated.content.decode('utf-8'))['error'])
        task.refresh_from_db()
        self.assertTrue(task.is_active)
        self.assertEqual(task.approval_id, activation.id)
        self.assertTrue(task.approval_execution_ref.startswith('schedule-activate:'))

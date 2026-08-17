import errno
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import resolve

from apps.account.models import User
from apps.audit.models import ApprovalRequest, AuditEvent
from apps.audit.services import create_approval, decide_approval
from apps.file.models import FileTransfer, FileTransferBatch
from apps.file.services import (
    MAX_CLEANUP_ATTEMPTS,
    cleanup_expired_file_transfers,
)
from apps.file.utils import remote_file_etag
from apps.file.views import (
    ObjectView,
    UploadBatchDetailView,
    UploadBatchView,
    UploadChunkView,
    UploadCompleteView,
    UploadSessionDetailView,
)
from apps.host.models import Host
from apps.schedule.scheduler import Scheduler
from libs.ssh import SSH


class DownloadSFTPStub:
    def __init__(self, content, *, mtime=1700000000, open_error=None):
        self.content = content
        self.mtime = mtime
        self.open_error = open_error
        self.open_calls = 0

    def stat(self, path):
        return SimpleNamespace(st_size=len(self.content), st_mtime=self.mtime)

    def open(self, path, mode):
        self.open_calls += 1
        if self.open_error:
            raise self.open_error
        return BytesIO(self.content)


class DownloadClientStub:
    def __init__(self, sftp):
        self.sftp = sftp
        self.close_calls = 0

    def open_sftp(self):
        return self.sftp

    def close(self):
        self.close_calls += 1


class DownloadSSHStub:
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return self.client


class CleanupSSHStub:
    def __init__(self, outcome):
        self.outcome = outcome

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def remove_file_if_exists(self, path):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class CompleteSSHStub:
    def __init__(self, digest):
        self.digest = digest
        self.created = []
        self.replaced = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def create_empty_file(self, path):
        self.created.append(path)

    def remote_file_sha256(self, path):
        return self.digest

    def replace_file(self, source, target, overwrite=True):
        self.replaced.append((source, target, overwrite))


class FileFeatureTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create(
            username='file-admin',
            nickname='file-admin',
            password_hash='unused',
            type='default',
            is_supper=True,
            is_active=True,
            access_token='file-admin' + ('x' * 22),
            token_expired=0,
            last_login='',
            last_ip='127.0.0.1',
        )
        self.approver = User.objects.create(
            username='file-approver',
            nickname='file-approver',
            password_hash='unused',
            type='default',
            is_supper=True,
            is_active=True,
            access_token='file-approver' + ('x' * 19),
            token_expired=0,
            last_login='',
            last_ip='127.0.0.1',
        )
        self.host = Host.objects.create(
            name='file-host', hostname='10.0.0.8', port=22, username='root',
            created_by=self.user,
        )

    def request_download(self, content, *, method='get', byte_range=None,
                         if_range=None, open_error=None):
        path = '/tmp/archive.bin'
        headers = {}
        if byte_range is not None:
            headers['HTTP_RANGE'] = byte_range
        if if_range is not None:
            headers['HTTP_IF_RANGE'] = if_range
        request = getattr(self.factory, method)(
            '/file/object/?id=%s&file=%s' % (self.host.id, path), **headers
        )
        request.user = self.user
        sftp = DownloadSFTPStub(content, open_error=open_error)
        client = DownloadClientStub(sftp)
        with patch('apps.file.views.Host.get_ssh', return_value=DownloadSSHStub(client)):
            response = ObjectView.as_view()(request)
        return response, sftp, client

    def make_transfer(self, *, status='active', expires_at=None,
                      cleanup_status='pending', cleanup_attempts=0, suffix=None):
        suffix = suffix or uuid.uuid4().hex[:8]
        approval = ApprovalRequest.objects.create(
            requester=self.user,
            action='file.write',
            resource_type='host',
            resource_ids=json.dumps([self.host.id]),
            risk_level='medium',
            summary='upload ' + suffix,
            payload_hash=hashlib.sha256(suffix.encode('ascii')).hexdigest(),
            status='consumed',
        )
        return FileTransfer.objects.create(
            correlation_id=approval.correlation_id,
            uploader=self.user,
            host=self.host,
            approval=approval,
            directory='/tmp',
            filename=suffix + '.bin',
            remote_path='/tmp/' + suffix + '.bin',
            temporary_path='/tmp/.spug-upload-' + suffix + '.part',
            size=10,
            chunk_size=256 * 1024,
            expected_sha256='0' * 64,
            status=status,
            expires_at=expires_at or (datetime.now() + timedelta(hours=1)),
            cleanup_status=cleanup_status,
            cleanup_attempts=cleanup_attempts,
        )

    def batch_files(self):
        return [
            {
                'filename': 'b.txt',
                'size': 0,
                'sha256': hashlib.sha256(b'').hexdigest(),
            },
            {
                'filename': 'a.txt',
                'size': 0,
                'sha256': hashlib.sha256(b'').hexdigest(),
            },
        ]

    def batch_payload(self, *, files=None, max_concurrency=2):
        files = files or self.batch_files()
        return {
            'host_ids': [self.host.id],
            'path': '/tmp',
            'files': sorted(files, key=lambda item: item['filename']),
            'chunk_size': 256 * 1024,
            'conflict_strategy': 'overwrite',
            'max_concurrency': max_concurrency,
        }

    def approved_batch_request(self, *, files=None, max_concurrency=2):
        files = files or self.batch_files()
        payload = self.batch_payload(
            files=files, max_concurrency=max_concurrency
        )
        approval = create_approval(
            requester=self.user,
            action='file.write',
            resource_type='host',
            resource_ids=[self.host.id],
            payload=payload,
            summary='批量上传测试文件',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )
        request = self.factory.post(
            '/file/upload-batches/',
            data=json.dumps({
                'id': self.host.id,
                'path': '/tmp',
                'files': files,
                'chunk_size': 256 * 1024,
                'conflict_strategy': 'overwrite',
                'max_concurrency': max_concurrency,
                'approval_id': str(approval.id),
            }),
            content_type='application/json',
        )
        request.user = self.user
        return approval, request

    def create_batch(self, *, files=None, max_concurrency=2):
        approval, request = self.approved_batch_request(
            files=files, max_concurrency=max_concurrency
        )
        response = UploadBatchView.as_view()(request)
        body = json.loads(response.content.decode('utf-8'))
        self.assertFalse(body['error'])
        return approval, FileTransferBatch.objects.get(pk=body['data']['id'])

    def batch_action(self, batch, action, *, user=None):
        request = self.factory.patch(
            '/file/upload-batches/%s/' % batch.id,
            data=json.dumps({'action': action}),
            content_type='application/json',
        )
        request.user = user or self.user
        response = UploadBatchDetailView.as_view()(request, batch_id=batch.id)
        return json.loads(response.content.decode('utf-8'))

    def test_full_and_single_range_downloads(self):
        content = b'0123456789'
        response, _, _ = self.request_download(content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Content-Length'], '10')
        self.assertEqual(b''.join(response.streaming_content), content)

        cases = (
            ('bytes=2-5', b'2345', 'bytes 2-5/10'),
            ('bytes=5-', b'56789', 'bytes 5-9/10'),
            ('bytes=-4', b'6789', 'bytes 6-9/10'),
        )
        for byte_range, expected, content_range in cases:
            with self.subTest(byte_range=byte_range):
                response, _, _ = self.request_download(
                    content, byte_range=byte_range
                )
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response['Content-Range'], content_range)
                self.assertEqual(response['Content-Length'], str(len(expected)))
                self.assertEqual(b''.join(response.streaming_content), expected)

        self.assertEqual(
            AuditEvent.objects.filter(action='file.read', result='succeeded').count(),
            4,
        )

    def test_invalid_and_multiple_ranges_return_416(self):
        for byte_range in ('bytes=20-30', 'bytes=5-2', 'bytes=1-2,4-5', 'items=1-2'):
            with self.subTest(byte_range=byte_range):
                response, sftp, client = self.request_download(
                    b'0123456789', byte_range=byte_range
                )
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response['Content-Range'], 'bytes */10')
                self.assertEqual(sftp.open_calls, 0)
                self.assertEqual(client.close_calls, 1)

    def test_if_range_match_resumes_and_mismatch_returns_full_body(self):
        content = b'0123456789'
        stat = SimpleNamespace(st_size=len(content), st_mtime=1700000000)
        etag = remote_file_etag(self.host.id, '/tmp/archive.bin', stat)

        response, _, _ = self.request_download(
            content, byte_range='bytes=3-', if_range=etag
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'3456789')

        response, _, _ = self.request_download(
            content, byte_range='bytes=3-', if_range='"stale-etag"'
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Content-Range', response)
        self.assertEqual(b''.join(response.streaming_content), content)

    def test_head_does_not_open_remote_file(self):
        response, sftp, client = self.request_download(b'0123456789', method='head')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Length'], '10')
        self.assertEqual(sftp.open_calls, 0)
        self.assertEqual(client.close_calls, 1)
        event = AuditEvent.objects.filter(
            action='file.read', result='succeeded'
        ).get()
        self.assertTrue(event.detail_data['head_only'])
        self.assertEqual(event.detail_data['bytes_sent'], 0)

    def test_stream_completion_and_client_interrupt_are_audited(self):
        complete, _, _ = self.request_download(b'complete')
        self.assertEqual(b''.join(complete.streaming_content), b'complete')

        content = b'x' * (1024 * 1024 + 10)
        interrupted, _, client = self.request_download(content)
        stream = iter(interrupted.streaming_content)
        self.assertEqual(len(next(stream)), 1024 * 1024)
        self.assertEqual(len(interrupted._closable_objects), 1)
        interrupted._closable_objects[0].close()
        self.assertGreaterEqual(client.close_calls, 1)

        events = list(AuditEvent.objects.filter(
            action='file.read'
        ).order_by('sequence').values_list('result', flat=True))
        self.assertEqual(events, ['started', 'succeeded', 'started', 'failed'])
        failed = AuditEvent.objects.filter(action='file.read', result='failed').get()
        self.assertEqual(failed.detail_data['bytes_sent'], 1024 * 1024)

    def test_remote_open_failure_is_audited(self):
        with self.assertRaises(PermissionError):
            self.request_download(
                b'blocked', open_error=PermissionError('permission denied')
            )
        events = list(AuditEvent.objects.filter(
            action='file.read'
        ).order_by('sequence').values_list('result', flat=True))
        self.assertEqual(events, ['started', 'failed'])

    def test_cancel_upload_records_removed_missing_and_failed_cleanup(self):
        cases = (
            (True, 'removed'),
            (False, 'missing'),
            (PermissionError('permission denied'), 'failed'),
        )
        for remote_outcome, expected in cases:
            with self.subTest(expected=expected):
                transfer = self.make_transfer()
                request = self.factory.delete('/file/uploads/%s/' % transfer.id)
                request.user = self.user
                with patch(
                    'apps.host.models.Host.get_ssh',
                    return_value=CleanupSSHStub(remote_outcome),
                ):
                    response = UploadSessionDetailView.as_view()(
                        request, upload_id=transfer.id
                    )
                body = json.loads(response.content.decode('utf-8'))
                self.assertFalse(body['error'])
                transfer.refresh_from_db()
                self.assertEqual(transfer.status, 'cancelled')
                self.assertEqual(transfer.cleanup_status, expected)
                self.assertEqual(transfer.cleanup_attempts, 1)
                self.assertIsNotNone(transfer.cleanup_attempted_at)
                if expected == 'failed':
                    self.assertIsNone(transfer.cleanup_completed_at)
                    self.assertEqual(
                        transfer.cleanup_error, 'cleanup failed: PermissionError'
                    )
                else:
                    self.assertIsNotNone(transfer.cleanup_completed_at)
                    self.assertIsNone(transfer.cleanup_error)
                self.assertTrue(AuditEvent.objects.filter(
                    correlation_id=transfer.correlation_id,
                    action='file.cleanup',
                    result='failed' if expected == 'failed' else 'succeeded',
                ).exists())
                if expected in ('removed', 'missing'):
                    get_ssh = Mock(return_value=CleanupSSHStub(True))
                    with patch('apps.host.models.Host.get_ssh', get_ssh):
                        repeated = UploadSessionDetailView.as_view()(
                            request, upload_id=transfer.id
                        )
                    self.assertFalse(
                        json.loads(repeated.content.decode('utf-8'))['error']
                    )
                    self.assertEqual(get_ssh.call_count, 0)
                    self.assertEqual(AuditEvent.objects.filter(
                        correlation_id=transfer.correlation_id,
                        action='file.cleanup',
                    ).count(), 1)

    def test_cleanup_service_retries_failures_and_stops_at_limit(self):
        transfer = self.make_transfer(
            status='failed', cleanup_status='failed',
            cleanup_attempts=MAX_CLEANUP_ATTEMPTS - 1,
        )
        get_ssh = Mock(return_value=CleanupSSHStub(PermissionError('denied')))
        with patch('apps.host.models.Host.get_ssh', get_ssh):
            first = cleanup_expired_file_transfers(execute=True)
            second = cleanup_expired_file_transfers(execute=True)
        self.assertEqual(first['failed'], 1)
        self.assertEqual(second['eligible'], 0)
        self.assertEqual(get_ssh.call_count, 1)
        transfer.refresh_from_db()
        self.assertEqual(transfer.cleanup_attempts, MAX_CLEANUP_ATTEMPTS)
        self.assertEqual(transfer.cleanup_status, 'failed')

    def test_cleanup_management_command_is_dry_run_by_default_and_executes(self):
        transfer = self.make_transfer(
            expires_at=datetime.now() - timedelta(minutes=1)
        )
        output = StringIO()
        get_ssh = Mock(return_value=CleanupSSHStub(True))
        with patch('apps.host.models.Host.get_ssh', get_ssh):
            call_command('cleanup_file_transfers', stdout=output)
        self.assertIn('dry-run eligible=1', output.getvalue())
        self.assertEqual(get_ssh.call_count, 0)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'active')

        output = StringIO()
        with patch('apps.host.models.Host.get_ssh', get_ssh):
            call_command('cleanup_file_transfers', '--execute', stdout=output)
        self.assertIn('eligible=1 removed=1', output.getvalue())
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'expired')
        self.assertEqual(transfer.cleanup_status, 'removed')
        self.assertEqual(get_ssh.call_count, 1)

    def test_scheduler_registers_single_instance_cleanup_every_15_minutes(self):
        scheduler = Scheduler()
        scheduler.scheduler = Mock()
        scheduler._init_builtin_jobs()
        cleanup_call = scheduler.scheduler.add_job.call_args_list[2]
        args, kwargs = cleanup_call
        self.assertIs(args[0], scheduler._cleanup_file_transfers)
        self.assertEqual(args[1], 'interval')
        self.assertEqual(kwargs['minutes'], 15)
        self.assertEqual(kwargs['id'], 'builtin:file-transfer-cleanup')
        self.assertEqual(kwargs['max_instances'], 1)
        self.assertTrue(kwargs['coalesce'])

    def test_ssh_remove_file_if_exists_only_swallows_missing_file(self):
        class SFTPStub:
            def __init__(self, error):
                self.error = error

            def remove(self, path):
                raise self.error

        ssh = SSH.__new__(SSH)
        ssh.sftp = SFTPStub(IOError(errno.ENOENT, 'No such file'))
        self.assertFalse(ssh.remove_file_if_exists('/tmp/missing.part'))

        ssh.sftp = SFTPStub(IOError(errno.EACCES, 'Permission denied'))
        with self.assertRaises(IOError):
            ssh.remove_file_if_exists('/tmp/protected.part')

    def test_batch_creation_consumes_exact_approval_and_sorts_transfers(self):
        approval, batch = self.create_batch()
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'consumed')
        self.assertEqual(approval.execution_ref, str(batch.id))
        self.assertEqual(batch.file_count, 2)
        self.assertEqual(batch.total_size, 0)
        self.assertEqual(batch.max_concurrency, 2)
        transfers = list(batch.transfers.order_by('sequence'))
        self.assertEqual([item.filename for item in transfers], ['a.txt', 'b.txt'])
        self.assertEqual([item.sequence for item in transfers], [0, 1])
        self.assertTrue(all(item.approval_id == approval.id for item in transfers))
        self.assertTrue(all(item.correlation_id == approval.correlation_id for item in transfers))

    def test_batch_approval_rejects_file_digest_and_concurrency_tampering(self):
        mutations = (
            lambda data: data['files'].append({
                'filename': 'extra.txt',
                'size': 0,
                'sha256': hashlib.sha256(b'').hexdigest(),
            }),
            lambda data: data['files'][0].update({'sha256': 'f' * 64}),
            lambda data: data.update({'max_concurrency': 3}),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                approval, request = self.approved_batch_request()
                data = json.loads(request.body.decode('utf-8'))
                mutate(data)
                request = self.factory.post(
                    '/file/upload-batches/',
                    data=json.dumps(data),
                    content_type='application/json',
                )
                request.user = self.user
                response = UploadBatchView.as_view()(request)
                body = json.loads(response.content.decode('utf-8'))
                self.assertIn('参数不匹配', body['error'])
                approval.refresh_from_db()
                self.assertEqual(approval.status, 'approved')

    def test_batch_rejects_duplicate_too_many_and_oversized_file_sets(self):
        empty_digest = hashlib.sha256(b'').hexdigest()
        invalid_sets = (
            (
                [
                    {'filename': 'same', 'size': 0, 'sha256': empty_digest},
                    {'filename': 'same', 'size': 0, 'sha256': empty_digest},
                ],
                '同名',
            ),
            (
                [
                    {'filename': '%02d.bin' % index, 'size': 0, 'sha256': empty_digest}
                    for index in range(51)
                ],
                '1 到 50',
            ),
            (
                [
                    {
                        'filename': '%02d.bin' % index,
                        'size': 1024 ** 4,
                        'sha256': empty_digest,
                    }
                    for index in range(6)
                ],
                '5 TiB',
            ),
        )
        for files, expected in invalid_sets:
            with self.subTest(expected=expected):
                request = self.factory.post(
                    '/file/upload-batches/',
                    data=json.dumps({
                        'id': self.host.id,
                        'path': '/tmp',
                        'files': files,
                        'chunk_size': 256 * 1024,
                        'conflict_strategy': 'overwrite',
                        'max_concurrency': 2,
                        'approval_id': str(uuid.uuid4()),
                    }),
                    content_type='application/json',
                )
                request.user = self.user
                response = UploadBatchView.as_view()(request)
                body = json.loads(response.content.decode('utf-8'))
                self.assertIn(expected, body['error'])
        self.assertEqual(FileTransferBatch.objects.count(), 0)

    def test_batch_pause_blocks_chunks_and_resume_allows_them(self):
        content = b'x' * (256 * 1024)
        files = [{
            'filename': 'chunk.bin',
            'size': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
        }]
        _, batch = self.create_batch(files=files)
        transfer = batch.transfers.get()
        paused = self.batch_action(batch, 'pause')
        self.assertFalse(paused['error'])
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'paused')

        def chunk_request():
            request = self.factory.post(
                '/file/uploads/%s/chunk/' % transfer.id,
                data={
                    'offset': 0,
                    'sha256': hashlib.sha256(content).hexdigest(),
                    'chunk': SimpleUploadedFile('chunk', content),
                },
            )
            request.user = self.user
            return request

        blocked = UploadChunkView.as_view()(
            chunk_request(), upload_id=transfer.id
        )
        self.assertIn(
            '已暂停或终止', json.loads(blocked.content.decode('utf-8'))['error']
        )

        resumed = self.batch_action(batch, 'resume')
        self.assertFalse(resumed['error'])
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'active')
        with patch(
            'apps.host.models.Host.get_ssh',
            return_value=type('ChunkSSHStub', (), {
                '__enter__': lambda value: value,
                '__exit__': lambda value, *args: False,
                'remote_file_size': lambda value, path: 0,
                'write_file_chunk': lambda value, file_obj, path, offset: file_obj.size,
            })(),
        ):
            accepted = UploadChunkView.as_view()(
                chunk_request(), upload_id=transfer.id
            )
        self.assertFalse(json.loads(accepted.content.decode('utf-8'))['error'])
        transfer.refresh_from_db()
        self.assertEqual(transfer.offset, len(content))

    def test_expired_chunk_request_persists_terminal_batch_state(self):
        content = b'x' * (256 * 1024)
        files = [{
            'filename': 'expired.bin',
            'size': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
        }]
        _, batch = self.create_batch(files=files)
        transfer = batch.transfers.get()
        FileTransfer.objects.filter(pk=transfer.id).update(
            expires_at=datetime.now() - timedelta(seconds=1)
        )
        request = self.factory.post(
            '/file/uploads/%s/chunk/' % transfer.id,
            data={
                'offset': 0,
                'sha256': hashlib.sha256(content).hexdigest(),
                'chunk': SimpleUploadedFile('chunk', content),
            },
        )
        request.user = self.user
        response = UploadChunkView.as_view()(request, upload_id=transfer.id)
        self.assertIn(
            '已过期', json.loads(response.content.decode('utf-8'))['error']
        )
        transfer.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(transfer.status, 'expired')
        self.assertEqual(batch.status, 'failed')

    def test_batch_cancel_marks_active_transfers_for_scheduled_cleanup(self):
        _, batch = self.create_batch()
        request = self.factory.delete('/file/upload-batches/%s/' % batch.id)
        request.user = self.user
        response = UploadBatchDetailView.as_view()(request, batch_id=batch.id)
        body = json.loads(response.content.decode('utf-8'))
        self.assertFalse(body['error'])
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'cancelled')
        self.assertFalse(batch.transfers.filter(status='active').exists())
        self.assertEqual(
            set(batch.transfers.values_list('cleanup_status', flat=True)),
            {'pending'},
        )
        self.assertTrue(AuditEvent.objects.filter(
            correlation_id=batch.correlation_id,
            action='file.write',
            result='cancelled',
        ).exists())

    def test_cancelling_only_child_refreshes_batch_to_failed(self):
        _, batch = self.create_batch(files=[self.batch_files()[0]])
        transfer = batch.transfers.get()
        request = self.factory.delete('/file/uploads/%s/' % transfer.id)
        request.user = self.user
        with patch(
            'apps.host.models.Host.get_ssh',
            return_value=CleanupSSHStub(True),
        ):
            response = UploadSessionDetailView.as_view()(
                request, upload_id=transfer.id
            )
        self.assertFalse(json.loads(response.content.decode('utf-8'))['error'])
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertTrue(AuditEvent.objects.filter(
            correlation_id=batch.correlation_id,
            action='file.write',
            result='failed',
            details__contains='"batch_id"',
        ).exists())

    def test_batch_retry_resets_cleaned_failed_transfer(self):
        _, batch = self.create_batch(files=[self.batch_files()[0]])
        transfer = batch.transfers.get()
        FileTransfer.objects.filter(pk=transfer.id).update(
            status='failed', offset=8, actual_sha256='f' * 64,
            error='checksum mismatch', cleanup_status='pending',
        )
        FileTransferBatch.objects.filter(pk=batch.id).update(
            status='failed', error='child failed'
        )
        with patch(
            'apps.host.models.Host.get_ssh',
            return_value=CleanupSSHStub(True),
        ):
            body = self.batch_action(batch, 'retry')
        self.assertFalse(body['error'])
        transfer.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'active')
        self.assertEqual(transfer.status, 'active')
        self.assertEqual(transfer.offset, 0)
        self.assertIsNone(transfer.actual_sha256)
        self.assertIsNone(transfer.error)
        self.assertEqual(transfer.cleanup_status, 'pending')
        self.assertEqual(transfer.cleanup_attempts, 1)

    def test_batch_retry_keeps_failure_when_remote_cleanup_fails(self):
        for remote_error in (
            PermissionError('permission denied'),
            ConnectionError('connection lost'),
        ):
            with self.subTest(error=type(remote_error).__name__):
                _, batch = self.create_batch(files=[self.batch_files()[0]])
                transfer = batch.transfers.get()
                FileTransfer.objects.filter(pk=transfer.id).update(
                    status='failed', error='upload failed', cleanup_status='pending'
                )
                FileTransferBatch.objects.filter(pk=batch.id).update(
                    status='failed', error='child failed'
                )
                with patch(
                    'apps.host.models.Host.get_ssh',
                    return_value=CleanupSSHStub(remote_error),
                ):
                    body = self.batch_action(batch, 'retry')
                self.assertIn('尚未清理', body['error'])
                transfer.refresh_from_db()
                batch.refresh_from_db()
                self.assertEqual(transfer.status, 'failed')
                self.assertEqual(transfer.cleanup_status, 'failed')
                self.assertEqual(transfer.cleanup_attempts, 1)
                self.assertEqual(batch.status, 'failed')

    def test_batch_transitions_to_completed_after_all_files_complete(self):
        _, batch = self.create_batch()
        digest = hashlib.sha256(b'').hexdigest()
        for transfer in batch.transfers.order_by('sequence'):
            request = self.factory.post(
                '/file/uploads/%s/complete/' % transfer.id
            )
            request.user = self.user
            with patch(
                'apps.host.models.Host.get_ssh',
                return_value=CompleteSSHStub(digest),
            ):
                response = UploadCompleteView.as_view()(
                    request, upload_id=transfer.id
                )
            self.assertFalse(
                json.loads(response.content.decode('utf-8'))['error']
            )
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'completed')
        self.assertIsNotNone(batch.completed_at)
        self.assertTrue(AuditEvent.objects.filter(
            correlation_id=batch.correlation_id,
            action='file.write',
            result='succeeded',
            details__contains='"batch_id"',
        ).exists())

    def test_batch_transitions_to_failed_when_final_child_fails(self):
        _, batch = self.create_batch()
        transfers = list(batch.transfers.order_by('sequence'))
        FileTransfer.objects.filter(pk=transfers[0].id).update(
            status='completed', actual_sha256=hashlib.sha256(b'').hexdigest(),
            cleanup_status='removed', completed_at=datetime.now(),
        )
        request = self.factory.post(
            '/file/uploads/%s/complete/' % transfers[1].id
        )
        request.user = self.user
        with patch(
            'apps.host.models.Host.get_ssh',
            return_value=CompleteSSHStub('f' * 64),
        ):
            response = UploadCompleteView.as_view()(
                request, upload_id=transfers[1].id
            )
        self.assertIn(
            'SHA-256', json.loads(response.content.decode('utf-8'))['error']
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'failed')
        self.assertIsNone(batch.completed_at)
        self.assertTrue(AuditEvent.objects.filter(
            correlation_id=batch.correlation_id,
            action='file.write',
            result='failed',
            details__contains='"batch_id"',
        ).exists())

    def test_batch_views_only_return_current_users_batches(self):
        _, batch = self.create_batch()
        list_request = self.factory.get('/file/upload-batches/')
        list_request.user = self.approver
        listed = UploadBatchView.as_view()(list_request)
        listed_body = json.loads(listed.content.decode('utf-8'))
        self.assertFalse(listed_body['error'])
        self.assertEqual(listed_body['data'], [])

        detail_request = self.factory.get(
            '/file/upload-batches/%s/' % batch.id
        )
        detail_request.user = self.approver
        detail = UploadBatchDetailView.as_view()(
            detail_request, batch_id=batch.id
        )
        self.assertIn(
            '未找到批量上传任务',
            json.loads(detail.content.decode('utf-8'))['error'],
        )

    def test_batch_routes_resolve(self):
        self.assertIs(
            resolve('/file/upload-batches/').func.view_class,
            UploadBatchView,
        )
        self.assertIs(
            resolve('/file/upload-batches/%s/' % uuid.uuid4()).func.view_class,
            UploadBatchDetailView,
        )

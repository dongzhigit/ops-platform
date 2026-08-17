import errno
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import RequestFactory, TestCase

from apps.account.models import User
from apps.audit.models import ApprovalRequest, AuditEvent
from apps.file.models import FileTransfer
from apps.file.services import MAX_CLEANUP_ATTEMPTS, cleanup_expired_file_transfers
from apps.file.utils import remote_file_etag
from apps.file.views import ObjectView, UploadSessionDetailView
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

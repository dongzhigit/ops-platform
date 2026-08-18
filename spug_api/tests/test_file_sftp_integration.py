import hashlib
import json
import os
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from unittest import skipUnless

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import RequestFactory, TransactionTestCase

from apps.account.models import User
from apps.assets.models import AssetIdentityBinding, Credential, Identity
from apps.audit.services import create_approval, decide_approval
from apps.file.models import FileEditSession, FileTransfer, FileTransferBatch
from apps.file.services import cleanup_expired_file_transfers
from apps.file.views import (
    EditSessionSaveView,
    EditSessionView,
    UploadBatchDetailView,
    UploadBatchView,
    UploadChunkView,
    UploadCompleteView,
)
from apps.host.models import Host


SFTP_ENABLED = bool(os.environ.get('SPUG_TEST_SFTP_HOST'))
CHUNK_SIZE = 256 * 1024


@skipUnless(SFTP_ENABLED, 'real SFTP integration environment is not configured')
class FileBatchSFTPIntegrationTest(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = self.make_user('sftp-requester')
        self.approver = self.make_user('sftp-approver')
        credential = Credential.create_with_secret(
            os.environ['SPUG_TEST_SFTP_PASSWORD'],
            name='sftp-integration-password',
            type='password',
            created_by=self.user,
        )
        identity = Identity.objects.create(
            name='sftp-integration-identity',
            protocol='ssh',
            username=os.environ.get('SPUG_TEST_SFTP_USER', 'spug'),
            credential=credential,
            created_by=self.user,
        )
        self.host = Host.objects.create(
            name='sftp-integration-host',
            hostname=os.environ['SPUG_TEST_SFTP_HOST'],
            port=int(os.environ.get('SPUG_TEST_SFTP_PORT', '22')),
            username=identity.username,
            created_by=self.user,
        )
        AssetIdentityBinding.bind(
            host=self.host,
            identity=identity,
            created_by=self.user,
            is_default=True,
        )
        self.directory = os.environ.get('SPUG_TEST_SFTP_DIRECTORY', '/upload')
        self.prefix = 'alpha8-%s-' % uuid.uuid4().hex[:10]

    @staticmethod
    def make_user(username):
        return User.objects.create(
            username=username,
            nickname=username,
            password_hash='unused',
            type='default',
            is_supper=True,
            is_active=True,
            access_token=(username + ('x' * 32))[:32],
            token_expired=0,
            last_login='',
            last_ip='127.0.0.1',
        )

    @staticmethod
    def response_data(response):
        body = json.loads(response.content.decode('utf-8'))
        if body['error']:
            raise AssertionError(body['error'])
        return body['data']

    def create_batch(self, files):
        files = sorted(files, key=lambda item: item['filename'])
        payload = {
            'host_ids': [self.host.id],
            'path': self.directory,
            'files': files,
            'chunk_size': CHUNK_SIZE,
            'conflict_strategy': 'overwrite',
            'max_concurrency': 2,
        }
        approval = create_approval(
            requester=self.user,
            action='file.write',
            resource_type='host',
            resource_ids=[self.host.id],
            payload=payload,
            summary='真实 SFTP 批量上传集成测试',
        )
        decide_approval(
            approval_id=approval.id,
            approver=self.approver,
            is_pass=True,
        )
        request = RequestFactory().post(
            '/file/upload-batches/',
            data=json.dumps(dict(
                payload,
                id=self.host.id,
                approval_id=str(approval.id),
            )),
            content_type='application/json',
        )
        request.user = self.user
        data = self.response_data(UploadBatchView.as_view()(request))
        return FileTransferBatch.objects.get(pk=data['id'])

    def batch_action(self, batch_id, action):
        request = RequestFactory().patch(
            '/file/upload-batches/%s/' % batch_id,
            data=json.dumps({'action': action}),
            content_type='application/json',
        )
        request.user = self.user
        response = UploadBatchDetailView.as_view()(
            request, batch_id=batch_id
        )
        return self.response_data(response)

    def write_transfer(self, transfer_id, content, *, max_chunks=None,
                       complete=True):
        close_old_connections()
        try:
            transfer = FileTransfer.objects.get(pk=transfer_id)
            offset = transfer.offset
            chunks_written = 0
            while offset < len(content):
                if max_chunks is not None and chunks_written >= max_chunks:
                    break
                chunk = content[offset:offset + transfer.chunk_size]
                request = RequestFactory().post(
                    '/file/uploads/%s/chunk/' % transfer.id,
                    data={
                        'offset': offset,
                        'sha256': hashlib.sha256(chunk).hexdigest(),
                        'chunk': SimpleUploadedFile(
                            transfer.filename, chunk
                        ),
                    },
                )
                request.user = User.objects.get(pk=self.user.id)
                data = self.response_data(UploadChunkView.as_view()(
                    request, upload_id=transfer.id
                ))
                offset = data['offset']
                chunks_written += 1
            if complete and offset == len(content):
                request = RequestFactory().post(
                    '/file/uploads/%s/complete/' % transfer.id
                )
                request.user = User.objects.get(pk=self.user.id)
                return self.response_data(UploadCompleteView.as_view()(
                    request, upload_id=transfer.id
                ))
            return FileTransfer.objects.get(pk=transfer.id).to_view()
        finally:
            close_old_connections()

    def complete_transfer(self, transfer_id):
        request = RequestFactory().post(
            '/file/uploads/%s/complete/' % transfer_id
        )
        request.user = self.user
        return UploadCompleteView.as_view()(request, upload_id=transfer_id)

    def test_concurrent_pause_retry_cancel_and_cleanup_against_real_sftp(self):
        first_content = (b'first-file-' * 70000) + b'end'
        second_content = (b'second-file-' * 65000) + b'end'
        files = [
            {
                'filename': self.prefix + 'first.bin',
                'size': len(first_content),
                'sha256': hashlib.sha256(first_content).hexdigest(),
            },
            {
                'filename': self.prefix + 'second.bin',
                'size': len(second_content),
                'sha256': hashlib.sha256(second_content).hexdigest(),
            },
        ]
        batch = self.create_batch(files)
        self.assertEqual(self.batch_action(batch.id, 'pause')['status'], 'paused')
        blocked_transfer = batch.transfers.order_by('sequence').first()
        blocked_chunk = first_content[:CHUNK_SIZE]
        blocked_request = RequestFactory().post(
            '/file/uploads/%s/chunk/' % blocked_transfer.id,
            data={
                'offset': 0,
                'sha256': hashlib.sha256(blocked_chunk).hexdigest(),
                'chunk': SimpleUploadedFile('blocked', blocked_chunk),
            },
        )
        blocked_request.user = self.user
        blocked_body = json.loads(UploadChunkView.as_view()(
            blocked_request, upload_id=blocked_transfer.id
        ).content.decode('utf-8'))
        self.assertIn('已暂停或终止', blocked_body['error'])
        self.assertEqual(self.batch_action(batch.id, 'resume')['status'], 'active')

        transfers = list(batch.transfers.order_by('sequence'))
        content_by_name = {
            files[0]['filename']: first_content,
            files[1]['filename']: second_content,
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self.write_transfer,
                    transfer.id,
                    content_by_name[transfer.filename],
                )
                for transfer in transfers
            ]
            for future in futures:
                self.assertEqual(future.result()['status'], 'completed')
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'completed')
        with self.host.get_ssh() as ssh:
            for item in files:
                self.assertEqual(
                    ssh.remote_file_sha256(
                        self.directory + '/' + item['filename']
                    ),
                    item['sha256'],
                )

        retry_content = (b'retry-after-corruption-' * 40000) + b'end'
        retry_file = {
            'filename': self.prefix + 'retry.bin',
            'size': len(retry_content),
            'sha256': hashlib.sha256(retry_content).hexdigest(),
        }
        retry_batch = self.create_batch([retry_file])
        retry_transfer = retry_batch.transfers.get()
        self.write_transfer(retry_transfer.id, retry_content, complete=False)
        retry_transfer.refresh_from_db()
        with self.host.get_ssh() as ssh:
            ssh.write_file_chunk(
                BytesIO(b'z' * len(retry_content)),
                retry_transfer.temporary_path,
                0,
            )
        failed_body = json.loads(self.complete_transfer(
            retry_transfer.id
        ).content.decode('utf-8'))
        self.assertIn('SHA-256', failed_body['error'])
        retry_batch.refresh_from_db()
        self.assertEqual(retry_batch.status, 'failed')
        self.assertEqual(self.batch_action(retry_batch.id, 'retry')['status'], 'active')
        self.assertEqual(
            self.write_transfer(retry_transfer.id, retry_content)['status'],
            'completed',
        )

        cancel_content = b'cancel-me-' * 80000
        cancel_file = {
            'filename': self.prefix + 'cancel.bin',
            'size': len(cancel_content),
            'sha256': hashlib.sha256(cancel_content).hexdigest(),
        }
        cancel_batch = self.create_batch([cancel_file])
        cancel_transfer = cancel_batch.transfers.get()
        self.write_transfer(
            cancel_transfer.id, cancel_content,
            max_chunks=1, complete=False,
        )
        request = RequestFactory().delete(
            '/file/upload-batches/%s/' % cancel_batch.id
        )
        request.user = self.user
        cancelled = self.response_data(UploadBatchDetailView.as_view()(
            request, batch_id=cancel_batch.id
        ))
        self.assertEqual(cancelled['status'], 'cancelled')
        cleanup = cleanup_expired_file_transfers(execute=True)
        self.assertGreaterEqual(cleanup['removed'], 1)
        cancel_transfer.refresh_from_db()
        self.assertEqual(cancel_transfer.cleanup_status, 'removed')

        edit_path = self.directory + '/' + self.prefix + 'edit.conf'
        initial_content = 'name=old\n说明=初始\n'.encode('utf-8')
        saved_content = 'name=new\n说明=安全保存\n'.encode('utf-8')
        with self.host.get_ssh() as ssh:
            ssh.write_file_chunk(BytesIO(initial_content), edit_path, 0)
            original_stat = ssh.sftp_stat(edit_path)
            ssh.set_file_attributes(
                edit_path,
                0o640,
                original_stat.st_uid,
                original_stat.st_gid,
            )
            original_stat = ssh.sftp_stat(edit_path)
        request = RequestFactory().post(
            '/file/edit-sessions/',
            data=json.dumps({'id': self.host.id, 'file': edit_path}),
            content_type='application/json',
        )
        request.user = self.user
        opened = self.response_data(EditSessionView.as_view()(request))
        self.assertEqual(opened['content'].encode('utf-8'), initial_content)
        edit_session = FileEditSession.objects.get(
            session_id=opened['session_id']
        )
        edit_payload = {
            'host_ids': [self.host.id],
            'file': edit_path,
            'base_etag': edit_session.base_etag,
            'base_sha256': edit_session.base_sha256,
            'size': len(saved_content),
            'sha256': hashlib.sha256(saved_content).hexdigest(),
            'encoding': 'utf-8',
            'conflict_strategy': 'reject_on_change',
        }
        edit_approval = create_approval(
            requester=self.user,
            action='file.write',
            resource_type='host',
            resource_ids=[self.host.id],
            payload=edit_payload,
            summary='真实 SFTP 在线编辑集成测试',
        )
        decide_approval(
            approval_id=edit_approval.id,
            approver=self.approver,
            is_pass=True,
        )
        request = RequestFactory().post(
            '/file/edit-sessions/%s/save/' % edit_session.session_id,
            data=json.dumps({
                'content': saved_content.decode('utf-8'),
                'sha256': edit_payload['sha256'],
                'base_etag': edit_payload['base_etag'],
                'base_sha256': edit_payload['base_sha256'],
                'approval_id': str(edit_approval.id),
            }),
            content_type='application/json',
        )
        request.user = self.user
        saved = self.response_data(EditSessionSaveView.as_view()(
            request, session_id=edit_session.session_id
        ))
        self.assertEqual(saved['status'], 'saved')
        with self.host.get_ssh() as ssh:
            self.assertEqual(
                ssh.remote_file_sha256(edit_path),
                hashlib.sha256(saved_content).hexdigest(),
            )
            self.assertEqual(
                stat.S_IMODE(ssh.sftp_stat(edit_path).st_mode),
                0o640,
            )
            saved_stat = ssh.sftp_stat(edit_path)
            self.assertEqual(saved_stat.st_uid, original_stat.st_uid)
            self.assertEqual(saved_stat.st_gid, original_stat.st_gid)

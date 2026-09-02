# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import http_date
from django_redis import get_redis_connection
from apps.host.models import Host
from apps.account.utils import has_host_perm
from apps.audit.services import (
    ApprovalError,
    authorize_operation,
    record_event,
    verify_consumed_approval,
)
from apps.file.models import (
    FileEditSession,
    FileTransfer,
    FileTransferBatch,
    file_edit_expiry,
    file_transfer_expiry,
)
from apps.file.services import (
    MAX_CLEANUP_ATTEMPTS,
    cleanup_transfer_temporary_file,
    refresh_file_transfer_batch,
)
from apps.file.utils import (
    RemoteFileIterator,
    attachment_header,
    fetch_dir_list,
    parse_http_range,
    remote_file_etag,
)
from libs import json_response, JsonParser, Argument, auth
from functools import partial
from datetime import datetime
from io import BytesIO
import hashlib
import logging
import posixpath
import re
import uuid


logger = logging.getLogger(__name__)
MIN_CHUNK_SIZE = 256 * 1024
MAX_CHUNK_SIZE = 16 * 1024 * 1024
MAX_FILE_SIZE = 1024 * 1024 * 1024 * 1024
MAX_BATCH_FILES = 50
MAX_BATCH_TOTAL_SIZE = 5 * MAX_FILE_SIZE
MAX_BATCH_CONCURRENCY = 4
MAX_EDIT_SIZE = 2 * 1024 * 1024
SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')


class FileTransferError(Exception):
    pass


def _normalize_upload_target(directory, filename):
    if '\x00' in directory or '\x00' in filename:
        raise FileTransferError('文件路径包含非法字符')
    if not directory.startswith('/'):
        raise FileTransferError('上传目录必须是绝对路径')
    directory = posixpath.normpath(directory)
    normalized_name = posixpath.basename(filename)
    if normalized_name != filename or normalized_name in ('', '.', '..'):
        raise FileTransferError('文件名不合法')
    if len(filename.encode('utf-8')) > 255:
        raise FileTransferError('文件名过长')
    return directory, normalized_name


def _file_sha256(file_obj):
    digest = hashlib.sha256()
    file_obj.seek(0)
    block = file_obj.read(1024 * 1024)
    while block:
        digest.update(block)
        block = file_obj.read(1024 * 1024)
    file_obj.seek(0)
    return digest.hexdigest()


def _get_user_transfer(upload_id, user, for_update=False):
    if for_update:
        # Lock only the transfer row. Locking joined host/batch rows would
        # serialize otherwise independent files in the same batch.
        transfers = FileTransfer.objects.select_for_update()
    else:
        transfers = FileTransfer.objects.select_related(
            'host', 'approval', 'batch'
        )
    transfer = transfers.filter(pk=upload_id, uploader=user).first()
    if not transfer:
        raise FileTransferError('未找到上传会话')
    if not has_host_perm(user, transfer.host_id, action='file.write'):
        raise FileTransferError('无权访问主机，请联系管理员')
    return transfer


def _get_user_batch(batch_id, user, for_update=False):
    batches = FileTransferBatch.objects.select_related('host', 'approval')
    if for_update:
        batches = batches.select_for_update()
    batch = batches.filter(pk=batch_id, creator=user).first()
    if not batch:
        raise FileTransferError('未找到批量上传任务')
    if not has_host_perm(user, batch.host_id, action='file.write'):
        raise FileTransferError('无权访问主机，请联系管理员')
    return batch


def _normalize_batch_files(files):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_BATCH_FILES:
        raise FileTransferError('批量上传一次必须选择 1 到 50 个文件')
    normalized = []
    names = set()
    total_size = 0
    for item in files:
        if not isinstance(item, dict):
            raise FileTransferError('批量上传文件参数不合法')
        filename = item.get('filename')
        if not isinstance(filename, str):
            raise FileTransferError('批量上传文件名不合法')
        _, filename = _normalize_upload_target('/', filename)
        if filename in names:
            raise FileTransferError('批量上传不能包含同名文件')
        size = item.get('size')
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_SIZE:
            raise FileTransferError('批量上传文件大小超出允许范围')
        digest = str(item.get('sha256') or '').lower()
        if not SHA256_PATTERN.match(digest):
            raise FileTransferError('批量上传文件 SHA-256 不合法')
        names.add(filename)
        total_size += size
        normalized.append({
            'filename': filename,
            'size': size,
            'sha256': digest,
        })
    if total_size > MAX_BATCH_TOTAL_SIZE:
        raise FileTransferError('批量上传总大小超出 5 TiB 限制')
    return sorted(normalized, key=lambda item: item['filename']), total_size


def _normalize_edit_path(remote_path):
    if not isinstance(remote_path, str) or '\x00' in remote_path:
        raise FileTransferError('文件路径包含非法字符')
    if not remote_path.startswith('/'):
        raise FileTransferError('文件路径必须是绝对路径')
    normalized = posixpath.normpath(remote_path)
    filename = posixpath.basename(normalized)
    if normalized == '/' or filename in ('', '.', '..'):
        raise FileTransferError('只能在线编辑普通文件')
    if len(filename.encode('utf-8')) > 255 or len(normalized) > 1280:
        raise FileTransferError('文件路径过长')
    return normalized, filename


def _edit_path_hash(remote_path):
    return hashlib.sha256(remote_path.encode('utf-8')).hexdigest()


def _get_user_edit_session(session_id, user, for_update=False):
    sessions = FileEditSession.objects
    if for_update:
        sessions = sessions.select_for_update()
    else:
        sessions = sessions.select_related('host', 'approval')
    session = sessions.filter(session_id=session_id, editor=user).first()
    if not session:
        raise FileTransferError('未找到在线编辑会话')
    if not has_host_perm(user, session.host_id, action='file.read') or not (
        has_host_perm(user, session.host_id, action='file.write')
    ):
        raise FileTransferError('无权编辑主机文件，请联系管理员')
    return session


class FileView(View):
    @auth('host.console.list')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('path', help='参数错误')
        ).parse(request.GET)
        if error is None:
            if not has_host_perm(request.user, form.id, action='file.read'):
                return json_response(error='无权访问主机，请联系管理员')
            host = Host.objects.get(pk=form.id)
            if not host:
                return json_response(error='未找到指定主机')
            objects = fetch_dir_list(host, form.path)
            return json_response(objects)
        return json_response(error=error)


class ObjectView(View):
    @auth('host.console.list')
    def get(self, request):
        return self._download(request, include_body=True)

    @auth('host.console.list')
    def head(self, request):
        return self._download(request, include_body=False)

    def _download(self, request, include_body):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('file', help='请输入文件路径')
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        if not has_host_perm(request.user, form.id, action='file.read'):
            return json_response(error='无权访问主机，请联系管理员')
        host = Host.objects.filter(pk=form.id).first()
        if not host:
            return json_response(error='未找到指定主机')

        filename = posixpath.basename(form.file)
        ssh_cli = host.get_ssh().get_client()
        sftp = ssh_cli.open_sftp()
        try:
            file_stat = sftp.stat(form.file)
            size = int(file_stat.st_size or 0)
            etag = remote_file_etag(host.id, form.file, file_stat)
            last_modified = http_date(file_stat.st_mtime) if file_stat.st_mtime else None
            range_header = request.META.get('HTTP_RANGE')
            if_range = request.META.get('HTTP_IF_RANGE')
            if range_header and if_range and if_range not in (etag, last_modified):
                range_header = None
            try:
                byte_range = parse_http_range(range_header, size)
            except ValueError:
                ssh_cli.close()
                response = HttpResponse(status=416)
                response['Accept-Ranges'] = 'bytes'
                response['Content-Range'] = 'bytes */%s' % size
                response['Cache-Control'] = 'no-store'
                return response

            if byte_range:
                start, end = byte_range
                status = 206
            else:
                start, end = 0, max(size - 1, -1)
                status = 200
            length = max(end - start + 1, 0)
            correlation_id = uuid.uuid4()
            record_event(
                correlation_id=correlation_id,
                actor=request.user,
                action='file.read',
                resource_type='host',
                resource_id=host.id,
                result='started',
                details={
                    'file': form.file,
                    'size': size,
                    'range_start': start,
                    'range_end': end,
                    'head_only': not include_body,
                },
                request=request,
            )

            def finish_download(completed, bytes_sent):
                try:
                    ssh_cli.close()
                except Exception:
                    logger.exception('failed to close SFTP download connection')
                try:
                    record_event(
                        correlation_id=correlation_id,
                        actor=request.user,
                        action='file.read',
                        resource_type='host',
                        resource_id=host.id,
                        result='succeeded' if completed else 'failed',
                        details={
                            'file': form.file,
                            'size': size,
                            'range_start': start,
                            'range_end': end,
                            'bytes_sent': bytes_sent,
                            'head_only': not include_body,
                        },
                        request=request,
                    )
                except Exception:
                    logger.exception('failed to record SFTP download audit event')

            if include_body:
                remote_file = None
                try:
                    remote_file = sftp.open(form.file, 'rb')
                    iterator = RemoteFileIterator(
                        remote_file, start, length, close_callback=finish_download
                    )
                except Exception:
                    if remote_file is not None:
                        try:
                            remote_file.close()
                        except Exception:
                            logger.exception('failed to close remote SFTP file')
                    finish_download(False, 0)
                    raise
                response = StreamingHttpResponse(
                    iterator, status=status, content_type='application/octet-stream'
                )
            else:
                finish_download(True, 0)
                response = HttpResponse(status=status, content_type='application/octet-stream')

            response['Accept-Ranges'] = 'bytes'
            response['Content-Length'] = str(length)
            response['Content-Disposition'] = attachment_header(filename)
            response['ETag'] = etag
            response['Cache-Control'] = 'no-store'
            response['X-Accel-Buffering'] = 'no'
            if last_modified:
                response['Last-Modified'] = last_modified
            if status == 206:
                response['Content-Range'] = 'bytes %s-%s/%s' % (start, end, size)
            return response
        except Exception:
            try:
                ssh_cli.close()
            except Exception:
                logger.exception('failed to close SFTP download connection')
            raise

    @auth('host.console.upload')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('token', help='参数错误'),
            Argument('path', help='参数错误'),
            Argument('approval_id', required=False),
        ).parse(request.POST)
        if error is None:
            if not has_host_perm(request.user, form.id, action='file.write'):
                return json_response(error='无权访问主机，请联系管理员')
            file = request.FILES.get('file')
            if not file:
                return json_response(error='请选择要上传的文件')
            host = Host.objects.get(pk=form.id)
            if not host:
                return json_response(error='未找到指定主机')
            payload = {
                'host_ids': [form.id],
                'path': form.path,
                'filename': file.name,
                'size': file.size,
            }
            try:
                gate = authorize_operation(
                    requester=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_ids=[form.id],
                    payload=payload,
                    approval_id=form.approval_id,
                    execution_ref=form.token,
                    request=request,
                )
            except ApprovalError as exc:
                return json_response(error=exc.message)
            record_event(
                correlation_id=gate['correlation_id'],
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=form.id,
                result='started',
                details={'path': form.path, 'filename': file.name, 'size': file.size},
                request=request,
            )
            try:
                rds_cli = get_redis_connection()
                callback = partial(self._compute_progress, rds_cli, form.token, file.size)
                with host.get_ssh() as ssh:
                    ssh.put_file_by_fl(file, f'{form.path}/{file.name}', callback=callback)
            except Exception as exc:
                record_event(
                    correlation_id=gate['correlation_id'],
                    actor=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_id=form.id,
                    result='failed',
                    details={'error_type': type(exc).__name__, 'filename': file.name},
                    request=request,
                )
                raise
            record_event(
                correlation_id=gate['correlation_id'],
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=form.id,
                result='succeeded',
                details={'path': form.path, 'filename': file.name, 'size': file.size},
                request=request,
            )
        return json_response(error=error)

    @auth('host.console.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('file', help='请输入文件路径'),
            Argument('approval_id', required=False),
        ).parse(request.GET)
        if error is None:
            if not has_host_perm(request.user, form.id, action='file.write'):
                return json_response(error='无权访问主机，请联系管理员')
            host = Host.objects.get(pk=form.id)
            if not host:
                return json_response(error='未找到指定主机')
            payload = {'host_ids': [form.id], 'file': form.file}
            try:
                gate = authorize_operation(
                    requester=request.user,
                    action='file.delete',
                    resource_type='host',
                    resource_ids=[form.id],
                    payload=payload,
                    approval_id=form.approval_id,
                    request=request,
                )
            except ApprovalError as exc:
                return json_response(error=exc.message)
            record_event(
                correlation_id=gate['correlation_id'],
                actor=request.user,
                action='file.delete',
                resource_type='host',
                resource_id=form.id,
                result='started',
                details={'file': form.file},
                request=request,
            )
            try:
                with host.get_ssh() as ssh:
                    ssh.remove_file(form.file)
            except Exception as exc:
                record_event(
                    correlation_id=gate['correlation_id'],
                    actor=request.user,
                    action='file.delete',
                    resource_type='host',
                    resource_id=form.id,
                    result='failed',
                    details={'file': form.file, 'error_type': type(exc).__name__},
                    request=request,
                )
                raise
            record_event(
                correlation_id=gate['correlation_id'],
                actor=request.user,
                action='file.delete',
                resource_type='host',
                resource_id=form.id,
                result='succeeded',
                details={'file': form.file},
                request=request,
            )
        return json_response(error=error)

    def _compute_progress(self, rds_cli, token, total, value, *args):
        percent = '%.1f' % (value / total * 100)
        rds_cli.publish(token, percent)


class EditSessionView(View):
    @auth('host.console.upload')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('file', help='请输入文件路径'),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        if not has_host_perm(request.user, form.id, action='file.read') or not (
            has_host_perm(request.user, form.id, action='file.write')
        ):
            return json_response(error='无权编辑主机文件，请联系管理员')
        host = Host.objects.filter(pk=form.id).first()
        if not host:
            return json_response(error='未找到指定主机')
        try:
            remote_path, filename = _normalize_edit_path(form.file)
            path_hash = _edit_path_hash(remote_path)
            with transaction.atomic():
                session = FileEditSession.objects.select_for_update().filter(
                    host=host, path_hash=path_hash
                ).first()
                if not session:
                    try:
                        with transaction.atomic():
                            FileEditSession.objects.create(
                                correlation_id=uuid.uuid4(),
                                editor=request.user,
                                host=host,
                                remote_path=remote_path,
                                path_hash=path_hash,
                                filename=filename,
                                temporary_path=posixpath.join(
                                    posixpath.dirname(remote_path),
                                    '.ops-platform-edit-placeholder.part',
                                ),
                                base_size=0,
                                base_mtime=0,
                                base_etag='""',
                                base_sha256='0' * 64,
                                status='expired',
                                cleanup_status='missing',
                                cleanup_completed_at=datetime.now(),
                            )
                    except IntegrityError:
                        pass
                    session = FileEditSession.objects.select_for_update().get(
                        host=host, path_hash=path_hash
                    )
                session.refresh_expiry_status()
                if session.status == 'active' and session.editor_id != request.user.id:
                    raise FileTransferError(
                        '文件正由 %s 编辑，锁将在 %s 过期' % (
                            session.editor.nickname or session.editor.username,
                            session.expires_at,
                        )
                    )
                reuse_active_lock = (
                    session.status == 'active' and
                    session.editor_id == request.user.id
                )
                if not reuse_active_lock and session.cleanup_status in (
                    'pending', 'failed'
                ):
                    raise FileTransferError(
                        '上一个编辑会话的临时文件正在清理，请稍后重试'
                    )
                try:
                    with host.get_ssh() as ssh:
                        raw_content, file_stat = ssh.read_file(
                            remote_path, MAX_EDIT_SIZE
                        )
                except ValueError:
                    raise FileTransferError(
                        '在线编辑仅支持不超过 2 MiB 的普通文本文件'
                    )
                try:
                    content = raw_content.decode('utf-8')
                except UnicodeDecodeError:
                    raise FileTransferError('在线编辑仅支持 UTF-8 文本文件')
                if '\x00' in content:
                    raise FileTransferError('在线编辑不支持包含 NUL 的二进制文件')

                edit_session_id = (
                    session.session_id if reuse_active_lock else uuid.uuid4()
                )
                session.session_id = edit_session_id
                session.correlation_id = uuid.uuid4()
                session.editor = request.user
                session.approval = None
                session.remote_path = remote_path
                session.filename = filename
                if not reuse_active_lock:
                    session.temporary_path = posixpath.join(
                        posixpath.dirname(remote_path),
                        '.ops-platform-edit-%s.part' % edit_session_id.hex,
                    )
                session.base_size = len(raw_content)
                session.base_mtime = int(file_stat.st_mtime or 0)
                session.base_etag = remote_file_etag(
                    host.id, remote_path, file_stat
                )
                session.base_sha256 = hashlib.sha256(raw_content).hexdigest()
                session.status = 'active'
                session.error = None
                session.expires_at = file_edit_expiry()
                session.completed_at = None
                session.cleanup_status = 'pending'
                session.cleanup_attempts = 0
                session.cleanup_attempted_at = None
                session.cleanup_completed_at = None
                session.cleanup_error = None
                session.save(update_fields=(
                    'session_id', 'correlation_id', 'editor', 'approval',
                    'remote_path', 'filename', 'temporary_path', 'base_size',
                    'base_mtime', 'base_etag', 'base_sha256', 'status', 'error',
                    'expires_at', 'completed_at', 'cleanup_status',
                    'cleanup_attempts', 'cleanup_attempted_at',
                    'cleanup_completed_at', 'cleanup_error', 'updated_at',
                ))
            record_event(
                correlation_id=session.correlation_id,
                actor=request.user,
                action='file.edit',
                resource_type='host',
                resource_id=host.id,
                result='started',
                details={
                    'edit_session_id': str(session.session_id),
                    'file': remote_path,
                    'base_size': session.base_size,
                    'base_sha256': session.base_sha256,
                    'expires_at': session.expires_at,
                },
                request=request,
            )
            data = session.to_view()
            data['content'] = content
            return json_response(data)
        except FileTransferError as exc:
            return json_response(error=str(exc))
        except Exception:
            logger.exception('failed to open SFTP file edit session')
            return json_response(error='读取远端文件失败，请检查文件和主机连接')


class EditSessionDetailView(View):
    @auth('host.console.upload')
    def get(self, request, session_id):
        try:
            with transaction.atomic():
                session = _get_user_edit_session(
                    session_id, request.user, for_update=True
                )
                session.refresh_expiry_status()
            return json_response(session.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))

    @auth('host.console.upload')
    def patch(self, request, session_id):
        form, error = JsonParser(
            Argument(
                'action', filter=lambda value: value == 'heartbeat',
                help='不支持的在线编辑操作',
            ),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        try:
            with transaction.atomic():
                session = _get_user_edit_session(
                    session_id, request.user, for_update=True
                )
                if session.refresh_expiry_status() or session.status != 'active':
                    raise FileTransferError('在线编辑锁已失效，请重新打开文件')
                session.expires_at = file_edit_expiry()
                session.error = None
                session.save(update_fields=(
                    'expires_at', 'error', 'updated_at'
                ))
            return json_response(session.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))

    @auth('host.console.upload')
    def delete(self, request, session_id):
        try:
            with transaction.atomic():
                session = _get_user_edit_session(
                    session_id, request.user, for_update=True
                )
                session.refresh_expiry_status()
                if session.status in ('saved', 'cancelled'):
                    return json_response(session.to_view())
                outcome = cleanup_transfer_temporary_file(session)
                attempted = outcome is not None
                outcome = outcome or session.cleanup_status
                if session.status == 'active':
                    session.status = 'cancelled'
                    session.error = None
                session.completed_at = datetime.now()
                session.save(update_fields=(
                    'status', 'error', 'completed_at', 'cleanup_status',
                    'cleanup_attempts', 'cleanup_attempted_at',
                    'cleanup_completed_at', 'cleanup_error', 'updated_at',
                ))
            record_event(
                correlation_id=session.correlation_id,
                actor=request.user,
                action='file.edit',
                resource_type='host',
                resource_id=session.host_id,
                result='cancelled',
                details={
                    'edit_session_id': str(session.session_id),
                    'file': session.remote_path,
                    'cleanup_outcome': outcome,
                },
                request=request,
            )
            if attempted:
                record_event(
                    correlation_id=session.correlation_id,
                    actor=request.user,
                    action='file.cleanup',
                    resource_type='host',
                    resource_id=session.host_id,
                    result='failed' if outcome == 'failed' else 'succeeded',
                    details={
                        'edit_session_id': str(session.session_id),
                        'file': session.remote_path,
                        'cleanup_outcome': outcome,
                        'trigger': 'edit_cancelled',
                    },
                    request=request,
                )
            return json_response(session.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))


class EditSessionSaveView(View):
    @auth('host.console.upload')
    def post(self, request, session_id):
        form, error = JsonParser(
            Argument('content', default='', required=False),
            Argument(
                'sha256', handler=lambda value: value.lower(),
                filter=lambda value: bool(SHA256_PATTERN.match(value.lower())),
                help='文件 SHA-256 不合法',
            ),
            Argument('base_etag', help='缺少文件基础版本'),
            Argument(
                'base_sha256', handler=lambda value: value.lower(),
                filter=lambda value: bool(SHA256_PATTERN.match(value.lower())),
                help='基础文件 SHA-256 不合法',
            ),
            Argument('approval_id', type=uuid.UUID, help='请指定有效审批单'),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        content = form.content.encode('utf-8')
        if len(content) > MAX_EDIT_SIZE:
            return json_response(error='在线编辑内容不能超过 2 MiB')
        if hashlib.sha256(content).hexdigest() != form.sha256:
            return json_response(error='文件内容与 SHA-256 不匹配')

        operation_error = None
        cleanup_outcome = None
        gate = None
        try:
            with transaction.atomic():
                session = _get_user_edit_session(
                    session_id, request.user, for_update=True
                )
                if session.refresh_expiry_status() or session.status != 'active':
                    raise FileTransferError('在线编辑锁已失效，请重新打开文件')
                if (
                    form.base_etag != session.base_etag or
                    form.base_sha256 != session.base_sha256
                ):
                    raise FileTransferError('编辑基础版本已变化，请重新打开文件')
                payload = {
                    'host_ids': [session.host_id],
                    'file': session.remote_path,
                    'base_etag': session.base_etag,
                    'base_sha256': session.base_sha256,
                    'size': len(content),
                    'sha256': form.sha256,
                    'encoding': 'utf-8',
                    'conflict_strategy': 'reject_on_change',
                }
                temp_attempted = False
                try:
                    with session.host.get_ssh() as ssh:
                        current_stat = ssh.sftp_stat(session.remote_path)
                        current_etag = remote_file_etag(
                            session.host_id, session.remote_path, current_stat
                        )
                        current_sha256 = (
                            ssh.remote_file_sha256(session.remote_path)
                            if current_etag == session.base_etag else None
                        )
                        if (
                            current_etag != session.base_etag or
                            current_sha256 != session.base_sha256
                        ):
                            operation_error = '远端文件已被修改，请重新打开后合并变更'
                        else:
                            gate = authorize_operation(
                                requester=request.user,
                                action='file.write',
                                resource_type='host',
                                resource_ids=[session.host_id],
                                payload=payload,
                                approval_id=form.approval_id,
                                execution_ref=session.session_id,
                                request=request,
                            )
                            session.approval_id = gate['approval_id']
                            temp_attempted = True
                            written = ssh.write_file_chunk(
                                BytesIO(content), session.temporary_path, 0
                            )
                            if written != len(content) or (
                                ssh.remote_file_sha256(session.temporary_path) != form.sha256
                            ):
                                operation_error = '远端临时文件校验失败'
                            else:
                                if current_stat.st_mode:
                                    ssh.set_file_attributes(
                                        session.temporary_path,
                                        current_stat.st_mode,
                                        getattr(current_stat, 'st_uid', None),
                                        getattr(current_stat, 'st_gid', None),
                                    )
                                latest_stat = ssh.sftp_stat(session.remote_path)
                                latest_etag = remote_file_etag(
                                    session.host_id,
                                    session.remote_path,
                                    latest_stat,
                                )
                                latest_sha256 = (
                                    ssh.remote_file_sha256(session.remote_path)
                                    if latest_etag == session.base_etag else None
                                )
                                if (
                                    latest_etag != session.base_etag or
                                    latest_sha256 != session.base_sha256
                                ):
                                    operation_error = '保存期间远端文件发生变化，未覆盖原文件'
                                else:
                                    ssh.replace_file(
                                        session.temporary_path,
                                        session.remote_path,
                                        overwrite=True,
                                    )
                except ApprovalError:
                    raise
                except Exception:
                    logger.exception('failed to save SFTP edited file')
                    operation_error = '保存远端文件失败，请检查连接和目录权限'

                if operation_error:
                    if temp_attempted:
                        cleanup_outcome = cleanup_transfer_temporary_file(session)
                    session.error = operation_error
                    session.save(update_fields=(
                        'approval', 'error', 'cleanup_status',
                        'cleanup_attempts', 'cleanup_attempted_at',
                        'cleanup_completed_at', 'cleanup_error', 'updated_at',
                    ))
                else:
                    session.status = 'saved'
                    session.error = None
                    session.completed_at = datetime.now()
                    session.cleanup_status = 'removed'
                    session.cleanup_completed_at = session.completed_at
                    session.cleanup_error = None
                    session.save(update_fields=(
                        'approval', 'status', 'error', 'completed_at',
                        'cleanup_status', 'cleanup_completed_at',
                        'cleanup_error', 'updated_at',
                    ))
            correlation_id = (
                gate['correlation_id'] if gate else session.correlation_id
            )
            record_event(
                correlation_id=correlation_id,
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=session.host_id,
                result='failed' if operation_error else 'succeeded',
                details={
                    'edit_session_id': str(session.session_id),
                    'file': session.remote_path,
                    'base_sha256': session.base_sha256,
                    'sha256': form.sha256,
                    'size': len(content),
                    'cleanup_outcome': cleanup_outcome,
                    'reason': operation_error,
                },
                request=request,
            )
            if operation_error:
                return json_response(error=operation_error)
            return json_response(session.to_view())
        except ApprovalError as exc:
            return json_response(error=exc.message)
        except FileTransferError as exc:
            return json_response(error=str(exc))


class UploadSessionView(View):
    @auth('host.console.upload')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('path', help='请输入上传目录'),
            Argument('filename', help='请输入文件名'),
            Argument(
                'size', type=int, filter=lambda value: 0 <= value <= MAX_FILE_SIZE,
                help='文件大小超出允许范围',
            ),
            Argument(
                'chunk_size', type=int,
                filter=lambda value: MIN_CHUNK_SIZE <= value <= MAX_CHUNK_SIZE,
                help='分片大小必须在 256KB 到 16MB 之间',
            ),
            Argument(
                'sha256', handler=lambda value: value.lower(),
                filter=lambda value: bool(SHA256_PATTERN.match(value.lower())),
                help='文件 SHA-256 不合法',
            ),
            Argument(
                'conflict_strategy', default='overwrite',
                filter=lambda value: value in ('overwrite', 'reject'),
                help='不支持的文件冲突策略',
            ),
            Argument('approval_id', type=uuid.UUID, help='请指定有效审批单'),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        if not has_host_perm(request.user, form.id, action='file.write'):
            return json_response(error='无权访问主机，请联系管理员')
        host = Host.objects.filter(pk=form.id).first()
        if not host:
            return json_response(error='未找到指定主机')
        try:
            directory, filename = _normalize_upload_target(form.path, form.filename)
        except FileTransferError as exc:
            return json_response(error=str(exc))

        upload_id = uuid.uuid4()
        payload = {
            'host_ids': [form.id],
            'path': directory,
            'filename': filename,
            'size': form.size,
            'sha256': form.sha256,
            'chunk_size': form.chunk_size,
            'conflict_strategy': form.conflict_strategy,
        }
        try:
            gate = authorize_operation(
                requester=request.user,
                action='file.write',
                resource_type='host',
                resource_ids=[form.id],
                payload=payload,
                approval_id=form.approval_id,
                execution_ref=upload_id,
                request=request,
            )
        except ApprovalError as exc:
            return json_response(error=exc.message)

        remote_path = posixpath.join(directory, filename)
        temporary_path = posixpath.join(
            directory, '.ops-platform-upload-%s.part' % upload_id.hex
        )
        transfer = FileTransfer.objects.create(
            id=upload_id,
            correlation_id=gate['correlation_id'],
            uploader=request.user,
            host=host,
            approval_id=gate['approval_id'],
            directory=directory,
            filename=filename,
            remote_path=remote_path,
            temporary_path=temporary_path,
            size=form.size,
            chunk_size=form.chunk_size,
            expected_sha256=form.sha256,
            conflict_strategy=form.conflict_strategy,
        )
        record_event(
            correlation_id=transfer.correlation_id,
            actor=request.user,
            action='file.write',
            resource_type='host',
            resource_id=host.id,
            result='started',
            details={
                'upload_id': str(transfer.id),
                'path': directory,
                'filename': filename,
                'size': transfer.size,
                'sha256': transfer.expected_sha256,
                'chunk_size': transfer.chunk_size,
                'conflict_strategy': transfer.conflict_strategy,
            },
            request=request,
        )
        return json_response(transfer.to_view())


class UploadBatchView(View):
    @auth('host.console.upload')
    def get(self, request):
        batches = FileTransferBatch.objects.filter(
            creator=request.user
        ).select_related('host').prefetch_related(Prefetch(
            'transfers',
            queryset=FileTransfer.objects.order_by('sequence', 'filename'),
            to_attr='_view_transfers',
        ))
        host_id = request.GET.get('id')
        if host_id:
            try:
                host_id = int(host_id)
            except (TypeError, ValueError):
                return json_response(error='主机参数错误')
            if not has_host_perm(request.user, host_id, action='file.write'):
                return json_response(error='无权访问主机，请联系管理员')
            batches = batches.filter(host_id=host_id)
        return json_response([item.to_view() for item in batches[:50]])

    @auth('host.console.upload')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('path', help='请输入上传目录'),
            Argument('files', type=list, help='请选择批量上传文件'),
            Argument(
                'chunk_size', type=int,
                filter=lambda value: MIN_CHUNK_SIZE <= value <= MAX_CHUNK_SIZE,
                help='分片大小必须在 256KB 到 16MB 之间',
            ),
            Argument(
                'conflict_strategy', default='overwrite',
                filter=lambda value: value in ('overwrite', 'reject'),
                help='不支持的文件冲突策略',
            ),
            Argument(
                'max_concurrency', type=int, default=2,
                filter=lambda value: 1 <= value <= MAX_BATCH_CONCURRENCY,
                help='批量上传并发数必须在 1 到 4 之间',
            ),
            Argument('approval_id', type=uuid.UUID, help='请指定有效审批单'),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        if not has_host_perm(request.user, form.id, action='file.write'):
            return json_response(error='无权访问主机，请联系管理员')
        host = Host.objects.filter(pk=form.id).first()
        if not host:
            return json_response(error='未找到指定主机')
        try:
            directory, _ = _normalize_upload_target(form.path, 'placeholder')
            files, total_size = _normalize_batch_files(form.files)
        except FileTransferError as exc:
            return json_response(error=str(exc))

        batch_id = uuid.uuid4()
        payload = {
            'host_ids': [form.id],
            'path': directory,
            'files': files,
            'chunk_size': form.chunk_size,
            'conflict_strategy': form.conflict_strategy,
            'max_concurrency': form.max_concurrency,
        }
        try:
            with transaction.atomic():
                gate = authorize_operation(
                    requester=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_ids=[form.id],
                    payload=payload,
                    approval_id=form.approval_id,
                    execution_ref=batch_id,
                    request=request,
                )
                batch = FileTransferBatch.objects.create(
                    id=batch_id,
                    correlation_id=gate['correlation_id'],
                    creator=request.user,
                    host=host,
                    approval_id=gate['approval_id'],
                    directory=directory,
                    file_count=len(files),
                    total_size=total_size,
                    max_concurrency=form.max_concurrency,
                )
                for sequence, item in enumerate(files):
                    upload_id = uuid.uuid4()
                    FileTransfer.objects.create(
                        id=upload_id,
                        correlation_id=gate['correlation_id'],
                        uploader=request.user,
                        host=host,
                        approval_id=gate['approval_id'],
                        batch=batch,
                        sequence=sequence,
                        directory=directory,
                        filename=item['filename'],
                        remote_path=posixpath.join(directory, item['filename']),
                        temporary_path=posixpath.join(
                            directory, '.ops-platform-upload-%s.part' % upload_id.hex
                        ),
                        size=item['size'],
                        chunk_size=form.chunk_size,
                        expected_sha256=item['sha256'],
                        conflict_strategy=form.conflict_strategy,
                    )
        except ApprovalError as exc:
            return json_response(error=exc.message)
        record_event(
            correlation_id=batch.correlation_id,
            actor=request.user,
            action='file.write',
            resource_type='host',
            resource_id=host.id,
            result='started',
            details={
                'batch_id': str(batch.id),
                'path': directory,
                'file_count': batch.file_count,
                'total_size': batch.total_size,
                'max_concurrency': batch.max_concurrency,
            },
            request=request,
        )
        return json_response(batch.to_view())


class UploadBatchDetailView(View):
    @auth('host.console.upload')
    def get(self, request, batch_id):
        try:
            batch = _get_user_batch(batch_id, request.user)
            batch, _ = refresh_file_transfer_batch(batch.id)
        except FileTransferError as exc:
            return json_response(error=str(exc))
        return json_response(batch.to_view())

    @auth('host.console.upload')
    def patch(self, request, batch_id):
        form, error = JsonParser(
            Argument(
                'action',
                filter=lambda value: value in ('pause', 'resume', 'retry'),
                help='不支持的批量上传操作',
            ),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        cleanup_events = []
        action_error = None
        try:
            with transaction.atomic():
                batch = _get_user_batch(
                    batch_id, request.user, for_update=True
                )
                if not verify_consumed_approval(
                    approval_id=batch.approval_id,
                    requester_id=request.user.id,
                    correlation_id=batch.correlation_id,
                    action='file.write',
                    execution_ref=batch.id,
                ):
                    raise FileTransferError('批量上传审批授权已失效')
                if form.action == 'pause':
                    if batch.status == 'active':
                        batch.status = 'paused'
                        batch.error = None
                    elif batch.status != 'paused':
                        raise FileTransferError('当前批量上传状态不能暂停')
                elif form.action == 'resume':
                    if batch.status == 'paused':
                        if not batch.transfers.filter(status='active').exists():
                            raise FileTransferError('当前批量上传没有可恢复的文件')
                        batch.status = 'active'
                        batch.error = None
                    elif batch.status != 'active':
                        raise FileTransferError('当前批量上传状态不能恢复')
                else:
                    if batch.status != 'failed':
                        raise FileTransferError('只有失败的批量上传可以重试')
                    reset_count = 0
                    transfers = batch.transfers.select_for_update().filter(
                        status='failed'
                    )
                    for transfer in transfers:
                        outcome = cleanup_transfer_temporary_file(transfer)
                        attempted = outcome is not None
                        outcome = outcome or transfer.cleanup_status
                        cleanup_events.append((transfer, outcome, attempted))
                        if outcome not in ('removed', 'missing'):
                            transfer.save(update_fields=(
                                'cleanup_status', 'cleanup_attempts',
                                'cleanup_attempted_at', 'cleanup_completed_at',
                                'cleanup_error', 'updated_at',
                            ))
                            continue
                        transfer.status = 'active'
                        transfer.offset = 0
                        transfer.actual_sha256 = None
                        transfer.error = None
                        transfer.expires_at = file_transfer_expiry()
                        transfer.completed_at = None
                        transfer.cleanup_status = 'pending'
                        transfer.cleanup_completed_at = None
                        transfer.cleanup_error = None
                        transfer.save(update_fields=(
                            'status', 'offset', 'actual_sha256', 'error',
                            'expires_at', 'completed_at', 'cleanup_status',
                            'cleanup_attempts', 'cleanup_attempted_at',
                            'cleanup_completed_at', 'cleanup_error', 'updated_at',
                        ))
                        reset_count += 1
                    if not reset_count:
                        action_error = '失败文件的远端临时文件尚未清理，不能重试'
                    else:
                        batch.status = 'active'
                        batch.error = None
                if not action_error:
                    batch.completed_at = None
                    batch.save(update_fields=(
                        'status', 'error', 'completed_at', 'updated_at'
                    ))
            for transfer, outcome, attempted in cleanup_events:
                if attempted:
                    record_event(
                        correlation_id=batch.correlation_id,
                        actor=request.user,
                        action='file.cleanup',
                        resource_type='host',
                        resource_id=batch.host_id,
                        result='failed' if outcome == 'failed' else 'succeeded',
                        details={
                            'batch_id': str(batch.id),
                            'upload_id': str(transfer.id),
                            'cleanup_outcome': outcome,
                            'trigger': 'batch_retry',
                        },
                        request=request,
                    )
            if action_error:
                return json_response(error=action_error)
            record_event(
                correlation_id=batch.correlation_id,
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=batch.host_id,
                result='succeeded',
                details={
                    'batch_id': str(batch.id),
                    'batch_action': form.action,
                    'status': batch.status,
                },
                request=request,
            )
            return json_response(batch.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))

    @auth('host.console.upload')
    def delete(self, request, batch_id):
        try:
            with transaction.atomic():
                batch = _get_user_batch(
                    batch_id, request.user, for_update=True
                )
                if batch.status == 'completed':
                    raise FileTransferError('已完成的批量上传不能取消')
                if batch.status == 'cancelled':
                    return json_response(batch.to_view())
                if not verify_consumed_approval(
                    approval_id=batch.approval_id,
                    requester_id=request.user.id,
                    correlation_id=batch.correlation_id,
                    action='file.write',
                    execution_ref=batch.id,
                ):
                    raise FileTransferError('批量上传审批授权已失效')
                batch.transfers.filter(status='active').update(
                    status='cancelled', error=None, updated_at=datetime.now()
                )
                batch.status = 'cancelled'
                batch.error = None
                batch.completed_at = None
                batch.save(update_fields=(
                    'status', 'error', 'completed_at', 'updated_at'
                ))
            record_event(
                correlation_id=batch.correlation_id,
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=batch.host_id,
                result='cancelled',
                details={
                    'batch_id': str(batch.id),
                    'file_count': batch.file_count,
                    'cleanup': 'scheduled',
                },
                request=request,
            )
            return json_response(batch.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))


class UploadSessionDetailView(View):
    @auth('host.console.upload')
    def get(self, request, upload_id):
        expired = False
        try:
            with transaction.atomic():
                transfer = _get_user_transfer(
                    upload_id, request.user, for_update=True
                )
                expired = transfer.refresh_expiry_status()
        except FileTransferError as exc:
            return json_response(error=str(exc))
        if expired and transfer.batch_id:
            refresh_file_transfer_batch(transfer.batch_id)
        return json_response(transfer.to_view())

    @auth('host.console.upload')
    def delete(self, request, upload_id):
        batch_transition = None
        try:
            with transaction.atomic():
                transfer = _get_user_transfer(
                    upload_id, request.user, for_update=True
                )
                if transfer.status == 'completed':
                    raise FileTransferError('已完成的上传不能取消')
                if transfer.status == 'cancelled' and (
                    transfer.cleanup_status in ('removed', 'missing') or
                    transfer.cleanup_attempts >= MAX_CLEANUP_ATTEMPTS
                ):
                    return json_response(transfer.to_view())
                cleanup_outcome = cleanup_transfer_temporary_file(transfer)
                cleanup_attempted = cleanup_outcome is not None
                cleanup_outcome = cleanup_outcome or transfer.cleanup_status
                transfer.status = 'cancelled'
                transfer.error = None
                transfer.save(update_fields=(
                    'status', 'error', 'cleanup_status', 'cleanup_attempts',
                    'cleanup_attempted_at', 'cleanup_completed_at',
                    'cleanup_error', 'updated_at',
                ))
            if transfer.batch_id:
                batch, changed = refresh_file_transfer_batch(
                    transfer.batch_id
                )
                if changed and batch.status in ('completed', 'failed'):
                    batch_transition = batch
            record_event(
                correlation_id=transfer.correlation_id,
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=transfer.host_id,
                result='cancelled',
                details={
                    'upload_id': str(transfer.id),
                    'filename': transfer.filename,
                    'temporary_file_removed': cleanup_outcome == 'removed',
                    'cleanup_outcome': cleanup_outcome,
                },
                request=request,
            )
            if cleanup_attempted:
                try:
                    record_event(
                        correlation_id=transfer.correlation_id,
                        actor=request.user,
                        action='file.cleanup',
                        resource_type='host',
                        resource_id=transfer.host_id,
                        result='failed' if cleanup_outcome == 'failed' else 'succeeded',
                        details={
                            'upload_id': str(transfer.id),
                            'filename': transfer.filename,
                            'cleanup_outcome': cleanup_outcome,
                            'cleanup_attempt': transfer.cleanup_attempts,
                            'trigger': 'upload_cancelled',
                        },
                        request=request,
                    )
                except Exception:
                    logger.exception('failed to record cancelled upload cleanup audit event')
            if batch_transition:
                record_event(
                    correlation_id=batch_transition.correlation_id,
                    actor=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_id=batch_transition.host_id,
                    result=(
                        'succeeded'
                        if batch_transition.status == 'completed' else 'failed'
                    ),
                    details={
                        'batch_id': str(batch_transition.id),
                        'file_count': batch_transition.file_count,
                        'status': batch_transition.status,
                        'reason': batch_transition.error,
                    },
                    request=request,
                )
            return json_response(transfer.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))


class UploadChunkView(View):
    @auth('host.console.upload')
    def post(self, request, upload_id):
        form, error = JsonParser(
            Argument('offset', type=int, filter=lambda value: value >= 0, help='分片偏移量不合法'),
            Argument(
                'sha256', handler=lambda value: value.lower(),
                filter=lambda value: bool(SHA256_PATTERN.match(value.lower())),
                help='分片 SHA-256 不合法',
            ),
        ).parse(request.POST)
        if error:
            return json_response(error=error)
        chunk = request.FILES.get('chunk')
        if not chunk:
            return json_response(error='请选择上传分片')
        reconciled = False
        failure = None
        expired = False
        batch_transition = None
        try:
            with transaction.atomic():
                transfer = _get_user_transfer(
                    upload_id, request.user, for_update=True
                )
                expired = transfer.refresh_expiry_status()
                if not expired:
                    if transfer.status != 'active':
                        raise FileTransferError('上传会话当前状态不允许继续上传')
                    if transfer.batch_id and transfer.batch.status != 'active':
                        raise FileTransferError('批量上传任务已暂停或终止')
                    if form.offset != transfer.offset:
                        raise FileTransferError(
                            '上传偏移量已变化，请刷新状态后续传'
                        )
                    with transfer.host.get_ssh() as ssh:
                        remote_offset = ssh.remote_file_size(
                            transfer.temporary_path
                        )
                        if remote_offset > transfer.size:
                            failure = '远端临时文件长度超过审批文件大小'
                            transfer.status = 'failed'
                            transfer.error = failure
                            transfer.save(update_fields=(
                                'status', 'error', 'updated_at'
                            ))
                        elif remote_offset != transfer.offset:
                            transfer.offset = remote_offset
                            transfer.save(update_fields=('offset', 'updated_at'))
                            reconciled = True
                        else:
                            remaining = transfer.size - transfer.offset
                            expected_size = min(transfer.chunk_size, remaining)
                            if chunk.size != expected_size:
                                raise FileTransferError(
                                    '分片大小不匹配，期望 %s 字节' % expected_size
                                )
                            if _file_sha256(chunk) != form.sha256:
                                raise FileTransferError('分片 SHA-256 校验失败')
                            new_offset = ssh.write_file_chunk(
                                chunk, transfer.temporary_path, transfer.offset
                            )
                            if new_offset != transfer.offset + chunk.size:
                                raise FileTransferError('远端分片写入长度不匹配')
                            transfer.offset = new_offset
                            transfer.save(update_fields=('offset', 'updated_at'))
            if (expired or failure) and transfer.batch_id:
                batch, changed = refresh_file_transfer_batch(
                    transfer.batch_id
                )
                if changed and batch.status in ('completed', 'failed'):
                    batch_transition = batch
            terminal_error = transfer.error if expired else failure
            if terminal_error:
                record_event(
                    correlation_id=transfer.correlation_id,
                    actor=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_id=transfer.host_id,
                    result='failed',
                    details={
                        'upload_id': str(transfer.id),
                        'filename': transfer.filename,
                        'reason': terminal_error,
                    },
                    request=request,
                )
                if batch_transition:
                    record_event(
                        correlation_id=batch_transition.correlation_id,
                        actor=request.user,
                        action='file.write',
                        resource_type='host',
                        resource_id=batch_transition.host_id,
                        result='failed',
                        details={
                            'batch_id': str(batch_transition.id),
                            'file_count': batch_transition.file_count,
                            'reason': batch_transition.error,
                        },
                        request=request,
                    )
                return json_response(error=terminal_error)
            if reconciled:
                return json_response(
                    error='已根据远端临时文件恢复上传偏移，请从新偏移继续'
                )
            return json_response(transfer.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))
        except Exception:
            logger.exception('failed to upload SFTP file chunk')
            return json_response(error='分片上传失败，请重新选择同一文件续传')


class UploadCompleteView(View):
    @auth('host.console.upload')
    def post(self, request, upload_id):
        failure = None
        batch_transition = None
        expired = False
        already_completed = False
        try:
            with transaction.atomic():
                transfer = _get_user_transfer(
                    upload_id, request.user, for_update=True
                )
                if transfer.status == 'completed':
                    already_completed = True
                else:
                    expired = transfer.refresh_expiry_status()
                if not already_completed and not expired:
                    if transfer.status != 'active':
                        raise FileTransferError('上传会话当前状态不允许完成')
                    if transfer.batch_id and transfer.batch.status != 'active':
                        raise FileTransferError('批量上传任务已暂停或终止')
                    if transfer.offset != transfer.size:
                        raise FileTransferError(
                            '文件尚未上传完成，当前进度 %s/%s' % (
                                transfer.offset, transfer.size
                            )
                        )
                    try:
                        with transfer.host.get_ssh() as ssh:
                            if transfer.size == 0:
                                ssh.create_empty_file(transfer.temporary_path)
                            actual_sha256 = ssh.remote_file_sha256(
                                transfer.temporary_path
                            )
                            if actual_sha256 != transfer.expected_sha256:
                                transfer.status = 'failed'
                                transfer.actual_sha256 = actual_sha256
                                transfer.error = '远端文件 SHA-256 校验失败'
                                transfer.save(update_fields=(
                                    'status', 'actual_sha256', 'error', 'updated_at'
                                ))
                                failure = transfer.error
                            else:
                                ssh.replace_file(
                                    transfer.temporary_path,
                                    transfer.remote_path,
                                    overwrite=transfer.conflict_strategy == 'overwrite',
                                )
                                transfer.status = 'completed'
                                transfer.actual_sha256 = actual_sha256
                                transfer.error = None
                                transfer.completed_at = datetime.now()
                                transfer.cleanup_status = 'removed'
                                transfer.cleanup_completed_at = transfer.completed_at
                                transfer.save(update_fields=(
                                    'status', 'actual_sha256', 'error',
                                    'completed_at', 'cleanup_status',
                                    'cleanup_completed_at', 'updated_at'
                                ))
                    except FileTransferError:
                        raise
                    except Exception:
                        logger.exception('failed to finalize SFTP upload')
                        raise FileTransferError('完成上传失败，可稍后重试')
            if transfer.batch_id:
                batch, changed = refresh_file_transfer_batch(
                    transfer.batch_id
                )
                if changed and batch.status in ('completed', 'failed'):
                    batch_transition = batch
            if already_completed:
                return json_response(transfer.to_view())
            if expired:
                failure = transfer.error
            if failure:
                record_event(
                    correlation_id=transfer.correlation_id,
                    actor=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_id=transfer.host_id,
                    result='failed',
                    details={
                        'upload_id': str(transfer.id),
                        'filename': transfer.filename,
                        'reason': failure,
                        'expected_sha256': transfer.expected_sha256,
                        'actual_sha256': transfer.actual_sha256,
                    },
                    request=request,
                )
                if batch_transition:
                    record_event(
                        correlation_id=batch_transition.correlation_id,
                        actor=request.user,
                        action='file.write',
                        resource_type='host',
                        resource_id=batch_transition.host_id,
                        result='failed',
                        details={
                            'batch_id': str(batch_transition.id),
                            'file_count': batch_transition.file_count,
                            'reason': batch_transition.error,
                        },
                        request=request,
                    )
                return json_response(error=failure)
            record_event(
                correlation_id=transfer.correlation_id,
                actor=request.user,
                action='file.write',
                resource_type='host',
                resource_id=transfer.host_id,
                result='succeeded',
                details={
                    'upload_id': str(transfer.id),
                    'path': transfer.directory,
                    'filename': transfer.filename,
                    'size': transfer.size,
                    'sha256': transfer.actual_sha256,
                    'conflict_strategy': transfer.conflict_strategy,
                },
                request=request,
            )
            if batch_transition:
                record_event(
                    correlation_id=batch_transition.correlation_id,
                    actor=request.user,
                    action='file.write',
                    resource_type='host',
                    resource_id=batch_transition.host_id,
                    result=(
                        'succeeded'
                        if batch_transition.status == 'completed' else 'failed'
                    ),
                    details={
                        'batch_id': str(batch_transition.id),
                        'file_count': batch_transition.file_count,
                        'total_size': batch_transition.total_size,
                        'status': batch_transition.status,
                    },
                    request=request,
                )
            return json_response(transfer.to_view())
        except FileTransferError as exc:
            return json_response(error=str(exc))

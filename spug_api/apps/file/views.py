# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django_redis import get_redis_connection
from apps.host.models import Host
from apps.account.utils import has_host_perm
from apps.audit.services import ApprovalError, authorize_operation, record_event
from apps.file.utils import FileResponseAfter, fetch_dir_list
from libs import json_response, JsonParser, Argument, auth
from functools import partial
import os


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
        form, error = JsonParser(
            Argument('id', type=int, help='参数错误'),
            Argument('file', help='请输入文件路径')
        ).parse(request.GET)
        if error is None:
            if not has_host_perm(request.user, form.id, action='file.read'):
                return json_response(error='无权访问主机，请联系管理员')
            host = Host.objects.filter(pk=form.id).first()
            if not host:
                return json_response(error='未找到指定主机')
            filename = os.path.basename(form.file)
            ssh_cli = host.get_ssh().get_client()
            sftp = ssh_cli.open_sftp()
            f = sftp.open(form.file)
            return FileResponseAfter(ssh_cli.close, f, as_attachment=True, filename=filename)
        return json_response(error=error)

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

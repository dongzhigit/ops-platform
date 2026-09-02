# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django_redis import get_redis_connection
from django.conf import settings
from libs import json_response, JsonParser, Argument, human_datetime, auth
from apps.exec.models import ExecTemplate, ExecHistory
from apps.host.models import Host
from apps.account.utils import has_host_perm
from apps.audit.services import (
    ApprovalError,
    authorize_operation,
    record_event,
    verify_consumed_approval,
)
import uuid
import json


class TemplateView(View):
    @auth('exec.template.view|exec.task.do|schedule.schedule.add|schedule.schedule.edit|\
    monitor.monitor.add|monitor.monitor.edit')
    def get(self, request):
        templates = ExecTemplate.objects.all()
        types = [x['type'] for x in templates.order_by('type').values('type').distinct()]
        return json_response({'types': types, 'templates': [x.to_view() for x in templates]})

    @auth('exec.template.add|exec.template.edit')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('name', help='请输入模版名称'),
            Argument('type', help='请选择模版类型'),
            Argument('body', help='请输入模版内容'),
            Argument('interpreter', default='sh'),
            Argument('host_ids', type=list, handler=json.dumps, default=[]),
            Argument('parameters', type=list, handler=json.dumps, default=[]),
            Argument('desc', required=False)
        ).parse(request.body)
        if error is None:
            if form.id:
                form.updated_at = human_datetime()
                form.updated_by = request.user
                ExecTemplate.objects.filter(pk=form.pop('id')).update(**form)
            else:
                form.created_by = request.user
                ExecTemplate.objects.create(**form)
        return json_response(error=error)

    @auth('exec.template.del')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定操作对象')
        ).parse(request.GET)
        if error is None:
            ExecTemplate.objects.filter(pk=form.id).delete()
        return json_response(error=error)


class TaskView(View):
    @auth('exec.task.do')
    def get(self, request):
        records = ExecHistory.objects.filter(user=request.user).select_related('template')
        return json_response([x.to_view() for x in records])

    @auth('exec.task.do')
    def post(self, request):
        form, error = JsonParser(
            Argument('host_ids', type=list, filter=lambda x: len(x), help='请选择执行主机'),
            Argument('command', help='请输入执行命令内容'),
            Argument('interpreter', default='sh'),
            Argument('template_id', type=int, required=False),
            Argument('params', type=dict, handler=json.dumps, default={}),
            Argument('approval_id', required=False),
        ).parse(request.body)
        if error is None:
            if not has_host_perm(request.user, form.host_ids, action='exec.run'):
                return json_response(error='无权访问主机，请联系管理员')
            token = uuid.uuid4().hex
            form.host_ids.sort()
            if form.template_id:
                template = ExecTemplate.objects.filter(pk=form.template_id).first()
                if not template or template.body != form.command:
                    form.template_id = None

            approval_id = form.pop('approval_id')
            payload = {
                'host_ids': form.host_ids,
                'command': form.command,
                'interpreter': form.interpreter,
                'template_id': form.template_id,
                'params': json.loads(form.params),
            }
            try:
                gate = authorize_operation(
                    requester=request.user,
                    action='exec.run',
                    resource_type='host',
                    resource_ids=form.host_ids,
                    payload=payload,
                    approval_id=approval_id,
                    execution_ref=token,
                    request=request,
                )
            except ApprovalError as exc:
                return json_response(error=exc.message)

            ExecHistory.objects.create(
                user=request.user,
                digest=token,
                interpreter=form.interpreter,
                template_id=form.template_id,
                command=form.command,
                host_ids=json.dumps(form.host_ids),
                params=form.params,
                correlation_id=gate['correlation_id'],
                approval_id=gate['approval_id'],
            )
            return json_response(token)
        return json_response(error=error)

    @auth('exec.task.do')
    def patch(self, request):
        form, error = JsonParser(
            Argument('token', help='参数错误'),
            Argument('cols', type=int, required=False),
            Argument('rows', type=int, required=False)
        ).parse(request.body)
        if error is None:
            term = None
            if form.cols and form.rows:
                term = {'width': form.cols, 'height': form.rows}
            rds = get_redis_connection()
            task = ExecHistory.objects.filter(digest=form.token, user=request.user).first()
            if not task:
                return json_response(error='未找到指定执行任务')
            host_ids = json.loads(task.host_ids)
            if not has_host_perm(request.user, host_ids, action='exec.run'):
                return json_response(error='授权已失效，请联系管理员')
            if not verify_consumed_approval(
                    approval_id=task.approval_id,
                    requester_id=request.user.id,
                    correlation_id=task.correlation_id,
                    action='exec.run',
                    execution_ref=task.digest):
                record_event(
                    correlation_id=task.correlation_id,
                    actor=request.user,
                    action='exec.run',
                    resource_type='host',
                    resource_id=task.digest,
                    result='denied',
                    details={'reason': 'approval_invalid_at_dispatch', 'host_ids': host_ids},
                    request=request,
                )
                return json_response(error='审批授权已失效，任务未执行')
            for host in Host.objects.filter(id__in=host_ids):
                data = dict(
                    key=host.id,
                    host_id=host.id,
                    user_id=request.user.id,
                    token=task.digest,
                    interpreter=task.interpreter,
                    command=task.command,
                    params=json.loads(task.params),
                    term=term,
                    correlation_id=str(task.correlation_id),
                    approval_id=str(task.approval_id) if task.approval_id else None,
                    execution_ref=task.digest,
                )
                rds.rpush(settings.EXEC_WORKER_KEY, json.dumps(data))
            record_event(
                correlation_id=task.correlation_id,
                actor=request.user,
                action='exec.run',
                resource_type='host',
                resource_id=task.digest,
                result='queued',
                details={'host_ids': host_ids, 'target_count': len(host_ids)},
                request=request,
            )
        return json_response(error=error)

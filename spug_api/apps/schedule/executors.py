# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from libs.ssh import AuthenticationException
from django.db import close_old_connections, transaction
from apps.host.models import Host
from apps.schedule.models import History, Task
from apps.schedule.utils import send_fail_notify
from apps.account.utils import has_host_perm
from apps.audit.services import record_event, verify_consumed_approval
import subprocess
import socket
import time
import json


def local_executor(command):
    code, out, now = 1, None, time.time()
    task = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        code = task.wait(3600)
        out = task.stdout.read() + task.stderr.read()
        out = out.decode()
    except subprocess.TimeoutExpired:
        # task.kill()
        out = 'timeout, wait more than 1 hour'
    return code, round(time.time() - now, 3), out


def host_executor(host, command):
    code, out, now = 1, None, time.time()
    try:
        with host.get_ssh() as ssh:
            code, out = ssh.exec_command_raw(command)
    except AuthenticationException:
        out = 'ssh authentication fail'
    except socket.error as e:
        out = f'network error {e}'
    return code, round(time.time() - now, 3), out


def dispatch_job(host_id, interpreter, command):
    if interpreter == 'python':
        attach = 'INTERPRETER=python\ncommand -v python3 &> /dev/null && INTERPRETER=python3'
        command = f'{attach}\n$INTERPRETER << EOF\n# -*- coding: UTF-8 -*-\n{command}\nEOF'
    if host_id == 'local':
        code, duration, out = local_executor(command)
    else:
        host = Host.objects.filter(pk=host_id).first()
        if not host:
            code, duration, out = 1, 0, f'unknown host id for {host_id!r}'
        else:
            code, duration, out = host_executor(host, command)
    return code, duration, out


def schedule_worker_handler(job):
    history_id, host_id, interpreter, command = json.loads(job)
    task_id = History.objects.filter(pk=history_id).values_list('task_id', flat=True).first()
    task = Task.objects.select_related('created_by').filter(pk=task_id).first()
    approval_valid = bool(task and task.approval_id and verify_consumed_approval(
        approval_id=task.approval_id,
        requester_id=task.created_by_id,
        correlation_id=task.correlation_id,
        action='schedule.run',
        execution_ref=task.approval_execution_ref,
    ))
    host_authorized = bool(task) and (
        str(host_id) == 'local' and task.created_by.is_supper or
        str(host_id) != 'local' and has_host_perm(task.created_by, host_id, action='schedule.run')
    )
    if not approval_valid or not host_authorized:
        code, duration, out = 126, 0, 'authorization revoked before scheduled execution'
        if task:
            record_event(
                correlation_id=task.correlation_id,
                actor=task.created_by,
                action='schedule.run',
                resource_type='host',
                resource_id=host_id,
                result='denied',
                details={'task_id': task.id, 'history_id': history_id},
            )
    else:
        record_event(
            correlation_id=task.correlation_id,
            actor=task.created_by,
            action='schedule.run',
            resource_type='host',
            resource_id=host_id,
            result='started',
            details={'task_id': task.id, 'history_id': history_id},
        )
        code, duration, out = dispatch_job(host_id, interpreter, command)
        record_event(
            correlation_id=task.correlation_id,
            actor=task.created_by,
            action='schedule.run',
            resource_type='host',
            resource_id=host_id,
            result='succeeded' if code == 0 else 'failed',
            details={'task_id': task.id, 'history_id': history_id, 'exit_code': code, 'duration': duration},
        )

    close_old_connections()
    with transaction.atomic():
        history = History.objects.select_for_update().get(pk=history_id)
        output = json.loads(history.output)
        output[str(host_id)] = [code, duration, out]
        history.output = json.dumps(output)
        if all(output.values()):
            history.status = '1' if sum(x[0] for x in output.values()) == 0 else '2'
        history.save()
    if history.status == '2' and task:
        task = Task.objects.get(pk=history.task_id)
        send_fail_notify(task)

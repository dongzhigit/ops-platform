# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.views.generic import View
from django.conf import settings
from django.db import close_old_connections
from django_redis import get_redis_connection
from apps.exec.models import Transfer
from apps.account.utils import has_host_perm
from apps.host.models import Host
from libs import json_response, JsonParser, Argument, auth
from libs.utils import str_decode, human_seconds_time
from concurrent import futures
from contextlib import contextmanager
from threading import Thread
import subprocess
import tempfile
import shlex
import shutil
import uuid
import json
import time
import os


class TransferView(View):
    @auth('exec.transfer.do')
    def get(self, request):
        records = Transfer.objects.filter(user=request.user)
        return json_response([x.to_view() for x in records])

    @auth('exec.transfer.do')
    def post(self, request):
        data = request.POST.get('data')
        form, error = JsonParser(
            Argument('host', required=False),
            Argument('dst_dir', help='请输入目标路径'),
            Argument('host_ids', type=list, filter=lambda x: len(x), help='请选择目标主机'),
        ).parse(data)
        if error is None:
            if not has_host_perm(request.user, form.host_ids, action='file.distribute'):
                return json_response(error='无权访问主机，请联系管理员')
            host_id = None
            token = uuid.uuid4().hex
            base_dir = os.path.join(settings.TRANSFER_DIR, token)
            if form.host:
                host_id, path = json.loads(form.host)
                if not has_host_perm(request.user, host_id, action='file.distribute'):
                    return json_response(error='无权读取源主机，请联系管理员')
                if not path.strip('/'):
                    return json_response(error='请输入正确的数据源路径')
                host = Host.objects.get(pk=host_id)
                with host.get_ssh() as ssh:
                    code, _ = ssh.exec_command_raw('[ -d %s ]' % shlex.quote(path))
                    if code != 0:
                        return json_response(error='数据源路径必须为该主机上已存在的目录')
                os.makedirs(base_dir)
                with _ssh_transport(host) as (profile, ssh_options, env):
                    target = f'{host.ssh_username}@{host.hostname}:{path}'
                    command = ['sshfs', '-o', 'ro']
                    if profile['credential_type'] == 'password':
                        command.extend(['-o', 'password_stdin'])
                    command.extend([
                        '-o', 'ssh_command=%s' % _shell_join(['ssh'] + ssh_options),
                        target,
                        base_dir,
                    ])
                    stdin = (profile['secret'] + '\n').encode() if profile['credential_type'] == 'password' else None
                    process = subprocess.run(
                        command,
                        input=stdin,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                    )
                    if process.returncode != 0:
                        shutil.rmtree(base_dir, ignore_errors=True)
                        return json_response(error=process.stdout.decode())
            else:
                os.makedirs(base_dir)
                index = 0
                while True:
                    file = request.FILES.get(f'file{index}')
                    if not file:
                        break
                    with open(os.path.join(base_dir, file.name), 'wb') as f:
                        for chunk in file.chunks():
                            f.write(chunk)
                    index += 1
            Transfer.objects.create(
                user=request.user,
                digest=token,
                host_id=host_id,
                src_dir=base_dir,
                dst_dir=form.dst_dir,
                host_ids=json.dumps(form.host_ids),
            )
            return json_response(token)
        return json_response(error=error)

    @auth('exec.transfer.do')
    def patch(self, request):
        form, error = JsonParser(
            Argument('token', help='参数错误')
        ).parse(request.body)
        if error is None:
            task = Transfer.objects.filter(digest=form.token, user=request.user).first()
            if not task:
                return json_response(error='未找到指定分发任务')
            if not has_host_perm(request.user, json.loads(task.host_ids), action='file.distribute'):
                return json_response(error='授权已失效，请联系管理员')
            Thread(target=_dispatch_sync, args=(task,)).start()
        return json_response(error=error)


def _dispatch_sync(task):
    rds = get_redis_connection()
    threads = []
    max_workers = max(10, os.cpu_count() * 5)
    with futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        for host in Host.objects.filter(id__in=json.loads(task.host_ids)):
            t = executor.submit(_do_sync, rds, task, host)
            t.token = task.digest
            t.key = host.id
            threads.append(t)
        for t in futures.as_completed(threads):
            exc = t.exception()
            if exc:
                rds.publish(
                    t.token,
                    json.dumps({'key': t.key, 'status': -1, 'data': f'\x1b[31mException: {exc}\x1b[0m'})
                )
    if task.host_id:
        subprocess.run(['umount', '-f', task.src_dir], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    shutil.rmtree(task.src_dir, ignore_errors=True)
    close_old_connections()


def _do_sync(rds, task, host):
    token = task.digest
    rds.publish(token, json.dumps({'key': host.id, 'data': '\r\n\x1b[36m### Executing ...\x1b[0m\r\n'}))
    with _ssh_transport(host) as (profile, ssh_options, env):
        flag = time.time()
        options = '-azv' if task.host_id else '-rzv'
        remote_path = shlex.quote(task.dst_dir)
        target = f'{host.ssh_username}@{host.hostname}:{remote_path}'
        command = [
            'rsync', options, '--progress', '-h',
            '-e', _shell_join(['ssh'] + ssh_options),
            task.src_dir.rstrip('/') + '/',
            target,
        ]
        if profile['credential_type'] == 'password':
            command = ['sshpass', '-e'] + command
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        message = b''
        while True:
            output = process.stdout.read(1)
            if not output:
                break
            if output in (b'\r', b'\n'):
                message += b'\r\n' if output == b'\n' else b'\r'
                message = str_decode(message)
                if 'rsync: command not found' in message:
                    data = '\r\n\x1b[31m检测到该主机未安装rsync，可通过批量执行/执行任务模块进行以下命令批量安装\x1b[0m'
                    data += '\r\nCentos/Redhat: yum install -y rsync'
                    data += '\r\nUbuntu/Debian: apt install -y rsync'
                    rds.publish(token, json.dumps({'key': host.id, 'data': data}))
                    break
                rds.publish(token, json.dumps({'key': host.id, 'data': message}))
                message = b''
            else:
                message += output
        status = process.wait()
        if status == 0:
            human_time = human_seconds_time(time.time() - flag)
            rds.publish(token, json.dumps({'key': host.id, 'data': f'\r\n\x1b[32m** 分发完成，总耗时：{human_time} **\x1b[0m'}))
        rds.publish(token, json.dumps({'key': host.id, 'status': status}))


def _shell_join(arguments):
    return ' '.join(shlex.quote(str(item)) for item in arguments)


@contextmanager
def _ssh_transport(host):
    profile = host.get_connection_profile()
    options = [
        '-p', str(host.port),
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
    ]
    environment = os.environ.copy()
    if profile['credential_type'] == 'password':
        environment['SSHPASS'] = profile['secret']
        yield profile, options, environment
        return
    if profile['credential_type'] != 'ssh_key':
        raise RuntimeError('文件分发仅支持 SSH 密钥或密码凭据')
    with tempfile.NamedTemporaryFile(mode='w') as key_file:
        key_file.write(profile['secret'])
        key_file.write('\n')
        key_file.flush()
        key_options = options + ['-i', key_file.name, '-o', 'IdentitiesOnly=yes']
        yield profile, key_options, environment

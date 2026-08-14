# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django_redis import get_redis_connection
from apps.account.models import User
from apps.account.utils import has_host_perm
from apps.host.models import Host
from libs.utils import human_seconds_time
import threading
import socket
import json
import time


def exec_worker_handler(job):
    job = Job(**json.loads(job))
    threading.Thread(target=job.run).start()


class Job:
    def __init__(self, key, host_id, user_id, command, interpreter, params=None, token=None, term=None):
        self.key = key
        self.host = Host.objects.filter(pk=host_id).first()
        self.user = User.objects.filter(pk=user_id).first()
        self.term = term
        self.command = self._handle_command(command, interpreter)
        self.token = token
        self.rds = get_redis_connection()
        if not self.host:
            raise RuntimeError('unknown host id for %r' % host_id)
        self.env = dict(
            SPUG_HOST_ID=str(self.key),
            SPUG_HOST_NAME=self.host.name,
            SPUG_HOST_HOSTNAME=self.host.hostname,
            SPUG_SSH_PORT=str(self.host.port),
            SPUG_SSH_USERNAME=self.host.get_connection_metadata()['username'],
            SPUG_INTERPRETER=interpreter
        )
        if isinstance(params, dict):
            self.env.update({f'_SPUG_{k}': str(v) for k, v in params.items()})

    def _send(self, message):
        self.rds.publish(self.token, json.dumps(message))

    def _handle_command(self, command, interpreter):
        if interpreter == 'python':
            attach = 'INTERPRETER=python\ncommand -v python3 &> /dev/null && INTERPRETER=python3'
            return f'{attach}\n$INTERPRETER << EOF\n# -*- coding: UTF-8 -*-\n{command}\nEOF'
        return command

    def send(self, data):
        self._send({'key': self.key, 'data': data})

    def send_status(self, code):
        self._send({'key': self.key, 'status': code})

    def run(self):
        if not self.user or not has_host_perm(self.user, self.host.id, action='exec.run'):
            if self.token:
                self.send('\r\n\x1b[31m### 授权已失效，任务未执行\x1b[0m')
                self.send_status(126)
            return 126, 'authorization revoked'
        if not self.token:
            with self.host.get_ssh(term=self.term) as ssh:
                return ssh.exec_command(self.command, self.env)
        flag = time.time()
        self.send('\r\n\x1b[36m### Executing ...\x1b[0m\r\n')
        code = -1
        try:
            with self.host.get_ssh(term=self.term) as ssh:
                for code, out in ssh.exec_command_with_stream(self.command, self.env):
                    self.send(out)
            human_time = human_seconds_time(time.time() - flag)
            self.send(f'\r\n\x1b[36m** 执行结束，总耗时：{human_time} **\x1b[0m')
        except socket.timeout:
            code = 130
            self.send('\r\n\x1b[31m### Time out\x1b[0m')
        except Exception as e:
            code = 131
            self.send(f'\r\n\x1b[31m### Exception {e}\x1b[0m')
            raise e
        finally:
            self.send_status(code)

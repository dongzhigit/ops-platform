import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

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
from apps.exec.models import ExecHistory
from apps.exec.views import TaskView
from apps.host.models import Host
from apps.schedule.models import Task
from apps.schedule.views import Schedule


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

    def test_server_side_risk_classification_cannot_be_downgraded(self):
        self.assertEqual(assess_risk('exec.run', {'command': 'uptime'}), 'medium')
        self.assertEqual(assess_risk('exec.run', {'command': 'sudo systemctl restart nginx'}), 'high')
        self.assertEqual(assess_risk('exec.run', {'command': 'rm -rf /srv/app'}), 'critical')
        self.assertEqual(
            assess_risk('exec.run', {'command': 'uptime', 'host_ids': list(range(50))}),
            'critical',
        )

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

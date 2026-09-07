import json

from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from apps.account.models import Role, User
from apps.aiops.models import AIActionPlan, AIInvestigation, AIRemediationProposal
from apps.assets.models import AccessGrant
from apps.host.models import Host
from apps.incident.models import IncidentRoom, IncidentTimeline
from apps.incident.views import IncidentRoomView, IncidentScopeView, IncidentTimelineView
from apps.observability.models import AlertEvent, MetricTarget
from apps.topology.models import TopologyEdge, TopologyNode


def body(response):
    return json.loads(response.content.decode('utf-8'))


@override_settings(SPUG_OBSERVABILITY_ENABLED=True)
class IncidentTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = self.make_user('admin', is_supper=True)
        self.user = self.make_user('operator')
        self.outsider = self.make_user('outsider')
        role = Role.objects.create(
            name='incident-operator',
            page_perms=json.dumps({
                'incident': {'room': ['view', 'manage']},
                'topology': {'topology': ['view']},
                'host': {'host': ['view']},
                'monitor': {'metrics': ['view']},
                'alarm': {'event': ['view']},
                'aiops': {
                    'investigation': ['view'],
                    'remediation': ['view'],
                },
            }),
            created_by=self.admin,
        )
        self.user.roles.add(role)
        self.outsider.roles.add(role)
        self.user.set_perms_cache()
        self.outsider.set_perms_cache()
        self.host = Host.objects.create(
            name='linux-01',
            hostname='192.168.18.226',
            port=22,
            username='root',
            created_by=self.admin,
        )
        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect='allow',
            actions=json.dumps(['host.view', 'metrics.view']),
            created_by=self.admin,
        )
        MetricTarget.objects.create(
            host=self.host,
            exporter_address=self.host.hostname,
            created_by=self.admin,
        )
        self.alert = AlertEvent.objects.create(
            fingerprint='incident-alert',
            host=self.host,
            alert_name='HostDown',
            severity='critical',
            status='firing',
            summary='host exporter down',
        )
        self.host_node = TopologyNode.objects.create(
            key='host:%s' % self.host.id,
            type='host',
            name=self.host.name,
            source_type='host',
            source_id=str(self.host.id),
            created_by=self.admin,
        )
        self.service_node = TopologyNode.objects.create(
            key='service:web',
            type='service',
            name='web',
            created_by=self.admin,
        )
        TopologyEdge.objects.create(
            source=self.service_node,
            target=self.host_node,
            type='deployed_on',
            created_by=self.admin,
        )
        self.investigation = AIInvestigation.objects.create(
            created_by=self.user,
            question='check host down',
            requested_host_ids='[%s]' % self.host.id,
            status='completed',
            provider='openai-compatible',
            model='ops-model-v1',
            prompt_version='readonly-v2',
            citations=json.dumps(['asset://host/%s' % self.host.id]),
        )
        self.action_plan = AIActionPlan.objects.create(
            investigation=self.investigation,
            title='修复建议',
            summary='人工复核后登记执行证据。',
            risk_level='medium',
            steps='[]',
            created_by=self.user,
        )
        self.proposal = AIRemediationProposal.objects.create(
            action_plan=self.action_plan,
            title='受控修复提案',
            summary='记录修复建议、审批和执行证据。',
            proposed_action='manual_check',
            target_refs=json.dumps(['host:%s' % self.host.id]),
            risk_level='medium',
            validation_plan='人工验证告警恢复。',
            rollback_plan='继续观察。',
            citation_refs=json.dumps(['asset://host/%s' % self.host.id]),
            created_by=self.user,
        )

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

    def request(self, method, path, user, data=None, query=None):
        if method == 'get':
            request = self.factory.get(path, query or {})
        else:
            request = getattr(self.factory, method)(
                path, data=json.dumps(data or {}), content_type='application/json'
            )
        request.user = user
        return request

    def test_routes_are_versioned(self):
        self.assertIs(resolve('/v1/incidents/scope/').func.view_class, IncidentScopeView)
        self.assertIs(resolve('/v1/incidents/rooms/').func.view_class, IncidentRoomView)
        self.assertIs(resolve('/v1/incidents/timeline/').func.view_class, IncidentTimelineView)

    def test_room_groups_alert_topology_ai_and_timeline(self):
        request = self.request('post', '/v1/incidents/rooms/', self.user, {
            'title': '226 exporter down',
            'alert_id': self.alert.id,
            'severity': 'critical',
            'topology_node_ids': [str(self.service_node.id)],
            'ai_investigation_ids': [str(self.investigation.id)],
            'postmortem_draft': 'initial draft',
        })
        data = body(IncidentRoomView.as_view()(request))['data']
        self.assertEqual(data['title'], '226 exporter down')
        self.assertEqual(data['alert']['id'], self.alert.id)
        self.assertEqual(data['owner']['id'], self.user.id)
        self.assertEqual(data['postmortem_draft'], 'initial draft')
        self.assertEqual(len(data['timeline']), 1)
        self.assertEqual(data['topology']['host_ids'], [self.host.id])
        self.assertEqual(data['ai_investigations'][0]['id'], str(self.investigation.id))
        self.assertEqual(data['remediation_proposals'][0]['id'], str(self.proposal.id))

        note = self.request('post', '/v1/incidents/timeline/', self.user, {
            'room_id': data['id'],
            'message': '已确认 exporter 恢复',
        })
        self.assertFalse(body(IncidentTimelineView.as_view()(note))['error'])

        update = self.request('post', '/v1/incidents/rooms/', self.user, {
            'id': data['id'],
            'status': 'resolved',
            'postmortem_draft': 'root cause draft',
        })
        detail = body(IncidentRoomView.as_view()(update))['data']
        self.assertEqual(detail['status'], 'resolved')
        self.assertEqual(detail['postmortem_draft'], 'root cause draft')
        self.assertEqual(IncidentRoom.objects.count(), 1)
        self.assertEqual(IncidentTimeline.objects.count(), 3)

    def test_rooms_are_filtered_by_alert_asset_authorization(self):
        room = IncidentRoom.objects.create(
            title='visible',
            severity='critical',
            alert=self.alert,
            created_by=self.admin,
        )
        request = self.request('get', '/v1/incidents/rooms/', self.user)
        data = body(IncidentRoomView.as_view()(request))['data']
        self.assertEqual(data[0]['id'], str(room.id))

        request = self.request('get', '/v1/incidents/rooms/', self.outsider)
        self.assertEqual(body(IncidentRoomView.as_view()(request))['data'], [])

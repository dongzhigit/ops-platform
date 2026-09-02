import json
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve

from apps.account.models import Role, User
from apps.app.models import App, Deploy
from apps.assets.models import AccessGrant
from apps.audit.models import AuditEvent
from apps.config.models import Environment, Service
from apps.host.models import Host
from apps.observability.models import AlertEvent, AlertTransition, MetricTarget
from apps.observability.services import create_alertmanager_silence
from apps.observability.views import (
    AlertEventView,
    AlertmanagerWebhookView,
    MetricQueryView,
    MetricTargetView,
    PrometheusDiscoveryView,
    TopologyView,
)


DISCOVERY_TOKEN = 'd' * 64
WEBHOOK_TOKEN = 'w' * 64


def body(response):
    return json.loads(response.content.decode('utf-8'))


@override_settings(
    SPUG_OBSERVABILITY_ENABLED=True,
    SPUG_PROMETHEUS_DISCOVERY_TOKEN=DISCOVERY_TOKEN,
    SPUG_ALERTMANAGER_WEBHOOK_TOKEN=WEBHOOK_TOKEN,
    SPUG_PROMETHEUS_URL='http://prometheus:9090',
    SPUG_ALERTMANAGER_URL='http://alertmanager:9093',
    SPUG_OBSERVABILITY_REQUEST_TIMEOUT=2,
    SPUG_ALERTMANAGER_MAX_BODY=524288,
)
class ObservabilityTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = self.make_user('admin', is_supper=True)
        self.user = self.make_user('operator')
        role = Role.objects.create(
            name='observer',
            page_perms=json.dumps({
                'monitor': {'metrics': ['view', 'manage']},
                'alarm': {'event': ['view', 'claim', 'silence']},
            }),
            created_by=self.admin,
        )
        self.user.roles.add(role)
        self.host = Host.objects.create(
            name='linux-01',
            hostname='10.20.30.40',
            port=22,
            username='root',
            created_by=self.admin,
        )
        self.target = MetricTarget.objects.create(
            host=self.host,
            exporter_address='10.20.30.40',
            exporter_port=9100,
            notify_grp='[]',
            notify_mode='[]',
            created_by=self.admin,
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

    def grant_metrics(self, effect='allow'):
        return AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect=effect,
            actions=json.dumps(['metrics.view']),
            created_by=self.admin,
        )

    def alert_payload(self, status='firing', fingerprint='abc123'):
        return {
            'status': status,
            'alerts': [{
                'status': status,
                'fingerprint': fingerprint,
                'labels': {
                    'alertname': 'OpsPlatformHostCpuHigh',
                    'severity': 'warning',
                    'instance': '10.20.30.40:9100',
                    'ops_platform_host_id': str(self.host.id),
                    'ops_platform_host_name': self.host.name,
                },
                'annotations': {
                    'summary': 'CPU usage is high',
                    'description': 'CPU has exceeded 90 percent',
                },
                'startsAt': '2026-08-18T00:00:00Z',
                'endsAt': '2026-08-18T00:10:00Z' if status == 'resolved' else '0001-01-01T00:00:00Z',
            }],
        }

    def webhook(self, payload, token=WEBHOOK_TOKEN):
        return self.client.post(
            '/v1/observability/alertmanager/webhook/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_AUTHORIZATION='Bearer ' + token,
        )

    def test_routes_are_versioned_and_service_endpoints_bypass_user_session(self):
        self.assertIs(
            resolve('/v1/observability/discovery/targets/').func.view_class,
            PrometheusDiscoveryView,
        )
        self.assertIs(
            resolve('/v1/observability/alertmanager/webhook/').func.view_class,
            AlertmanagerWebhookView,
        )
        self.assertIs(
            resolve('/v1/observability/targets/').func.view_class,
            MetricTargetView,
        )
        self.assertIs(
            resolve('/v1/observability/topology/').func.view_class,
            TopologyView,
        )
        response = self.client.get('/v1/observability/discovery/targets/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(body(response)['error'], 'unauthorized')

    def test_discovery_uses_independent_bearer_and_reserved_host_labels(self):
        denied = self.client.get(
            '/v1/observability/discovery/targets/',
            HTTP_AUTHORIZATION='Bearer wrong-token-that-is-long-enough',
        )
        self.assertEqual(denied.status_code, 401)

        response = self.client.get(
            '/v1/observability/discovery/targets/',
            HTTP_AUTHORIZATION='Bearer ' + DISCOVERY_TOKEN,
        )
        data = body(response)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]['targets'], ['10.20.30.40:9100'])
        self.assertEqual(data[0]['labels']['ops_platform_host_id'], str(self.host.id))
        self.assertEqual(data[0]['labels']['__metrics_path__'], '/metrics')
        self.assertEqual(response['Cache-Control'], 'no-store, max-age=0')

    @patch('apps.observability.services.Notify.make_monitor_notify')
    def test_webhook_aggregates_recovers_and_creates_a_new_episode(self, notify):
        unauthorized = self.webhook(self.alert_payload(), token='x' * 64)
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(AlertEvent.objects.count(), 0)

        first = self.webhook(self.alert_payload())
        duplicate = self.webhook(self.alert_payload())
        recovery = self.webhook(self.alert_payload(status='resolved'))
        second_episode = self.webhook(self.alert_payload())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(body(duplicate)['counts']['aggregated'], 1)
        self.assertEqual(body(recovery)['counts']['resolved'], 1)
        self.assertEqual(body(second_episode)['counts']['created'], 1)
        self.assertEqual(AlertEvent.objects.count(), 2)
        old, current = AlertEvent.objects.order_by('episode')
        self.assertEqual((old.episode, old.status, old.occurrence_count), (1, 'resolved', 3))
        self.assertEqual(old.starts_at.hour, 8)
        self.assertEqual((current.episode, current.status), (2, 'firing'))
        self.assertEqual(
            list(AlertTransition.objects.values_list('type', flat=True)),
            ['firing', 'resolved', 'firing'],
        )
        self.assertEqual(
            AuditEvent.objects.filter(action__startswith='observability.alert.').count(),
            3,
        )
        self.assertEqual(notify.call_count, 3)

    @patch('apps.observability.services.requests.get')
    def test_metric_query_is_predefined_and_host_authorized(self, get):
        self.grant_metrics()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'status': 'success',
            'data': {'resultType': 'vector', 'result': []},
        }
        get.return_value = response
        request = self.factory.get('/v1/observability/metrics/query/', {
            'host_id': self.host.id,
            'metric': 'cpu',
        })
        request.user = self.user
        result = MetricQueryView.as_view()(request)
        self.assertFalse(body(result)['error'])
        query = get.call_args[1]['params']['query']
        self.assertIn('ops_platform_host_id="%s"' % self.host.id, query)
        self.assertNotIn('query=', query)

        invalid = self.factory.get('/v1/observability/metrics/query/', {
            'host_id': self.host.id,
            'metric': 'up or vector(1)',
        })
        invalid.user = self.user
        self.assertIn('不支持', body(MetricQueryView.as_view()(invalid))['error'])

    def test_targets_and_alerts_are_filtered_by_asset_authorization(self):
        event = AlertEvent.objects.create(
            fingerprint='scoped',
            host=self.host,
            alert_name='ScopedAlert',
            labels='{}',
            annotations='{}',
        )
        request = self.factory.get('/v1/observability/targets/')
        request.user = self.user
        self.assertEqual(body(MetricTargetView.as_view()(request))['data'], [])

        self.grant_metrics()
        self.user.set_perms_cache()
        self.assertEqual(len(body(MetricTargetView.as_view()(request))['data']), 1)
        alerts = self.factory.get('/v1/observability/alerts/')
        alerts.user = self.user
        self.assertEqual(body(AlertEventView.as_view()(alerts))['data'][0]['id'], event.id)

        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect='deny',
            actions=json.dumps(['metrics.view']),
            created_by=self.admin,
        )
        self.assertEqual(body(MetricTargetView.as_view()(request))['data'], [])
        self.assertEqual(body(AlertEventView.as_view()(alerts))['data'], [])

    @patch('apps.observability.services.query_summary')
    def test_topology_includes_servers_services_and_dependency_edges(self, summary):
        summary.return_value = {
            str(self.host.id): {
                'availability': '1',
                'cpu': '12.5',
                'memory': '45',
                'disk': '61',
                'network': '1024',
            }
        }
        env = Environment.objects.create(
            name='prod', key='prod', created_by=self.admin,
        )
        service = Service.objects.create(
            name='mysql', key='mysql', desc='database', created_by=self.admin,
        )
        unmonitored = Host.objects.create(
            name='linux-02',
            hostname='10.20.30.41',
            port=22,
            username='root',
            created_by=self.admin,
        )
        app = App.objects.create(
            name='orders',
            key='orders',
            rel_services=json.dumps([service.id]),
            created_by=self.admin,
        )
        Deploy.objects.create(
            app=app,
            env=env,
            host_ids=json.dumps([self.host.id, unmonitored.id]),
            extend='1',
            is_audit=False,
            rst_notify='{}',
            created_by=self.admin,
        )

        request = self.factory.get('/v1/observability/topology/')
        request.user = self.admin
        data = body(TopologyView.as_view()(request))['data']
        node_ids = {item['id'] for item in data['nodes']}
        edge_types = {item['type'] for item in data['edges']}

        self.assertIn('host:%s' % self.host.id, node_ids)
        self.assertIn('host:%s' % unmonitored.id, node_ids)
        self.assertIn('app:%s' % app.id, node_ids)
        self.assertIn('service:%s' % service.id, node_ids)
        self.assertIn('deployed_on', edge_types)
        self.assertIn('depends_on_service', edge_types)
        host = [item for item in data['nodes'] if item['id'] == 'host:%s' % self.host.id][0]
        missing = [item for item in data['nodes'] if item['id'] == 'host:%s' % unmonitored.id][0]
        self.assertEqual(host['status'], 'healthy')
        self.assertEqual(missing['status'], 'unknown')

    def test_target_update_cannot_move_an_unauthorized_existing_target(self):
        other = Host.objects.create(
            name='linux-02', hostname='10.20.30.41', port=22,
            username='root', created_by=self.admin,
        )
        AccessGrant.objects.create(
            subject_user=self.user,
            host=other,
            effect='allow',
            actions=json.dumps(['metrics.view']),
            created_by=self.admin,
        )
        request = self.factory.post(
            '/v1/observability/targets/',
            data=json.dumps({
                'id': self.target.id,
                'host_id': other.id,
                'exporter_address': other.hostname,
                'exporter_port': 9100,
                'scheme': 'http',
                'metrics_path': '/metrics',
                'notify_grp': [],
                'notify_mode': [],
                'is_active': True,
            }),
            content_type='application/json',
        )
        request.user = self.user
        response = MetricTargetView.as_view()(request)
        self.assertEqual(body(response)['error'], '无权修改该指标采集目标')
        self.target.refresh_from_db()
        self.assertEqual(self.target.host_id, self.host.id)

    @patch('apps.observability.views.create_alertmanager_silence')
    @patch('apps.observability.views.delete_alertmanager_silence')
    def test_claim_and_real_alertmanager_silence_are_audited(self, delete_silence, create_silence):
        self.grant_metrics()
        create_silence.return_value = ('silence-1', datetime.now() + timedelta(hours=1))
        event = AlertEvent.objects.create(
            fingerprint='ops',
            host=self.host,
            alert_name='OpsAlert',
            labels=json.dumps({'alertname': 'OpsAlert', 'ops_platform_host_id': str(self.host.id)}),
            annotations='{}',
        )

        request = self.factory.patch(
            '/v1/observability/alerts/',
            data=json.dumps({'id': event.id, 'action': 'claim'}),
            content_type='application/json',
        )
        request.user = self.user
        result = AlertEventView.as_view()(request)
        self.assertFalse(body(result)['error'])
        event.refresh_from_db()
        self.assertEqual(event.claimed_by_id, self.user.id)

        request = self.factory.patch(
            '/v1/observability/alerts/',
            data=json.dumps({
                'id': event.id,
                'action': 'silence',
                'minutes': 60,
                'reason': 'planned maintenance',
            }),
            content_type='application/json',
        )
        request.user = self.user
        result = AlertEventView.as_view()(request)
        self.assertFalse(body(result)['error'])
        event.refresh_from_db()
        self.assertEqual(event.alertmanager_silence_id, 'silence-1')
        self.assertEqual(create_silence.call_count, 1)
        self.assertEqual(
            AuditEvent.objects.filter(
                action__in=('observability.alert.claim', 'observability.alert.silence')
            ).count(),
            2,
        )

        request = self.factory.patch(
            '/v1/observability/alerts/',
            data=json.dumps({'id': event.id, 'action': 'unsilence'}),
            content_type='application/json',
        )
        request.user = self.user
        self.assertFalse(body(AlertEventView.as_view()(request))['error'])
        delete_silence.assert_called_once_with('silence-1')

    @patch('apps.observability.services.requests.post')
    def test_alertmanager_silence_uses_utc_wire_timestamps(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'silenceID': 'utc-silence'}
        post.return_value = response
        event = AlertEvent.objects.create(
            fingerprint='utc',
            host=self.host,
            alert_name='UtcAlert',
            labels=json.dumps({'alertname': 'UtcAlert', 'ops_platform_host_id': str(self.host.id)}),
            annotations='{}',
        )
        silence_id, local_end = create_alertmanager_silence(
            event, 30, 'timezone validation', self.admin
        )
        payload = post.call_args[1]['json']
        wire_start = datetime.strptime(
            payload['startsAt'], '%Y-%m-%dT%H:%M:%S.%fZ'
        )
        self.assertEqual(silence_id, 'utc-silence')
        self.assertLess(abs((wire_start - datetime.utcnow()).total_seconds()), 5)
        self.assertLess(abs((local_end - (datetime.now() + timedelta(minutes=30))).total_seconds()), 5)

import json
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import resolve

from apps.account.models import Role, User
from apps.assets.models import AccessGrant
from apps.host.models import Host
from apps.observability.models import AlertEvent, MetricTarget
from apps.topology.models import TopologyEdge, TopologyNode
from apps.topology import services as topology_services
from apps.topology.views import (
    TopologyEdgeView,
    TopologyGraphView,
    TopologyNodeView,
    TopologyRuntimeScanView,
    TopologySchemaView,
    TopologySourceView,
)


def body(response):
    return json.loads(response.content.decode('utf-8'))


class TopologyTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = self.make_user('admin', is_supper=True)
        self.user = self.make_user('operator')
        role = Role.objects.create(
            name='topology-viewer',
            page_perms=json.dumps({
                'topology': {'topology': ['view', 'manage']},
            }),
            created_by=self.admin,
        )
        self.user.roles.add(role)
        self.host = Host.objects.create(
            name='linux-01',
            hostname='192.168.18.226',
            port=22,
            username='root',
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

    def test_routes_are_versioned(self):
        self.assertIs(
            resolve('/v1/topology/schema/').func.view_class,
            TopologySchemaView,
        )
        self.assertIs(
            resolve('/v1/topology/graph/').func.view_class,
            TopologyGraphView,
        )
        self.assertIs(
            resolve('/v1/topology/sources/').func.view_class,
            TopologySourceView,
        )
        self.assertIs(
            resolve('/v1/topology/nodes/').func.view_class,
            TopologyNodeView,
        )
        self.assertIs(
            resolve('/v1/topology/edges/').func.view_class,
            TopologyEdgeView,
        )
        self.assertIs(
            resolve('/v1/topology/runtime-scan/').func.view_class,
            TopologyRuntimeScanView,
        )

    def test_schema_defines_p0_contract(self):
        request = self.factory.get('/v1/topology/schema/')
        request.user = self.user
        data = body(TopologySchemaView.as_view()(request))['data']
        node_types = {item['key'] for item in data['node_types']}
        edge_types = {item['key'] for item in data['edge_types']}

        self.assertIn('host', node_types)
        self.assertIn('process', node_types)
        self.assertIn('application', node_types)
        self.assertIn('monitor_target', node_types)
        self.assertIn('calls', edge_types)
        self.assertIn('depends_on', edge_types)
        self.assertEqual(data['status_priority'][0], 'alert')
        self.assertIn('unknown', {item['key'] for item in data['statuses']})

    def test_model_rejects_invalid_json_source_and_self_edge(self):
        node = TopologyNode(
            key='host-226',
            type='host',
            name='226',
            source_type='host',
            source_id='',
            metadata='[]',
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            node.full_clean()

        source = TopologyNode.objects.create(
            key='external-api',
            type='external',
            name='external-api',
            created_by=self.admin,
        )
        edge = TopologyEdge(
            source=source,
            target=source,
            type='calls',
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            edge.full_clean()

    def test_graph_filters_host_nodes_and_edges_by_asset_authorization(self):
        host_node = TopologyNode.objects.create(
            key='host:%s' % self.host.id,
            type='host',
            name=self.host.name,
            source_type='host',
            source_id=str(self.host.id),
            status='unknown',
            created_by=self.admin,
        )
        service_node = TopologyNode.objects.create(
            key='service:web',
            type='service',
            name='web',
            status='unknown',
            created_by=self.admin,
        )
        TopologyEdge.objects.create(
            source=service_node,
            target=host_node,
            type='deployed_on',
            status='unknown',
            created_by=self.admin,
        )

        request = self.factory.get('/v1/topology/graph/')
        request.user = self.user
        data = body(TopologyGraphView.as_view()(request))['data']
        self.assertEqual([item['type'] for item in data['nodes']], ['service'])
        self.assertEqual(data['edges'], [])

    def test_runtime_scan_creates_process_port_and_connection_graph(self):
        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect='allow',
            actions=json.dumps(['host.view', 'ssh.connect']),
            created_by=self.admin,
        )
        Host.objects.create(
            name='client-01',
            hostname='192.168.18.10',
            port=22,
            username='root',
            created_by=self.admin,
        )
        self.user.set_perms_cache()

        class FakeSSH:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def exec_command_raw(self, command):
                return 0, """__OPS_TOPOLOGY_SECTION__ processes
1 0 systemd Ss
23 1 nginx S
42 1 app S
56 1 python S
__OPS_TOPOLOGY_SECTION__ process_hints
56 django
__OPS_TOPOLOGY_SECTION__ listeners
tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=23,fd=6))
tcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:((\"python\",pid=56,fd=6))
tcp LISTEN 0 128 0.0.0.0:39001 0.0.0.0:* -
__OPS_TOPOLOGY_SECTION__ connections
tcp ESTAB 0 0 192.168.18.226:80 192.168.18.10:51520 users:((\"nginx\",pid=23,fd=7))
tcp ESTAB 0 0 192.168.18.226:41002 127.0.0.1:8080 users:((\"nginx\",pid=23,fd=10))
tcp ESTAB 0 0 192.168.18.226:41000 192.168.18.20:3306 users:((\"python\",pid=56,fd=8))
tcp ESTAB 0 0 192.168.18.226:41001 192.168.18.21:6379 users:((\"python\",pid=56,fd=9))
"""

        original = Host.get_ssh
        Host.get_ssh = lambda host: FakeSSH()
        try:
            preview_request = self.factory.post(
                '/v1/topology/runtime-scan/',
                data=json.dumps({'host_ids': [self.host.id], 'dry_run': True}),
                content_type='application/json',
            )
            preview_request.user = self.user
            preview = body(TopologyRuntimeScanView.as_view()(preview_request))['data']
            self.assertEqual(preview[0]['status'], 'succeeded')
            self.assertEqual(preview[0]['business_connection_count'], 3)
            self.assertEqual(
                len(preview[0]['recommendations']['connections']),
                3,
            )
            service_names = {
                item['port']: item
                for item in preview[0]['recommendations']['services']
            }
            self.assertEqual(service_names[8080]['name'], 'Django 8080/tcp')
            self.assertTrue(service_names[8080]['selected'])
            self.assertEqual(service_names[8080]['detected_by'], 'framework')
            self.assertEqual(service_names[39001]['name'], 'TCP Service 39001/tcp')
            self.assertFalse(service_names[39001]['selected'])
            self.assertEqual(service_names[39001]['detected_by'], 'listener')
            self.assertFalse(TopologyNode.objects.filter(
                source_type='runtime',
                source_id=str(self.host.id),
            ).exists())
            selected_service_keys = [
                item['key']
                for item in preview[0]['recommendations']['services']
                if item['selected']
            ]
            selected_connection_keys = [
                item['key']
                for item in preview[0]['recommendations']['connections']
            ]

            request = self.factory.post(
                '/v1/topology/runtime-scan/',
                data=json.dumps({
                    'host_ids': [self.host.id],
                    'selected_service_keys': selected_service_keys,
                    'selected_connection_keys': selected_connection_keys,
                }),
                content_type='application/json',
            )
            request.user = self.user
            result = body(TopologyRuntimeScanView.as_view()(request))['data']
        finally:
            Host.get_ssh = original

        self.assertEqual(result[0]['status'], 'succeeded')
        self.assertEqual(result[0]['process_count'], 4)
        self.assertEqual(result[0]['listener_count'], 3)
        self.assertEqual(result[0]['connection_count'], 4)
        self.assertEqual(result[0]['dependency_connection_count'], 2)
        self.assertEqual(result[0]['business_connection_count'], 3)

        graph = topology_services.build_topology_graph(self.user)
        nodes = {item['key']: item for item in graph['nodes']}
        process_key = 'runtime:host:%s:process:23' % self.host.id
        django_process_key = 'runtime:host:%s:process:56' % self.host.id
        port_key = 'runtime:host:%s:port:tcp:80' % self.host.id
        backend_port_key = 'runtime:host:%s:port:tcp:8080' % self.host.id
        self.assertEqual(nodes[process_key]['type'], 'process')
        self.assertEqual(nodes[port_key]['type'], 'port')
        self.assertEqual(nodes[backend_port_key]['metadata']['runtime_layer'], 'backend')
        self.assertEqual(nodes[backend_port_key]['metadata']['service'], 'Django')
        self.assertIn('Django', nodes[backend_port_key]['name'])
        self.assertEqual(
            len([item for item in nodes.values() if item['type'] == 'database']),
            1,
        )
        self.assertEqual(
            len([item for item in nodes.values() if item['type'] == 'middleware']),
            1,
        )
        self.assertEqual(nodes[process_key]['metadata']['process_name'], 'nginx')
        serialized = json.dumps(graph, ensure_ascii=False)
        self.assertNotIn('exec_command_raw', serialized)
        self.assertNotIn('nginx -', serialized)
        self.assertNotIn('51520', serialized)
        self.assertNotIn('systemd[1]', serialized)
        edge_types = {item['type'] for item in graph['edges']}
        self.assertIn('deployed_on', edge_types)
        self.assertIn('listens_on', edge_types)
        self.assertIn('calls', edge_types)
        self.assertEqual(
            len([item for item in graph['edges'] if item['type'] == 'calls']),
            3,
        )
        nodes_by_id = {item['id']: item for item in graph['nodes']}
        dependency_calls = [
            item for item in graph['edges']
            if item['type'] == 'calls' and
            nodes_by_id[item['target']]['type'] in ('database', 'middleware')
        ]
        self.assertEqual(len(dependency_calls), 2)
        self.assertEqual(
            {item['source'] for item in dependency_calls},
            {nodes[django_process_key]['id']},
        )

        outsider_request = self.factory.get('/v1/topology/graph/')
        outsider_request.user = self.make_user('no-host-access')
        outsider_request.user.roles.add(self.user.roles.first())
        outsider_graph = body(TopologyGraphView.as_view()(outsider_request))['data']
        self.assertFalse([
            item for item in outsider_graph['nodes']
            if item['source']['type'] == 'runtime'
        ])

    def test_runtime_scan_links_to_remote_scanned_service(self):
        backend_host = Host.objects.create(
            name='linux-02',
            hostname='192.168.18.227',
            port=22,
            username='root',
            created_by=self.admin,
        )

        class FakeSSH:
            def __init__(self, hostname):
                self.hostname = hostname

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def exec_command_raw(self, command):
                if self.hostname == '192.168.18.227':
                    return 0, """__OPS_TOPOLOGY_SECTION__ processes
56 1 java S
__OPS_TOPOLOGY_SECTION__ listeners
tcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:((\"java\",pid=56,fd=6))
__OPS_TOPOLOGY_SECTION__ connections
"""
                return 0, """__OPS_TOPOLOGY_SECTION__ processes
23 1 nginx S
__OPS_TOPOLOGY_SECTION__ listeners
tcp LISTEN 0 128 0.0.0.0:80 0.0.0.0:* users:((\"nginx\",pid=23,fd=6))
__OPS_TOPOLOGY_SECTION__ connections
tcp ESTAB 0 0 192.168.18.226:41002 192.168.18.227:8080 users:((\"nginx\",pid=23,fd=10))
"""

        original = Host.get_ssh
        Host.get_ssh = lambda host: FakeSSH(host.hostname)
        try:
            preview = topology_services.sync_runtime_topology(
                self.admin, [backend_host.id], dry_run=True
            )[0]
            topology_services.sync_runtime_topology(
                self.admin,
                [backend_host.id],
                selected_service_keys=[
                    item['key']
                    for item in preview['recommendations']['services']
                ],
                selected_connection_keys=[],
            )
            preview = topology_services.sync_runtime_topology(
                self.admin, [self.host.id], dry_run=True
            )[0]
            topology_services.sync_runtime_topology(
                self.admin,
                [self.host.id],
                selected_service_keys=[
                    item['key']
                    for item in preview['recommendations']['services']
                ],
                selected_connection_keys=[
                    item['key']
                    for item in preview['recommendations']['connections']
                ],
            )
        finally:
            Host.get_ssh = original

        graph = topology_services.build_topology_graph(self.admin)
        nodes = {item['key']: item for item in graph['nodes']}
        remote_port_key = 'runtime:host:%s:port:tcp:8080' % backend_host.id
        self.assertEqual(nodes[remote_port_key]['metadata']['runtime_layer'], 'backend')
        self.assertIn('java', nodes[remote_port_key]['name'])

        calls = [item for item in graph['edges'] if item['type'] == 'calls']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['target'], nodes[remote_port_key]['id'])
        self.assertIn('java backend', calls[0]['label'])

    def test_runtime_scan_keeps_tcp6_dependency_listeners_and_inbound_calls(self):
        redis_host = Host.objects.create(
            name='redis-01',
            hostname='192.168.18.244',
            port=22,
            username='root',
            created_by=self.admin,
        )

        class FakeSSH:
            def __init__(self, hostname):
                self.hostname = hostname

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def exec_command_raw(self, command):
                if self.hostname == '192.168.18.244':
                    return 0, """__OPS_TOPOLOGY_SECTION__ processes
1 0 systemd Ss
__OPS_TOPOLOGY_SECTION__ listeners
tcp 0 0 192.168.18.244:6379 0.0.0.0:* LISTEN -
__OPS_TOPOLOGY_SECTION__ connections
tcp ESTAB 0 0 192.168.18.244:6379 192.168.18.90:54210 users:((\"redis-server\",pid=63,fd=7))
"""
                return 0, """__OPS_TOPOLOGY_SECTION__ processes
1 0 systemd Ss
__OPS_TOPOLOGY_SECTION__ listeners
tcp6 0 0 :::3306 :::* LISTEN -
__OPS_TOPOLOGY_SECTION__ connections
"""

        original = Host.get_ssh
        Host.get_ssh = lambda host: FakeSSH(host.hostname)
        try:
            preview = topology_services.sync_runtime_topology(
                self.admin, [self.host.id, redis_host.id], dry_run=True
            )
            service_keys = []
            connection_keys = []
            for item in preview:
                service_keys.extend([
                    service['key']
                    for service in item['recommendations']['services']
                ])
                connection_keys.extend([
                    connection['key']
                    for connection in item['recommendations']['connections']
                ])
            topology_services.sync_runtime_topology(
                self.admin,
                [self.host.id, redis_host.id],
                selected_service_keys=service_keys,
                selected_connection_keys=connection_keys,
            )
        finally:
            Host.get_ssh = original

        graph = topology_services.build_topology_graph(self.admin)
        nodes = {item['key']: item for item in graph['nodes']}
        mysql_key = 'runtime:host:%s:port:tcp:3306' % self.host.id
        redis_key = 'runtime:host:%s:port:tcp:6379' % redis_host.id
        self.assertIn(mysql_key, nodes)
        self.assertIn(redis_key, nodes)
        self.assertEqual(nodes[mysql_key]['metadata']['service'], 'MySQL')
        self.assertEqual(nodes[redis_key]['metadata']['service'], 'Redis')

        redis_calls = [
            item for item in graph['edges']
            if item['type'] == 'calls' and item['target'] == nodes[redis_key]['id']
        ]
        self.assertEqual(len(redis_calls), 1)
        self.assertIn('Redis', redis_calls[0]['label'])
        self.assertEqual(redis_calls[0]['metadata']['target_side'], 'local')

    def test_runtime_scan_maps_netstat_pid_and_nonstandard_mysql_port(self):
        class FakeSSH:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def exec_command_raw(self, command):
                self.assertIn('sudo -n netstat', command)
                return 0, """__OPS_TOPOLOGY_SECTION__ processes
2508 1 python S
1706 1 mysqld S
1900 1 smbd S
__OPS_TOPOLOGY_SECTION__ process_hints
2508 django
__OPS_TOPOLOGY_SECTION__ listeners
tcp6 0 0 :::13306 :::* LISTEN 1706/mysqld
tcp 0 0 192.168.18.226:445 0.0.0.0:* LISTEN 1900/smbd
__OPS_TOPOLOGY_SECTION__ connections
tcp 0 0 192.168.18.226:59456 192.168.18.226:13306 ESTABLISHED 2508/python
tcp6 0 0 192.168.18.226:13306 192.168.18.226:59456 ESTABLISHED 1706/mysqld
tcp 0 0 192.168.18.226:445 192.168.30.106:52300 ESTABLISHED 1900/smbd
"""

        original = Host.get_ssh
        Host.get_ssh = lambda host: FakeSSH()
        try:
            preview = topology_services.sync_runtime_topology(
                self.admin, [self.host.id], dry_run=True
            )[0]
            topology_services.sync_runtime_topology(
                self.admin,
                [self.host.id],
                selected_service_keys=[
                    item['key']
                    for item in preview['recommendations']['services']
                    if item['selected']
                ],
                selected_connection_keys=[
                    item['key']
                    for item in preview['recommendations']['connections']
                    if item['selected']
                ],
            )
        finally:
            Host.get_ssh = original

        self.assertEqual(preview['business_connection_count'], 1)
        service = preview['recommendations']['services'][0]
        self.assertEqual(service['port'], 13306)
        self.assertEqual(service['name'], 'MySQL 13306/tcp')
        self.assertEqual(service['detected_by'], 'process')
        tcp_services = [
            item for item in preview['recommendations']['services']
            if item['port'] == 445
        ]
        self.assertEqual(tcp_services[0]['name'], 'TCP Service 445/tcp')
        self.assertFalse(tcp_services[0]['selected'])
        connection = preview['recommendations']['connections'][0]
        self.assertEqual(connection['source'], 'python[2508]')
        self.assertEqual(connection['service'], 'MySQL')

        graph = topology_services.build_topology_graph(self.admin)
        nodes = {item['key']: item for item in graph['nodes']}
        mysql_key = 'runtime:host:%s:port:tcp:13306' % self.host.id
        django_key = 'runtime:host:%s:process:2508' % self.host.id
        self.assertEqual(nodes[mysql_key]['metadata']['service'], 'MySQL')
        self.assertEqual(nodes[django_key]['metadata']['service'], 'Django')
        calls = [item for item in graph['edges'] if item['type'] == 'calls']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['source'], nodes[django_key]['id'])
        self.assertEqual(calls[0]['target'], nodes[mysql_key]['id'])

    def test_runtime_scan_uses_django_config_without_open_socket(self):
        class FakeSSH:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def exec_command_raw(self, command):
                return 0, """__OPS_TOPOLOGY_SECTION__ processes
2493 1 python S
2508 1 python S
1706 1 mysqld S
__OPS_TOPOLOGY_SECTION__ process_hints
2493 django
2508 django
__OPS_TOPOLOGY_SECTION__ config_hints
2493\t/home/app/settings.py:10:'ENGINE': 'django.db.backends.mysql',
2493\t/home/app/settings.py:11:'HOST': '192.168.18.226',
2493\t/home/app/settings.py:12:'PORT': '13306',
2508\t/home/app/settings.py:10:'ENGINE': 'django.db.backends.mysql',
2508\t/home/app/settings.py:11:'HOST': '192.168.18.226',
2508\t/home/app/settings.py:12:'PORT': '13306',
__OPS_TOPOLOGY_SECTION__ listeners
tcp 0 0 0.0.0.0:8282 0.0.0.0:* LISTEN 2508/python
tcp6 0 0 :::13306 :::* LISTEN 1706/mysqld
__OPS_TOPOLOGY_SECTION__ connections
"""

        original = Host.get_ssh
        Host.get_ssh = lambda host: FakeSSH()
        try:
            preview = topology_services.sync_runtime_topology(
                self.admin, [self.host.id], dry_run=True
            )[0]
            topology_services.sync_runtime_topology(
                self.admin,
                [self.host.id],
                selected_service_keys=[
                    item['key']
                    for item in preview['recommendations']['services']
                    if item['selected']
                ],
                selected_connection_keys=[
                    item['key']
                    for item in preview['recommendations']['connections']
                    if item['selected']
                ],
            )
        finally:
            Host.get_ssh = original

        self.assertEqual(preview['business_connection_count'], 1)
        connection = preview['recommendations']['connections'][0]
        self.assertEqual(connection['source'], 'python[2508]')
        self.assertEqual(connection['service'], 'MySQL')
        self.assertEqual(connection['detected_by'], 'config')

        graph = topology_services.build_topology_graph(self.admin)
        nodes = {item['key']: item for item in graph['nodes']}
        mysql_key = 'runtime:host:%s:port:tcp:13306' % self.host.id
        django_key = 'runtime:host:%s:process:2508' % self.host.id
        calls = [item for item in graph['edges'] if item['type'] == 'calls']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['source'], nodes[django_key]['id'])
        self.assertEqual(calls[0]['target'], nodes[mysql_key]['id'])
        self.assertEqual(calls[0]['metadata']['detected_by'], 'config')

    def test_sources_and_crud_build_visible_graph_edge(self):
        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect='allow',
            actions=json.dumps(['host.view']),
            created_by=self.admin,
        )
        self.user.set_perms_cache()

        request = self.factory.get('/v1/topology/sources/')
        request.user = self.user
        data = body(TopologySourceView.as_view()(request))['data']
        self.assertEqual(data['hosts'][0]['hostname'], '192.168.18.226')

        request = self.factory.post(
            '/v1/topology/nodes/',
            data=json.dumps({
                'key': 'host:%s' % self.host.id,
                'type': 'host',
                'name': self.host.name,
                'source_type': 'host',
                'source_id': str(self.host.id),
                'status': 'unknown',
                'status_source': 'manual',
                'metadata': {},
                'position': {},
                'is_active': True,
            }),
            content_type='application/json',
        )
        request.user = self.user
        host_node = body(TopologyNodeView.as_view()(request))['data']

        request = self.factory.post(
            '/v1/topology/nodes/',
            data=json.dumps({
                'key': 'service:web',
                'type': 'service',
                'name': 'web',
                'source_type': 'manual',
                'status': 'unknown',
                'status_source': 'manual',
                'metadata': {},
                'position': {},
                'is_active': True,
            }),
            content_type='application/json',
        )
        request.user = self.user
        service_node = body(TopologyNodeView.as_view()(request))['data']

        request = self.factory.post(
            '/v1/topology/edges/',
            data=json.dumps({
                'source': service_node['id'],
                'target': host_node['id'],
                'type': 'deployed_on',
                'label': '部署于',
                'status': 'unknown',
                'status_source': 'manual',
                'probed': False,
                'metadata': {},
                'is_active': True,
            }),
            content_type='application/json',
        )
        request.user = self.user
        edge = body(TopologyEdgeView.as_view()(request))['data']
        self.assertEqual(edge['source'], service_node['id'])
        self.assertEqual(edge['target'], host_node['id'])

        request = self.factory.get('/v1/topology/graph/')
        request.user = self.user
        graph = body(TopologyGraphView.as_view()(request))['data']
        self.assertEqual(len(graph['nodes']), 2)
        self.assertEqual(len(graph['edges']), 1)

    def test_graph_maps_metric_and_alert_status_to_host_and_edges(self):
        target = MetricTarget.objects.create(
            host=self.host,
            exporter_address='192.168.18.226',
            exporter_port=9100,
            metrics_path='/metrics',
            created_by=self.admin,
        )
        AlertEvent.objects.create(
            fingerprint='alert-226',
            host=self.host,
            alert_name='SpugHostExporterDown',
            severity='critical',
            status='firing',
            instance='192.168.18.226:9100',
            summary='主机 192.168.18.226 指标采集离线',
        )
        host_node = TopologyNode.objects.create(
            key='host:%s' % self.host.id,
            type='host',
            name=self.host.name,
            source_type='host',
            source_id=str(self.host.id),
            status='unknown',
            created_by=self.admin,
        )
        target_node = TopologyNode.objects.create(
            key='metric_target:%s' % target.id,
            type='monitor_target',
            name='node exporter',
            source_type='metric_target',
            source_id=str(target.id),
            status='unknown',
            created_by=self.admin,
        )
        service_node = TopologyNode.objects.create(
            key='service:web',
            type='service',
            name='web',
            status='unknown',
            created_by=self.admin,
        )
        TopologyEdge.objects.create(
            source=target_node,
            target=host_node,
            type='collects',
            status='unknown',
            created_by=self.admin,
        )
        TopologyEdge.objects.create(
            source=service_node,
            target=host_node,
            type='deployed_on',
            status='unknown',
            created_by=self.admin,
        )

        original = topology_services.query_summary
        topology_services.query_summary = lambda host_ids: {
            str(self.host.id): {'availability': '0'}
        }
        try:
            graph = topology_services.build_topology_graph(self.admin)
        finally:
            topology_services.query_summary = original

        nodes = {item['key']: item for item in graph['nodes']}
        self.assertEqual(nodes['host:%s' % self.host.id]['status'], 'critical')
        self.assertEqual(
            nodes['host:%s' % self.host.id]['status_source'],
            'alert',
        )
        self.assertEqual(nodes['metric_target:%s' % target.id]['status'], 'critical')
        self.assertEqual(nodes['service:web']['status'], 'critical')
        self.assertEqual(len(graph['edges']), 2)
        self.assertEqual(graph['summary_error'], '')

        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect='allow',
            actions=json.dumps(['host.view']),
            created_by=self.admin,
        )
        self.user.set_perms_cache()
        request = self.factory.get('/v1/topology/graph/')
        data = body(TopologyGraphView.as_view()(request))['data']
        node_ids = {item['id'] for item in data['nodes']}
        self.assertIn(str(host_node.id), node_ids)
        self.assertIn(str(service_node.id), node_ids)
        self.assertEqual(len(data['edges']), 1)

        AccessGrant.objects.create(
            subject_user=self.user,
            host=self.host,
            effect='deny',
            actions=json.dumps(['host.view']),
            created_by=self.admin,
        )
        data = body(TopologyGraphView.as_view()(request))['data']
        self.assertEqual([item['type'] for item in data['nodes']], ['service'])
        self.assertEqual(data['edges'], [])

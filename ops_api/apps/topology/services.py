import hashlib
import ipaddress
import json
import re
import shlex
from datetime import datetime

from django.db import transaction
from django.db.models import Q

from apps.account.utils import get_host_perms, has_host_perm
from apps.app.models import App
from apps.config.models import Service
from apps.host.models import Host
from apps.observability.models import AlertEvent, MetricTarget
from apps.observability.services import ObservabilityError, query_summary

from .models import TopologyEdge, TopologyNode


STATUS_PRIORITY = (
    'alert',
    'metrics',
    'probe',
    'runtime',
    'manual',
    'inherited',
    'unknown',
)

STATUS_RANK = {
    'critical': 60,
    'offline': 55,
    'warning': 50,
    'info': 40,
    'disabled': 30,
    'healthy': 20,
    'unknown': 10,
}
RUNTIME_TOPOLOGY_MARKER = '__OPS_TOPOLOGY_SECTION__'
RUNTIME_TOPOLOGY_COMMAND = """
SUDO=''
if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  SUDO='sudo -n'
fi
printf '__OPS_TOPOLOGY_SECTION__ processes\\n'
$SUDO ps -eo pid,ppid,comm,stat 2>/dev/null | tail -n +2 | head -1000
printf '__OPS_TOPOLOGY_SECTION__ process_hints\\n'
$SUDO ps axww -o pid= -o args= 2>/dev/null | awk '
{
  pid=$1
  line=tolower($0)
  hint=""
  if (line ~ /django/ || line ~ /manage\\.py/ || line ~ /gunicorn/ || line ~ /uwsgi/ || line ~ /uvicorn/ || line ~ /daphne/) hint="django"
  else if (line ~ /celery/) hint="celery"
  else if (line ~ /spring/ || line ~ /tomcat/ || line ~ /\\.jar/) hint="java"
  else if (line ~ /nestjs/ || line ~ /express/ || line ~ /next/ || line ~ /nuxt/) hint="node"
  if (pid ~ /^[0-9]+$/ && hint != "") printf "%s %s\\n", pid, hint
}' 2>/dev/null | head -1000
printf '__OPS_TOPOLOGY_SECTION__ config_hints\\n'
$SUDO ps axww -o pid= -o args= 2>/dev/null | awk '
{
  pid=$1
  line=tolower($0)
  if (pid ~ /^[0-9]+$/ && (line ~ /django/ || line ~ /manage\\.py/ || line ~ /gunicorn/ || line ~ /uwsgi/ || line ~ /uvicorn/ || line ~ /daphne/)) print pid
}' 2>/dev/null | head -100 | while read pid; do
  cwd=$($SUDO readlink "/proc/$pid/cwd" 2>/dev/null)
  if [ -z "$cwd" ] || [ ! -d "$cwd" ]; then
    continue
  fi
  find "$cwd" -maxdepth 4 -type f \\( -name 'settings*.py' -o -name 'config.py' -o -name '.env' \\) 2>/dev/null | head -40 | while IFS= read -r file; do
    grep -HnEi "django\\.db\\.backends|['\\\"]HOST['\\\"]|['\\\"]PORT['\\\"]|DB_HOST|DB_PORT|MYSQL_HOST|MYSQL_PORT|POSTGRES_HOST|POSTGRES_PORT|REDIS_HOST|REDIS_PORT|redis://" "$file" 2>/dev/null | grep -Evi "PASSWORD|PASS|SECRET|KEY|TOKEN" | head -120 | while IFS= read -r line; do
      printf "%s\\t%s\\n" "$pid" "$line"
    done
  done
done | head -500
printf '__OPS_TOPOLOGY_SECTION__ app_hints\\n'
nginx_pid=$($SUDO ps axww -o pid= -o comm= 2>/dev/null | awk '$2 ~ /nginx/ {print $1; exit}')
nginx_bin=''
if command -v nginx >/dev/null 2>&1; then
  nginx_bin=$(command -v nginx)
elif [ -x /usr/local/nginx/sbin/nginx ]; then
  nginx_bin=/usr/local/nginx/sbin/nginx
fi
if [ -n "$nginx_pid" ]; then
  if [ -n "$nginx_bin" ]; then
    $SUDO "$nginx_bin" -T 2>/dev/null | grep -nEi "proxy_pass|uwsgi_pass|fastcgi_pass|grpc_pass|server[[:space:]]+[A-Za-z0-9_.:-]+:[0-9]+" | grep -Evi "PASSWORD|PASS|SECRET|KEY|TOKEN" | head -300 | while IFS= read -r line; do
      printf "%s\\t%s\\n" "$nginx_pid" "$line"
    done
  fi
  $SUDO grep -RInE "proxy_pass|uwsgi_pass|fastcgi_pass|grpc_pass|server[[:space:]]+[A-Za-z0-9_.:-]+:[0-9]+" /etc/nginx /usr/local/nginx/conf 2>/dev/null | grep -Evi "PASSWORD|PASS|SECRET|KEY|TOKEN" | head -300 | while IFS= read -r line; do
    printf "%s\\t%s\\n" "$nginx_pid" "$line"
  done
fi
printf '__OPS_TOPOLOGY_SECTION__ listeners\\n'
if command -v ss >/dev/null 2>&1; then
  $SUDO ss -H -lntup 2>/dev/null | head -1000
else
  $SUDO netstat -lntup 2>/dev/null | tail -n +3 | head -1000
fi
printf '__OPS_TOPOLOGY_SECTION__ connections\\n'
if command -v ss >/dev/null 2>&1; then
  $SUDO ss -H -tanp state established 2>/dev/null | head -1000
else
  $SUDO netstat -antp 2>/dev/null | grep ESTABLISHED | head -1000
fi
"""
RUNTIME_DEPENDENCY_PORTS = {
    3306: ('database', 'MySQL'),
    5432: ('database', 'PostgreSQL'),
    1433: ('database', 'SQL Server'),
    1521: ('database', 'Oracle'),
    27017: ('database', 'MongoDB'),
    6379: ('middleware', 'Redis'),
    6380: ('middleware', 'Redis'),
    11211: ('middleware', 'Memcached'),
    5672: ('middleware', 'RabbitMQ'),
    9092: ('middleware', 'Kafka'),
    2181: ('middleware', 'ZooKeeper'),
    2379: ('middleware', 'etcd'),
    2380: ('middleware', 'etcd'),
    8500: ('middleware', 'Consul'),
    8123: ('database', 'ClickHouse'),
    9000: ('database', 'ClickHouse'),
}
RUNTIME_FRONTEND_PORTS = {
    80, 443, 3000, 3001, 3002, 4200, 5173, 5174,
}
RUNTIME_BACKEND_PORTS = {
    5000, 5001, 7001, 8000, 8001, 8080, 8081, 8082, 8088,
    8090, 9001, 9002, 10000,
}
RUNTIME_BACKEND_PORT_RANGES = (
    (5000, 5999),
    (7000, 8999),
    (8080, 8099),
    (9001, 9099),
    (10000, 10999),
)
RUNTIME_APP_PORT_EXCLUDES = {
    22, 25, 53, 110, 111, 123, 135, 137, 138, 139, 143, 323,
    389, 445, 465, 587, 636, 993, 995, 3389, 5900, 9090, 9093,
    9100, 9200, 9300,
}
RUNTIME_FRONTEND_PROCESSES = {
    'nginx', 'openresty', 'httpd', 'apache2', 'caddy', 'haproxy',
    'traefik', 'vite', 'vue', 'nuxt', 'next',
}
RUNTIME_BACKEND_PROCESSES = {
    'python', 'python2', 'python3', 'gunicorn', 'uwsgi', 'uvicorn',
    'daphne', 'java', 'go', 'node', 'pm2', 'dotnet', 'php', 'php-fpm',
    'ruby', 'puma', 'passenger', 'beam.smp', 'erl', 'elixir',
}
RUNTIME_DATABASE_PROCESSES = {
    'mysqld', 'mariadbd', 'postgres', 'postmaster', 'mongod',
    'clickhouse-server', 'oracle',
}
RUNTIME_MIDDLEWARE_PROCESSES = {
    'redis-server', 'memcached', 'rabbitmq-server', 'beam.smp', 'kafka',
    'zookeeper', 'etcd', 'consul',
}
RUNTIME_SYSTEM_PROCESSES = {
    'systemd', 'sshd', 'dockerd', 'containerd', 'containerd-shim', 'kubelet',
    'prometheus', 'alertmanager', 'node_exporter', 'mysqld_exporter',
    'redis_exporter', 'blackbox_exporter', 'process-exporter', 'supervisord',
    'cron', 'crond', 'rsyslogd', 'journald', 'chronyd', 'ntpd',
    'rpcbind',
}
RUNTIME_LOCAL_ADDRESSES = {'127.0.0.1', '::1', 'localhost'}
RUNTIME_FRAMEWORK_PROFILES = {
    'django': {
        'service': 'Django',
        'runtime_layer': 'backend',
    },
    'celery': {
        'service': 'Celery Worker',
        'runtime_layer': 'backend',
    },
    'java': {
        'service': 'Java backend',
        'runtime_layer': 'backend',
    },
    'node': {
        'service': 'Node backend',
        'runtime_layer': 'backend',
    },
}
RUNTIME_PROCESS_PROFILES = {
    'mysqld': {
        'kind': 'dependency',
        'type': 'database',
        'service': 'MySQL',
        'runtime_layer': 'database',
    },
    'mariadbd': {
        'kind': 'dependency',
        'type': 'database',
        'service': 'MySQL',
        'runtime_layer': 'database',
    },
    'postgres': {
        'kind': 'dependency',
        'type': 'database',
        'service': 'PostgreSQL',
        'runtime_layer': 'database',
    },
    'postmaster': {
        'kind': 'dependency',
        'type': 'database',
        'service': 'PostgreSQL',
        'runtime_layer': 'database',
    },
    'mongod': {
        'kind': 'dependency',
        'type': 'database',
        'service': 'MongoDB',
        'runtime_layer': 'database',
    },
    'clickhouse-server': {
        'kind': 'dependency',
        'type': 'database',
        'service': 'ClickHouse',
        'runtime_layer': 'database',
    },
    'redis-server': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'Redis',
        'runtime_layer': 'middleware',
    },
    'memcached': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'Memcached',
        'runtime_layer': 'middleware',
    },
    'rabbitmq-server': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'RabbitMQ',
        'runtime_layer': 'middleware',
    },
    'kafka': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'Kafka',
        'runtime_layer': 'middleware',
    },
    'zookeeper': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'ZooKeeper',
        'runtime_layer': 'middleware',
    },
    'etcd': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'etcd',
        'runtime_layer': 'middleware',
    },
    'consul': {
        'kind': 'dependency',
        'type': 'middleware',
        'service': 'Consul',
        'runtime_layer': 'middleware',
    },
}


def topology_schema():
    return {
        'node_types': [
            {'key': key, 'name': name}
            for key, name in TopologyNode.TYPES
        ],
        'edge_types': [
            {'key': key, 'name': name}
            for key, name in TopologyEdge.TYPES
        ],
        'statuses': [
            {'key': key, 'name': name}
            for key, name in TopologyNode.STATUSES
        ],
        'status_sources': [
            {'key': key, 'name': name}
            for key, name in TopologyNode.STATUS_SOURCES
        ],
        'status_priority': list(STATUS_PRIORITY),
        'source_types': [
            {'key': key, 'name': name}
            for key, name in TopologyNode.SOURCE_TYPES
        ],
        'permission_policy': {
            'host': 'host.view',
            'application': 'deploy.app.view or config.app.view plus deploy scope',
            'service': 'config.src.view',
            'metric_target': 'metrics.view on the target host',
            'manual': 'topology.topology.view',
        },
    }


def _source_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_app_access(user, app_id):
    if user.is_supper:
        return True
    if not (
            user.has_perms(['deploy.app.view']) or
            user.has_perms(['config.app.view'])):
        return False
    return app_id in user.deploy_perms['apps']


def _access_scope(user):
    if user.is_supper:
        return {
            'superuser': True,
            'host_ids': None,
            'metric_host_ids': None,
            'metric_target_ids': None,
            'app_ids': None,
            'service_visible': True,
        }
    metric_host_ids = set(get_host_perms(user, action='metrics.view'))
    metric_target_ids = set(
        MetricTarget.objects.filter(
            host_id__in=metric_host_ids
        ).values_list('id', flat=True)
    )
    can_view_apps = user.has_perms(['deploy.app.view']) or user.has_perms([
        'config.app.view'
    ])
    app_ids = set(user.deploy_perms['apps']) if can_view_apps else set()
    return {
        'superuser': False,
        'host_ids': set(get_host_perms(user, action='host.view')),
        'metric_host_ids': metric_host_ids,
        'metric_target_ids': metric_target_ids,
        'app_ids': app_ids,
        'service_visible': bool(user.has_perms(['config.src.view'])),
    }


def _node_visible(node, scope):
    if scope['superuser']:
        return True
    source_id = _source_id(node.source_id)
    if node.source_type == 'host':
        return source_id in scope['host_ids']
    if node.source_type == 'application':
        return source_id in scope['app_ids']
    if node.source_type == 'service':
        return scope['service_visible'] and Service.objects.filter(
            id=source_id
        ).exists()
    if node.source_type == 'metric_target':
        return source_id in scope['metric_target_ids']
    if node.source_type == 'runtime':
        host_id = _source_id(node.source_id)
        if host_id is None:
            try:
                host_id = _source_id((node.metadata_data or {}).get('host_id'))
            except (TypeError, ValueError):
                host_id = None
        return host_id is not None and host_id in scope['host_ids']
    return node.source_type in ('manual', 'external')


def node_visible(user, node):
    return _node_visible(node, _access_scope(user))


def visible_topology_nodes(user, active_only=True):
    scope = _access_scope(user)
    queryset = TopologyNode.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)
    return [
        item for item in queryset
        if _node_visible(item, scope)
    ]


def source_options(user):
    if user.is_supper:
        hosts = Host.objects.all()
        apps = App.objects.all()
        services = Service.objects.all()
        metric_targets = MetricTarget.objects.select_related('host')
    else:
        hosts = Host.objects.filter(
            id__in=get_host_perms(user, action='host.view')
        )
        if user.has_perms(['deploy.app.view']) or user.has_perms([
                'config.app.view']):
            apps = App.objects.filter(id__in=user.deploy_perms['apps'])
        else:
            apps = App.objects.none()
        services = (
            Service.objects.all()
            if user.has_perms(['config.src.view'])
            else Service.objects.none()
        )
        metric_targets = MetricTarget.objects.select_related('host').filter(
            host_id__in=get_host_perms(user, action='metrics.view')
        )
    return {
        'hosts': [
            {
                'id': item.id,
                'name': item.name,
                'hostname': item.hostname,
                'key': 'host:%s' % item.id,
            }
            for item in hosts
        ],
        'applications': [
            {
                'id': item.id,
                'name': item.name,
                'key': 'application:%s' % item.id,
                'app_key': item.key,
                'description': item.desc or '',
            }
            for item in apps
        ],
        'services': [
            {
                'id': item.id,
                'name': item.name,
                'key': 'service:%s' % item.id,
                'service_key': item.key,
                'description': item.desc or '',
            }
            for item in services
        ],
        'metric_targets': [
            {
                'id': item.id,
                'name': '%s:%s' % (item.exporter_address, item.exporter_port),
                'key': 'metric_target:%s' % item.id,
                'host_id': item.host_id,
                'host_name': item.host.name,
                'hostname': item.host.hostname,
            }
            for item in metric_targets
        ],
    }


def validate_node_source(user, source_type, source_id):
    source_pk = _source_id(source_id)
    if source_type in ('manual', 'external', 'runtime'):
        return True, ''
    if source_pk is None:
        return False, '来源对象 ID 格式错误'
    if source_type == 'host':
        if source_pk not in get_host_perms(user, action='host.view'):
            return False, '无权关联该主机资产'
        if not Host.objects.filter(pk=source_pk).exists():
            return False, '未找到指定主机资产'
        return True, ''
    if source_type == 'application':
        if not _has_app_access(user, source_pk):
            return False, '无权关联该应用'
        if not App.objects.filter(pk=source_pk).exists():
            return False, '未找到指定应用'
        return True, ''
    if source_type == 'service':
        if not user.is_supper and not user.has_perms(['config.src.view']):
            return False, '无权关联该服务'
        if not Service.objects.filter(pk=source_pk).exists():
            return False, '未找到指定服务'
        return True, ''
    if source_type == 'metric_target':
        target = MetricTarget.objects.filter(pk=source_pk).first()
        if not target:
            return False, '未找到指定监控采集目标'
        if not user.is_supper and target.host_id not in get_host_perms(
                user, action='metrics.view'):
            return False, '无权关联该监控采集目标'
        return True, ''
    return False, '不支持的来源类型'


def _highest_status(statuses):
    statuses = [item for item in statuses if item]
    if not statuses:
        return 'unknown'
    return sorted(
        statuses,
        key=lambda item: STATUS_RANK.get(item, STATUS_RANK['unknown']),
        reverse=True,
    )[0]


def _alert_status(alerts):
    severities = [item.severity for item in alerts]
    if 'critical' in severities:
        return 'critical'
    if 'warning' in severities:
        return 'warning'
    if 'info' in severities:
        return 'info'
    return None


def _host_status(target, metrics, alerts):
    if not target:
        return None, None
    if not target.is_active:
        return 'disabled', 'metrics'
    alert_status = _alert_status(alerts)
    if alert_status:
        return alert_status, 'alert'
    if str(metrics.get('availability')) == '1':
        return 'healthy', 'metrics'
    if 'availability' in metrics:
        return 'offline', 'metrics'
    if metrics:
        return 'healthy', 'metrics'
    return 'unknown', 'metrics'


def _status_detail(status, status_source, evidence):
    detail = {'status': status, 'status_source': status_source}
    detail.update(evidence)
    return detail


def _observability_status(nodes):
    host_ids = set()
    target_ids = set()
    for node in nodes:
        source_id = _source_id(node.source_id)
        if node.source_type == 'host' and source_id is not None:
            host_ids.add(source_id)
        elif node.source_type == 'metric_target' and source_id is not None:
            target_ids.add(source_id)

    targets = list(MetricTarget.objects.select_related('host').filter(
        host_id__in=host_ids
    ))
    if target_ids:
        extra_targets = MetricTarget.objects.select_related('host').filter(
            id__in=target_ids
        )
        target_map = {item.id: item for item in targets}
        for item in extra_targets:
            target_map[item.id] = item
            host_ids.add(item.host_id)
        targets = list(target_map.values())
    else:
        target_map = {item.id: item for item in targets}

    target_by_host = {item.host_id: item for item in targets}
    target_host_ids = {item.host_id for item in targets}
    summary_error = ''
    try:
        summary = query_summary(target_host_ids) if target_host_ids else {}
    except ObservabilityError as exc:
        summary = {}
        summary_error = str(exc)

    alert_map = {}
    firing_alerts = AlertEvent.objects.select_related('host').filter(
        status='firing',
        host_id__in=target_host_ids,
    )
    for alert in firing_alerts:
        alert_map.setdefault(alert.host_id, []).append(alert)

    host_status = {}
    for host_id in host_ids:
        target = target_by_host.get(host_id)
        metrics = summary.get(str(host_id), {})
        alerts = alert_map.get(host_id, [])
        status, status_source = _host_status(target, metrics, alerts)
        if not status:
            continue
        host_status[host_id] = _status_detail(status, status_source, {
            'host_id': host_id,
            'target': target.to_view() if target else None,
            'metrics': metrics,
            'alerts': [item.to_view() for item in alerts[:10]],
            'alert_count': len(alerts),
            'summary_error': summary_error,
        })

    metric_target_status = {}
    for target_id, target in target_map.items():
        detail = host_status.get(target.host_id)
        if detail:
            metric_target_status[target_id] = detail

    return host_status, metric_target_status, summary_error


def _apply_status(view, status, status_source, evidence=None):
    view['status'] = status
    view['status_source'] = status_source
    metadata = dict(view.get('metadata') or {})
    if evidence:
        metadata['observability'] = evidence
    view['metadata'] = metadata
    return view


def _split_runtime_sections(output):
    sections = {
        'processes': [],
        'process_hints': [],
        'config_hints': [],
        'app_hints': [],
        'listeners': [],
        'connections': [],
    }
    current = None
    for line in (output or '').splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(RUNTIME_TOPOLOGY_MARKER):
            current = line.replace(RUNTIME_TOPOLOGY_MARKER, '').strip()
            sections.setdefault(current, [])
            continue
        if current in sections:
            sections[current].append(line[:1000])
    return sections


def _process_status(stat):
    stat = str(stat or '')
    if 'Z' in stat:
        return 'warning'
    if stat:
        return 'healthy'
    return 'unknown'


def _parse_processes(lines):
    processes = {}
    for line in lines:
        parts = line.split(None, 3)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        processes[pid] = {
            'pid': pid,
            'ppid': int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
            'name': _runtime_clean_process_name(parts[2]),
            'stat': parts[3][:20] if len(parts) > 3 else '',
        }
    return processes


def _runtime_clean_process_name(name):
    name = str(name or '').strip()
    if '/' in name:
        name = name.rsplit('/', 1)[-1]
    if name.startswith('./'):
        name = name[2:]
    if ':' in name:
        name = name.split(':', 1)[0]
    return name[:80]


def _parse_process_hints(lines):
    hints = {}
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        hint = parts[1].strip().lower()
        if hint in RUNTIME_FRAMEWORK_PROFILES:
            hints[int(parts[0])] = hint
    return hints


def _runtime_extract_config_value(line):
    line = str(line or '').strip()
    match = re.search(r'[:=]\s*[ruRU]?["\']([^"\']{0,160})["\']', line)
    if match:
        return match.group(1).strip()
    match = re.search(r'[:=]\s*([A-Za-z0-9_.:/-]{1,160})', line)
    if match:
        return match.group(1).strip().strip(',')
    return ''


def _runtime_database_profile_from_service(service):
    profiles = {
        'mysql': ('database', 'MySQL', 3306),
        'postgresql': ('database', 'PostgreSQL', 5432),
        'postgres': ('database', 'PostgreSQL', 5432),
        'oracle': ('database', 'Oracle', 1521),
        'redis': ('middleware', 'Redis', 6379),
    }
    item = profiles.get(str(service or '').strip().lower())
    if not item:
        return None
    node_type, service_name, default_port = item
    return {
        'kind': 'dependency',
        'type': node_type,
        'service': service_name,
        'runtime_layer': node_type,
        'detected_by': 'config',
        'selected': True,
        'default_port': default_port,
    }


def _parse_runtime_config_hints(lines):
    states = {}
    records = []

    def append_state(state):
        profile = _runtime_database_profile_from_service(state.get('service'))
        if not profile:
            return
        port = state.get('port') or profile['default_port']
        address = (state.get('address') or '127.0.0.1').strip()
        records.append({
            'pid': state['pid'],
            'address': address,
            'port': port,
            'protocol': 'tcp',
            'profile': profile,
            'file': state.get('file') or '',
        })

    for line in lines:
        parts = str(line or '').split('\t', 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        match = re.match(r'(.+?):(\d+):(.*)', parts[1])
        if not match:
            continue
        file_path, _, content = match.groups()
        stripped = content.strip()
        if not stripped or stripped.startswith('#'):
            continue
        lower = stripped.lower()
        key = (pid, file_path)
        state = states.setdefault(key, {
            'pid': pid,
            'file': file_path[:255],
            'service': '',
            'address': '',
            'port': None,
        })
        if 'redis://' in lower:
            redis = re.search(r'redis://([^:/\'"]+):(\d+)', stripped)
            if redis:
                profile = _runtime_database_profile_from_service('redis')
                records.append({
                    'pid': pid,
                    'address': redis.group(1),
                    'port': int(redis.group(2)),
                    'protocol': 'tcp',
                    'profile': profile,
                    'file': file_path[:255],
                })
                continue
        elif 'mysql' in lower and (
                'engine' in lower or 'django.db.backends' in lower):
            state['service'] = 'mysql'
        elif 'postgres' in lower and (
                'engine' in lower or 'django.db.backends' in lower):
            state['service'] = 'postgresql'
        elif 'oracle' in lower and (
                'engine' in lower or 'django.db.backends' in lower):
            state['service'] = 'oracle'
        if re.search(r'["\']HOST["\']|(^|_)HOST\s*=', stripped):
            value = _runtime_extract_config_value(stripped)
            if value:
                state['address'] = value
        if re.search(r'["\']PORT["\']|(^|_)PORT\s*=', stripped):
            value = _runtime_extract_config_value(stripped)
            if value and str(value).isdigit():
                state['port'] = int(value)

    hints = []
    seen = set()
    for state in states.values():
        append_state(state)
    for record in records:
        profile = record.get('profile')
        if not profile:
            continue
        port = record.get('port') or profile['default_port']
        address = (record.get('address') or '127.0.0.1').strip()
        key = (record['pid'], profile['service'], address, port)
        if key in seen:
            continue
        seen.add(key)
        hints.append({
            'pid': record['pid'],
            'address': address,
            'port': port,
            'protocol': 'tcp',
            'profile': profile,
            'file': record.get('file') or '',
        })
    return hints


def _parse_runtime_app_hints(lines):
    hints = []
    seen = set()
    for line in lines:
        parts = str(line or '').split('\t', 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        content = parts[1].strip()
        if not content or content.startswith('#'):
            continue
        matches = []
        for pattern in (
                r'(?:proxy_pass|uwsgi_pass|fastcgi_pass|grpc_pass)\s+(?:https?://|uwsgi://|grpc://|fastcgi://)?([^/;\s:]+):(\d+)',
                r'\bserver\s+([^;\s:]+):(\d+)'):
            matches.extend(re.findall(pattern, content, flags=re.I))
        for address, port in matches:
            address = str(address or '').strip().strip('[]')
            if address in ('', '$host', '$server_name') or address.startswith('$'):
                continue
            if address == 'localhost':
                address = '127.0.0.1'
            if not str(port).isdigit():
                continue
            key = (pid, address, int(port))
            if key in seen:
                continue
            seen.add(key)
            hints.append({
                'pid': pid,
                'address': address,
                'port': int(port),
                'protocol': 'tcp',
                'detected_by': 'config',
            })
    return hints


def _extract_process_refs(line):
    refs = []
    for name, pid in re.findall(r'\("([^"]{1,80})",pid=(\d+)', line or ''):
        refs.append({'name': _runtime_clean_process_name(name), 'pid': int(pid)})
    for pid, name in re.findall(r'(?:^|\s)(\d+)/(\S+)', line or ''):
        refs.append({
            'name': _runtime_clean_process_name(name),
            'pid': int(pid),
        })
    return refs


def _parse_endpoint(value):
    value = str(value or '').strip().strip(',')
    if not value or value in ('*', '*:*'):
        return None
    value = value.split('%', 1)[0]
    if value.startswith('[') and ']:' in value:
        host, port = value[1:].split(']:', 1)
    elif ':' in value:
        host, port = value.rsplit(':', 1)
    else:
        return None
    host = host.strip('[]') or '*'
    port = port.strip()
    if not port.isdigit():
        return None
    return {'address': host, 'port': int(port)}


def _parse_socket_line(line):
    parts = line.split()
    if len(parts) < 5:
        return None
    protocol = parts[0].lower()
    if protocol in ('tcp6', 'udp6'):
        protocol = protocol[:-1]
    if protocol not in ('tcp', 'udp'):
        return None
    state = parts[1] if len(parts) > 1 else ''
    local = None
    peer = None
    for part in parts:
        endpoint = _parse_endpoint(part)
        if not endpoint:
            continue
        if local is None:
            local = endpoint
        elif peer is None:
            peer = endpoint
            break
    if not local:
        return None
    return {
        'protocol': protocol,
        'state': state,
        'local': local,
        'peer': peer,
        'processes': _extract_process_refs(line),
    }


def _parse_sockets(lines):
    return [
        item for item in (_parse_socket_line(line) for line in lines)
        if item
    ]


def _runtime_key(*parts):
    key = ':'.join(str(item) for item in parts)
    if len(key) <= 128:
        return key
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]
    return ':'.join(str(item) for item in parts[:3])[:96] + ':' + digest


def _runtime_metadata(host_id, observed_at, **values):
    data = {'host_id': host_id, 'observed_at': observed_at}
    data.update(values)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _runtime_metadata_dict(host_id, observed_at, **values):
    data = {'host_id': host_id, 'observed_at': observed_at}
    data.update(values)
    return data


def _runtime_json(host_id, observed_at, **values):
    return json.dumps(
        _runtime_metadata_dict(host_id, observed_at, **values),
        ensure_ascii=False,
        sort_keys=True,
    )


def _runtime_is_fixed(item):
    try:
        return item.metadata_data.get('monitor_mode') == 'fixed'
    except (TypeError, ValueError):
        return False


def _runtime_service_monitor_key(host_id, protocol, port):
    return 'service:%s:%s:%s' % (host_id, protocol, port)


def _runtime_probe_key(host_id, protocol, address, port):
    return 'probe:%s:%s:%s:%s' % (host_id, protocol, address, port)


def _runtime_connection_monitor_key(host_id, connection, profile):
    target_side = connection.get('business_target') or 'peer'
    target = connection.get(target_side) or {}
    source = 'host:%s' % host_id
    if target_side == 'local':
        peer = connection.get('peer') or {}
        source = 'client:%s' % (peer.get('address') or 'unknown')
    return 'connection:%s:%s:%s:%s:%s:%s' % (
        source,
        target_side,
        connection['protocol'],
        target.get('address'),
        target.get('port'),
        profile['service'],
    )


def _runtime_safe_probe_target(target):
    if not target:
        return False
    if target.get('protocol') != 'tcp':
        return False
    address = str(target.get('address') or '').strip()
    if not address or address in ('*', '0.0.0.0', '::'):
        return False
    try:
        port = int(target.get('port'))
    except (TypeError, ValueError):
        return False
    if port <= 0 or port > 65535:
        return False
    return bool(re.match(r'^[A-Za-z0-9_.:-]+$', address))


def _runtime_probe_targets_command(targets):
    lines = ["printf '%s probes\\n'" % RUNTIME_TOPOLOGY_MARKER]
    for target in targets:
        if not _runtime_safe_probe_target(target):
            continue
        key = shlex.quote(target['key'])
        address = shlex.quote(str(target['address']))
        port = shlex.quote(str(int(target['port'])))
        lines.append(
            "if timeout 3 bash -c '</dev/tcp/%s/%s' >/dev/null 2>&1; "
            "then printf '%%s ok\\n' %s; else printf '%%s fail\\n' %s; fi" % (
                address, port, key, key
            )
        )
    return '\n'.join(lines)


def _parse_runtime_probe_results(output):
    sections = _split_runtime_sections(output)
    results = {}
    for line in sections.get('probes', []):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1] in ('ok', 'fail'):
            results[parts[0]] = 'healthy' if parts[1] == 'ok' else 'offline'
    return results


def _host_ip_map(hosts):
    result = {}
    for host in hosts:
        values = [host.hostname]
        if hasattr(host, 'hostextend'):
            try:
                values.extend(json.loads(host.hostextend.private_ip_address or '[]'))
                values.extend(json.loads(host.hostextend.public_ip_address or '[]'))
            except (TypeError, ValueError):
                pass
        for value in values:
            value = str(value or '').strip()
            if value:
                result[value] = host.id
    return result


def _upsert_node(user, key, node_type, name, status, metadata,
                 source_id=None, description='', source_type='runtime'):
    node = TopologyNode.objects.filter(key=key).first()
    if node is None:
        node = TopologyNode(key=key, created_by=user)
    node.type = node_type
    node.name = name[:100]
    node.description = (description or '')[:255] or None
    node.source_type = source_type
    node.source_id = str(source_id) if source_id is not None else None
    node.status = status
    node.status_source = 'runtime'
    node.metadata = metadata
    node.is_active = True
    node.updated_by = user
    node.updated_at = datetime.now()
    node.full_clean()
    node.save()
    return node


def _upsert_edge(user, source, target, edge_type, label, status, metadata,
                 probed=True):
    edge = TopologyEdge.objects.filter(
        source=source, target=target, type=edge_type
    ).first()
    if edge is None:
        edge = TopologyEdge(source=source, target=target, type=edge_type, created_by=user)
    edge.label = (label or '')[:100] or None
    edge.status = status
    edge.status_source = 'runtime'
    edge.probed = probed
    edge.metadata = metadata
    edge.is_active = True
    edge.updated_by = user
    edge.updated_at = datetime.now()
    edge.full_clean()
    edge.save()
    return edge


def _runtime_dependency_for_endpoint(endpoint, protocol):
    if not endpoint or protocol != 'tcp':
        return None
    service = RUNTIME_DEPENDENCY_PORTS.get(endpoint['port'])
    if not service:
        return None
    node_type, service_name = service
    return {
        'kind': 'dependency',
        'type': node_type,
        'service': service_name,
        'runtime_layer': node_type,
        'detected_by': 'port',
        'selected': True,
    }


def _runtime_process_profile(process):
    process = process or {}
    name = str(process.get('name') or '').strip().lower()
    if name in RUNTIME_PROCESS_PROFILES:
        profile = RUNTIME_PROCESS_PROFILES[name]
        return dict(profile, detected_by='process', selected=True)
    hint = str(process.get('framework') or '').strip().lower()
    if hint in RUNTIME_FRAMEWORK_PROFILES:
        profile = RUNTIME_FRAMEWORK_PROFILES[hint]
        return {
            'kind': 'application',
            'type': 'port',
            'service': profile['service'],
            'runtime_layer': profile['runtime_layer'],
            'detected_by': 'framework',
            'selected': True,
        }
    layer = _runtime_process_layer(process.get('name'))
    if not layer:
        return None
    kind = 'dependency' if layer in ('database', 'middleware') else 'application'
    node_type = layer if kind == 'dependency' else 'port'
    return {
        'kind': kind,
        'type': node_type,
        'service': _runtime_service_label(
            'Application', layer, process.get('name')
        ),
        'runtime_layer': layer,
        'detected_by': 'process',
        'selected': True,
    }


def _runtime_process_layer(name):
    name = str(name or '').strip().lower()
    if not name:
        return None
    if name in RUNTIME_DATABASE_PROCESSES:
        return 'database'
    if name in RUNTIME_MIDDLEWARE_PROCESSES:
        return 'middleware'
    if name in RUNTIME_FRONTEND_PROCESSES:
        return 'frontend'
    if name in RUNTIME_BACKEND_PROCESSES:
        return 'backend'
    if name.endswith('.jar'):
        return 'backend'
    return None


def _runtime_is_system_process(name):
    name = str(name or '').strip().lower()
    if not name:
        return False
    if name in RUNTIME_SYSTEM_PROCESSES:
        return True
    return name.endswith('_exporter') or name.startswith('containerd-shim')


def _runtime_endpoint_host_id(endpoint, current_host_id, known_host_ips):
    if not endpoint:
        return None
    address = str(endpoint.get('address') or '').strip()
    if address in RUNTIME_LOCAL_ADDRESSES:
        return current_host_id
    return known_host_ips.get(address)


def _runtime_endpoint_is_current_host(endpoint, current_host_id, known_host_ips):
    return _runtime_endpoint_host_id(
        endpoint, current_host_id, known_host_ips
    ) == current_host_id


def _runtime_endpoint_is_known_remote(endpoint, current_host_id, known_host_ips):
    host_id = _runtime_endpoint_host_id(endpoint, current_host_id, known_host_ips)
    return bool(host_id and host_id != current_host_id)


def _runtime_endpoint_is_private_remote(endpoint, current_host_id, known_host_ips):
    if _runtime_endpoint_is_current_host(endpoint, current_host_id, known_host_ips):
        return False
    address = str((endpoint or {}).get('address') or '').strip()
    try:
        return ipaddress.ip_address(address).is_private
    except ValueError:
        return False


def _runtime_endpoint_is_dependency_client(endpoint, current_host_id, known_host_ips):
    return (
        _runtime_endpoint_is_known_remote(endpoint, current_host_id, known_host_ips) or
        _runtime_endpoint_is_private_remote(endpoint, current_host_id, known_host_ips)
    )


def _runtime_is_backend_port(port):
    if port in RUNTIME_BACKEND_PORTS:
        return True
    return any(start <= port <= end for start, end in RUNTIME_BACKEND_PORT_RANGES)


def _runtime_service_label(service, layer=None, process_name=None):
    process_name = str(process_name or '').strip()
    if process_name and service == 'Application':
        if layer:
            return '%s %s' % (process_name, layer)
        return process_name
    return service


def _runtime_listener_name(profile, protocol, port):
    layer = profile.get('runtime_layer')
    service = _runtime_service_label(profile['service'], layer)
    return '%s %s/%s' % (service, port, protocol)


def _runtime_edge_label(profile, protocol, endpoint):
    layer = profile.get('runtime_layer')
    service = _runtime_service_label(profile['service'], layer)
    return '%s %s %s:%s' % (
        service, protocol.upper(), endpoint['address'], endpoint['port']
    )


def _runtime_profile_from_node(node, fallback):
    if not node:
        return fallback
    try:
        metadata = node.metadata_data
    except (TypeError, ValueError):
        metadata = {}
    service = metadata.get('service') or fallback.get('service')
    return {
        'kind': metadata.get('connection_kind') or fallback.get('kind'),
        'type': node.type or fallback.get('type'),
        'service': service,
        'runtime_layer': metadata.get('runtime_layer') or fallback.get('runtime_layer'),
        'detected_by': metadata.get('detected_by') or fallback.get('detected_by'),
    }


def _runtime_application_for_endpoint(endpoint, protocol, current_host_id,
                                      known_host_ips, allow_ranges=True):
    if not endpoint or protocol != 'tcp':
        return None
    port = endpoint['port']
    if port in RUNTIME_DEPENDENCY_PORTS or port in RUNTIME_APP_PORT_EXCLUDES:
        return None
    remote_host_id = _runtime_endpoint_host_id(
        endpoint, current_host_id, known_host_ips
    )
    if not remote_host_id:
        return None
    if port in RUNTIME_FRONTEND_PORTS:
        return {
            'kind': 'application',
            'type': 'port',
            'service': 'Frontend',
            'runtime_layer': 'frontend',
            'detected_by': 'port',
            'selected': True,
        }
    if port in RUNTIME_BACKEND_PORTS or (
            allow_ranges and _runtime_is_backend_port(port)):
        return {
            'kind': 'application',
            'type': 'port',
            'service': 'Backend API',
            'runtime_layer': 'backend',
            'detected_by': 'port',
            'selected': True,
        }
    return None


def _runtime_listener_profile(listener, processes, current_host_id,
                              known_host_ips):
    local = listener.get('local')
    dependency = _runtime_dependency_for_endpoint(local, listener['protocol'])
    if dependency:
        return dependency
    for process_ref in listener.get('processes') or []:
        process = processes.get(process_ref['pid'], process_ref)
        process_profile = _runtime_process_profile(process)
        if process_profile:
            return {
                'kind': process_profile.get('kind') or 'application',
                'type': process_profile.get('type') or 'port',
                'service': process_profile['service'],
                'runtime_layer': process_profile['runtime_layer'],
                'detected_by': process_profile.get('detected_by'),
                'selected': process_profile.get('selected', True),
            }
    application = _runtime_application_for_endpoint(
        local, listener['protocol'], current_host_id, known_host_ips,
        allow_ranges=False
    )
    if application:
        return application
    return {
        'kind': 'application',
        'type': 'port',
        'service': 'TCP Service',
        'runtime_layer': 'unknown',
        'detected_by': 'listener',
        'selected': False,
    }


def _runtime_source_is_business(connection, processes):
    refs = connection.get('processes') or []
    if not refs:
        return False
    for process_ref in refs:
        process = processes.get(process_ref['pid'], process_ref)
        name = process.get('name')
        if _runtime_process_profile(process) or not _runtime_is_system_process(name):
            return True
    return False


def _runtime_existing_port_node(endpoint, protocol, current_host_id, port_nodes,
                                known_host_ips):
    remote_host_id = _runtime_endpoint_host_id(
        endpoint, current_host_id, known_host_ips
    )
    if not remote_host_id:
        return None
    key = (remote_host_id, protocol, endpoint['port'])
    node = port_nodes.get(key)
    if node:
        return node
    return TopologyNode.objects.filter(
        key=_runtime_key(
            'runtime', 'host', remote_host_id, 'port',
            protocol, endpoint['port'],
        ),
        is_active=True,
    ).first()


def _runtime_listener_profile_for_endpoint(endpoint, protocol, current_host_id,
                                           known_host_ips, listener_profiles):
    if not endpoint or not listener_profiles:
        return None
    host_id = _runtime_endpoint_host_id(endpoint, current_host_id, known_host_ips)
    if not host_id:
        return None
    return listener_profiles.get((host_id, protocol, endpoint['port']))


def _runtime_profile_selected(profile):
    return bool(profile and profile.get('selected', True))


def _runtime_process_listener_endpoint(pid, listeners):
    for listener in listeners:
        for process_ref in listener.get('processes') or []:
            if process_ref.get('pid') == pid:
                return listener.get('local')
    return None


def _runtime_config_connections(config_hints, processes, listeners):
    connections = []
    seen = set()
    sorted_hints = sorted(
        config_hints,
        key=lambda hint: 0 if _runtime_process_listener_endpoint(
            hint.get('pid'), listeners
        ) else 1,
    )
    for hint in sorted_hints:
        pid = hint.get('pid')
        if pid not in processes:
            continue
        profile = hint.get('profile') or {}
        local = _runtime_process_listener_endpoint(pid, listeners) or {
            'address': '127.0.0.1',
            'port': 0,
        }
        peer = {
            'address': hint.get('address') or '127.0.0.1',
            'port': int(hint.get('port') or 0),
        }
        if not peer['port']:
            continue
        key = (
            pid, profile.get('service'), peer['address'], peer['port'],
        )
        if key in seen:
            continue
        seen.add(key)
        connections.append(({
            'protocol': hint.get('protocol') or 'tcp',
            'state': 'CONFIGURED',
            'local': local,
            'peer': peer,
            'processes': [{
                'pid': pid,
                'name': processes[pid].get('name') or 'process',
            }],
            'business_target': 'peer',
            'detected_by': 'config',
            'config_file': hint.get('file') or '',
        }, profile))
    return connections


def _runtime_app_connections(app_hints, processes, current_host_id,
                             known_host_ips, listener_profiles):
    connections = []
    seen = set()
    for hint in app_hints:
        pid = hint.get('pid')
        if pid not in processes:
            continue
        peer = {
            'address': hint.get('address') or '127.0.0.1',
            'port': int(hint.get('port') or 0),
        }
        if not peer['port']:
            continue
        profile = _runtime_listener_profile_for_endpoint(
            peer, hint.get('protocol') or 'tcp', current_host_id,
            known_host_ips, listener_profiles
        ) or _runtime_application_for_endpoint(
            peer, hint.get('protocol') or 'tcp', current_host_id,
            known_host_ips
        )
        if not _runtime_profile_selected(profile):
            continue
        key = (pid, peer['address'], peer['port'], profile.get('service'))
        if key in seen:
            continue
        seen.add(key)
        connections.append(({
            'protocol': hint.get('protocol') or 'tcp',
            'state': 'CONFIGURED',
            'local': {'address': '127.0.0.1', 'port': 0},
            'peer': peer,
            'processes': [{
                'pid': pid,
                'name': processes[pid].get('name') or 'process',
            }],
            'business_target': 'peer',
            'detected_by': hint.get('detected_by') or 'config',
        }, profile))
    return connections


def _runtime_business_connection(connection, processes, current_host_id,
                                 known_host_ips, relevant_listener_keys=None,
                                 listener_profiles=None):
    peer = connection.get('peer')
    dependency = _runtime_dependency_for_endpoint(peer, connection['protocol'])
    if dependency:
        connection['business_target'] = 'peer'
        return dependency
    if not _runtime_source_is_business(connection, processes):
        local_dependency = _runtime_dependency_for_endpoint(
            connection.get('local'), connection['protocol']
        )
        if local_dependency and peer and _runtime_endpoint_is_dependency_client(
                peer, current_host_id, known_host_ips):
            connection['business_target'] = 'local'
            return local_dependency
        return None
    listener_profile = _runtime_listener_profile_for_endpoint(
        peer, connection['protocol'], current_host_id, known_host_ips,
        listener_profiles
    )
    if _runtime_profile_selected(listener_profile):
        connection['business_target'] = 'peer'
        return listener_profile
    application = _runtime_application_for_endpoint(
        peer, connection['protocol'], current_host_id, known_host_ips
    )
    if application:
        connection['business_target'] = 'peer'
        return application
    local = connection.get('local')
    local_dependency = _runtime_dependency_for_endpoint(local, connection['protocol'])
    if local_dependency and peer and _runtime_endpoint_is_dependency_client(
            peer, current_host_id, known_host_ips):
        connection['business_target'] = 'local'
        return local_dependency
    local_listener_profile = _runtime_listener_profile_for_endpoint(
        local, connection['protocol'], current_host_id, known_host_ips,
        listener_profiles
    )
    if _runtime_profile_selected(local_listener_profile) and peer and (
            _runtime_endpoint_is_known_remote(peer, current_host_id, known_host_ips) or
            (
                local_listener_profile.get('kind') == 'dependency' and
                _runtime_endpoint_is_dependency_client(peer, current_host_id, known_host_ips)
            )):
        connection['business_target'] = 'local'
        return local_listener_profile
    existing_local_host_id = _runtime_endpoint_host_id(
        local, current_host_id, known_host_ips
    )
    local_listener_key = (
        existing_local_host_id, connection['protocol'], local['port']
    ) if existing_local_host_id and local else None
    if relevant_listener_keys and local_listener_key in relevant_listener_keys and peer and (
            _runtime_endpoint_is_known_remote(peer, current_host_id, known_host_ips) or
            (
                listener_profiles and
                (listener_profiles.get(local_listener_key) or {}).get('kind') == 'dependency' and
                _runtime_endpoint_is_dependency_client(peer, current_host_id, known_host_ips)
            )):
        if listener_profiles and not _runtime_profile_selected(
                listener_profiles.get(local_listener_key)):
            return None
        connection['business_target'] = 'local'
        return listener_profiles.get(local_listener_key) if listener_profiles and local_listener_key in listener_profiles else {
            'kind': 'application',
            'type': 'port',
            'service': 'Application',
            'runtime_layer': 'backend',
        }
    if peer and peer['port'] in RUNTIME_APP_PORT_EXCLUDES:
        return None
    existing_host_id = _runtime_endpoint_host_id(
        peer, current_host_id, known_host_ips
    )
    listener_key = (
        existing_host_id, connection['protocol'], peer['port']
    ) if existing_host_id else None
    if relevant_listener_keys and listener_key in relevant_listener_keys:
        if listener_profiles and not _runtime_profile_selected(
                listener_profiles.get(listener_key)):
            return None
        connection['business_target'] = 'peer'
        return listener_profiles.get(listener_key) if listener_profiles and listener_key in listener_profiles else {
            'kind': 'application',
            'type': 'port',
            'service': 'Application',
            'runtime_layer': 'backend',
        }
    if _runtime_existing_port_node(
            peer, connection['protocol'], current_host_id, {},
            known_host_ips):
        connection['business_target'] = 'peer'
        return {
            'kind': 'application',
            'type': 'port',
            'service': 'Application',
            'runtime_layer': 'backend',
        }
    return None


def _runtime_client_for_endpoint(user, host_id, endpoint, protocol, observed_at,
                                 known_host_ips):
    if not endpoint:
        return None
    remote_host_id = _runtime_endpoint_host_id(endpoint, host_id, known_host_ips)
    if remote_host_id and remote_host_id != host_id:
        host = Host.objects.filter(pk=remote_host_id).first()
        if host:
            return _upsert_node(
                user,
                'host:%s' % host.id,
                'host',
                host.name,
                'healthy',
                _runtime_metadata(
                    host.id,
                    observed_at,
                    role='host',
                    hostname=host.hostname,
                ),
                source_id=host.id,
                description=host.hostname,
                source_type='host',
            )
    address = str(endpoint.get('address') or '').strip() or 'unknown'
    digest = hashlib.sha256(
        ('%s:%s:%s' % (host_id, protocol, address)).encode('utf-8')
    ).hexdigest()[:16]
    return _upsert_node(
        user,
        _runtime_key('runtime', 'host', host_id, 'client', digest),
        'external',
        'Client %s' % address,
        'healthy',
        _runtime_metadata(
            host_id,
            observed_at,
            role='client_endpoint',
            monitor_mode='fixed',
            monitor_role='client_endpoint',
            monitor_source='discovery',
            protocol=protocol,
            address=address,
            remote_host_id=remote_host_id,
        ),
        source_id=host_id,
        description='Runtime client endpoint',
    )


def _runtime_target_for_endpoint(user, host_id, endpoint, protocol, observed_at,
                                 port_nodes, known_host_ips, profile):
    if not profile:
        return None
    existing_port = _runtime_existing_port_node(
        endpoint, protocol, host_id, port_nodes, known_host_ips
    )
    if existing_port:
        return existing_port
    remote_host_id = _runtime_endpoint_host_id(endpoint, host_id, known_host_ips)
    endpoint_key = '%s:%s:%s:%s' % (
        host_id, protocol, endpoint['address'], endpoint['port']
    )
    digest = hashlib.sha256(endpoint_key.encode('utf-8')).hexdigest()[:16]
    key = _runtime_key(
        'runtime', 'host', host_id, profile['kind'], 'endpoint', digest
    )
    name = '%s %s:%s' % (
        profile['service'], endpoint['address'], endpoint['port']
    )
    return _upsert_node(
        user,
        key,
        profile['type'],
        name,
        'healthy',
        _runtime_metadata(
            host_id,
            observed_at,
            role='%s_endpoint' % profile['kind'],
            monitor_mode='fixed',
            monitor_role='dependency',
            monitor_source='discovery',
            connection_kind=profile['kind'],
            dependency_service=profile['service']
            if profile['kind'] == 'dependency' else '',
            runtime_layer=profile.get('runtime_layer'),
            service=profile['service'],
            protocol=protocol,
            address=endpoint['address'],
            port=endpoint['port'],
            remote_host_id=remote_host_id,
        ),
        source_id=host_id,
        description='运行态连接对端',
    )


def _runtime_scan_recommendations(host_id, processes, listeners, listener_profiles,
                                  business_connections):
    service_map = {}
    for listener in listeners:
        local = listener['local']
        key = _runtime_service_monitor_key(
            host_id, listener['protocol'], local['port']
        )
        profile = listener_profiles.get((host_id, listener['protocol'], local['port']))
        if not profile:
            continue
        if key in service_map:
            service_map[key]['addresses'].append(local['address'])
            continue
        service_map[key] = {
            'key': key,
            'name': _runtime_listener_name(
                profile, listener['protocol'], local['port']
            ),
            'protocol': listener['protocol'],
            'address': local['address'],
            'addresses': [local['address']],
            'port': local['port'],
            'runtime_layer': profile.get('runtime_layer'),
            'connection_kind': profile['kind'],
            'detected_by': profile.get('detected_by'),
            'processes': [
                '%s[%s]' % (
                    processes.get(item['pid'], item).get('name'),
                    item['pid'],
                )
                for item in listener['processes'][:5]
            ],
            'selected': profile.get('selected', True),
        }

    connection_map = {}
    for connection, profile in business_connections:
        target_endpoint = connection.get(connection.get('business_target') or 'peer') or {}
        source = ', '.join([
            '%s[%s]' % (
                processes.get(item['pid'], item).get('name'),
                item['pid'],
            )
            for item in connection['processes'][:5]
        ]) or 'host'
        if connection.get('business_target') == 'local':
            peer = connection.get('peer') or {}
            source = 'client %s' % (peer.get('address') or 'unknown')
        target = '%s:%s' % (
            target_endpoint.get('address'), target_endpoint.get('port')
        )
        key = _runtime_connection_monitor_key(host_id, connection, profile)
        if key not in connection_map:
            connection_map[key] = {
                'key': key,
                'source': source,
                'target': target,
                'target_side': connection.get('business_target') or 'peer',
                'target_address': target_endpoint.get('address'),
                'target_port': target_endpoint.get('port'),
                'protocol': connection['protocol'],
                'runtime_layer': profile.get('runtime_layer'),
                'connection_kind': profile['kind'],
                'service': profile['service'],
                'detected_by': connection.get('detected_by') or profile.get('detected_by'),
                'count': 0,
                'selected': True,
            }
        connection_map[key]['count'] += 1
    return {
        'services': list(service_map.values()),
        'connections': list(connection_map.values()),
    }


def _runtime_selected_keys(values):
    if values in (None, ''):
        return None
    if not isinstance(values, (list, tuple, set)):
        raise ValueError('selected keys must be a list')
    result = set()
    for value in values:
        value = str(value or '').strip()
        if value:
            result.add(value[:160])
    return result


def _runtime_connection_probe_target(host_id, connection, profile, monitor_key):
    target_side = connection.get('business_target') or 'peer'
    endpoint = connection.get(target_side) or {}
    if not endpoint:
        return None
    return {
        'key': monitor_key,
        'host_id': host_id,
        'protocol': connection['protocol'],
        'address': endpoint.get('address'),
        'port': endpoint.get('port'),
        'service': profile.get('service'),
    }


def _runtime_fixed_probe_targets(host_id):
    targets = []
    edges = TopologyEdge.objects.select_related('source', 'target').filter(
        type='calls',
        is_active=True,
    )
    for edge in edges:
        if not _runtime_is_fixed(edge):
            continue
        metadata = edge.metadata_data
        if _source_id(metadata.get('probe_host_id')) != host_id:
            continue
        monitor_key = metadata.get('monitor_key')
        if not monitor_key:
            continue
        targets.append({
            'key': monitor_key,
            'host_id': host_id,
            'protocol': metadata.get('protocol') or 'tcp',
            'address': metadata.get('probe_address'),
            'port': metadata.get('probe_port'),
            'service': metadata.get('service'),
        })
    return targets


def _runtime_probe_targets(host, targets):
    unique = {}
    for target in targets:
        if _runtime_safe_probe_target(target):
            unique[target['key']] = target
    if not unique:
        return {}
    command = _runtime_probe_targets_command(list(unique.values()))
    with host.get_ssh() as ssh:
        exit_code, output = ssh.exec_command_raw(command)
    if exit_code != 0:
        return {}
    return _parse_runtime_probe_results(output)


def _runtime_update_fixed_services(user, host_id, observed_at,
                                   observed_service_keys):
    nodes = TopologyNode.objects.filter(
        source_type='runtime',
        source_id=str(host_id),
        is_active=True,
    )
    for node in nodes:
        if not _runtime_is_fixed(node):
            continue
        metadata = node.metadata_data
        if metadata.get('monitor_role') != 'service':
            continue
        monitor_key = metadata.get('monitor_key')
        status = 'healthy' if monitor_key in observed_service_keys else 'offline'
        metadata.update({
            'last_checked_at': observed_at,
            'last_result': 'listening' if status == 'healthy' else 'not_listening',
        })
        node.status = status
        node.status_source = 'runtime'
        node.metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        node.updated_by = user
        node.updated_at = datetime.now()
        node.save()


def _runtime_update_fixed_calls(user, host_id, observed_at,
                                observed_connection_keys, probe_results):
    edges = TopologyEdge.objects.filter(type='calls', is_active=True)
    for edge in edges:
        if not _runtime_is_fixed(edge):
            continue
        metadata = edge.metadata_data
        if _source_id(metadata.get('probe_host_id')) != host_id:
            continue
        monitor_key = metadata.get('monitor_key')
        if monitor_key in observed_connection_keys:
            status = 'healthy'
            result = 'connected'
        elif monitor_key in probe_results:
            status = probe_results[monitor_key]
            result = 'probe_ok' if status == 'healthy' else 'probe_failed'
        else:
            status = 'unknown'
            result = 'not_checked'
        metadata.update({
            'last_checked_at': observed_at,
            'last_result': result,
        })
        edge.status = status
        edge.status_source = 'probe' if monitor_key in probe_results else 'runtime'
        edge.probed = monitor_key in probe_results
        edge.metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        edge.updated_by = user
        edge.updated_at = datetime.now()
        edge.save()


def sync_runtime_topology(user, host_ids, dry_run=False,
                          selected_service_keys=None,
                          selected_connection_keys=None):
    selected_service_keys = _runtime_selected_keys(selected_service_keys)
    selected_connection_keys = _runtime_selected_keys(selected_connection_keys)
    try:
        host_ids = sorted({int(item) for item in (host_ids or [])})
    except (TypeError, ValueError):
        raise ValueError('主机范围格式错误')
    if not host_ids:
        raise ValueError('请选择需要扫描的主机')
    if len(host_ids) > 10:
        raise ValueError('单次最多扫描 10 台主机')
    if not user.is_supper and not has_host_perm(user, host_ids, action='host.view'):
        raise ValueError('无权查看选中的主机')
    if not user.is_supper and not has_host_perm(user, host_ids, action='ssh.connect'):
        raise ValueError('扫描运行态需要目标主机 SSH 连接权限')

    hosts = list(Host.objects.filter(pk__in=host_ids).select_related('hostextend'))
    if len(hosts) != len(host_ids):
        raise ValueError('部分主机不存在')
    known_host_ips = _host_ip_map(Host.objects.select_related('hostextend').all())
    observed_at = datetime.now().isoformat()
    summaries = []

    for host in hosts:
        summary = {
            'host_id': host.id,
            'host_name': host.name,
            'status': 'scanning',
            'process_count': 0,
            'listener_count': 0,
            'connection_count': 0,
            'dependency_connection_count': 0,
            'business_connection_count': 0,
            'node_count': 0,
            'edge_count': 0,
            'error': '',
        }
        try:
            with host.get_ssh() as ssh:
                exit_code, output = ssh.exec_command_raw(RUNTIME_TOPOLOGY_COMMAND)
            if exit_code != 0:
                raise ValueError('运行态扫描命令执行失败')
            sections = _split_runtime_sections(output)
            processes = _parse_processes(sections['processes'])
            process_hints = _parse_process_hints(sections['process_hints'])
            for pid, hint in process_hints.items():
                if pid in processes:
                    processes[pid]['framework'] = hint
            config_hints = _parse_runtime_config_hints(
                sections['config_hints']
            )
            app_hints = _parse_runtime_app_hints(sections['app_hints'])
            listeners = _parse_sockets(sections['listeners'])
            connections = _parse_sockets(sections['connections'])
            for socket in listeners + connections:
                for process_ref in socket['processes']:
                    processes.setdefault(process_ref['pid'], {
                        'pid': process_ref['pid'],
                        'ppid': None,
                        'name': process_ref['name'],
                        'stat': '',
                        'framework': process_hints.get(process_ref['pid'], ''),
                    })
            listener_profiles = {}
            relevant_listener_keys = set()
            business_pids = set()
            for listener in listeners:
                local = listener['local']
                profile = _runtime_listener_profile(
                    listener, processes, host.id, known_host_ips
                )
                if not profile:
                    continue
                listener_key = (host.id, listener['protocol'], local['port'])
                listener_profiles[listener_key] = profile
                relevant_listener_keys.add(listener_key)
                for process_ref in listener['processes']:
                    business_pids.add(process_ref['pid'])

            business_connections = []
            dependency_connections = []
            connection_keys = set()
            for connection in connections:
                profile = _runtime_business_connection(
                    connection, processes, host.id, known_host_ips,
                    relevant_listener_keys, listener_profiles
                )
                if not profile:
                    continue
                connection_key = _runtime_connection_monitor_key(
                    host.id, connection, profile
                )
                connection_keys.add(connection_key)
                business_connections.append((connection, profile))
                if profile['kind'] == 'dependency':
                    dependency_connections.append(connection)
                for process_ref in connection['processes']:
                    business_pids.add(process_ref['pid'])
            for connection, profile in _runtime_app_connections(
                    app_hints, processes, host.id, known_host_ips,
                    listener_profiles):
                connection_key = _runtime_connection_monitor_key(
                    host.id, connection, profile
                )
                if connection_key in connection_keys:
                    continue
                connection_keys.add(connection_key)
                business_connections.append((connection, profile))
                if profile['kind'] == 'dependency':
                    dependency_connections.append(connection)
                for process_ref in connection['processes']:
                    business_pids.add(process_ref['pid'])
            for connection, profile in _runtime_config_connections(
                    config_hints, processes, listeners):
                connection_key = _runtime_connection_monitor_key(
                    host.id, connection, profile
                )
                if connection_key in connection_keys:
                    continue
                connection_keys.add(connection_key)
                business_connections.append((connection, profile))
                if profile['kind'] == 'dependency':
                    dependency_connections.append(connection)
                for process_ref in connection['processes']:
                    business_pids.add(process_ref['pid'])
            observed_service_keys = {
                _runtime_service_monitor_key(
                    host.id, key[1], key[2]
                )
                for key in listener_profiles
                if key[0] == host.id
            }
            observed_connection_keys = {
                _runtime_connection_monitor_key(host.id, connection, profile)
                for connection, profile in business_connections
                if connection.get('state') != 'CONFIGURED'
            }
            selected_listener_profiles = {}
            if selected_service_keys is not None:
                selected_listener_profiles = {
                    key: profile for key, profile in listener_profiles.items()
                    if _runtime_service_monitor_key(host.id, key[1], key[2])
                    in selected_service_keys
                }
            selected_business_connections = [
                (connection, profile)
                for connection, profile in business_connections
                if selected_connection_keys is not None and
                _runtime_connection_monitor_key(host.id, connection, profile)
                in selected_connection_keys
            ]
            business_pids = set()
            for listener in listeners:
                local = listener['local']
                if (host.id, listener['protocol'], local['port']) not in selected_listener_profiles:
                    continue
                for process_ref in listener['processes']:
                    business_pids.add(process_ref['pid'])
            for connection, profile in selected_business_connections:
                for process_ref in connection['processes']:
                    business_pids.add(process_ref['pid'])
            probe_targets = _runtime_fixed_probe_targets(host.id)
            for connection, profile in selected_business_connections:
                monitor_key = _runtime_connection_monitor_key(
                    host.id, connection, profile
                )
                target = _runtime_connection_probe_target(
                    host.id, connection, profile, monitor_key
                )
                if target:
                    probe_targets.append(target)
            probe_results = {} if dry_run else _runtime_probe_targets(
                host, probe_targets
            )
            if dry_run:
                target_keys = {
                    '%s:%s:%s' % (
                        item[0]['protocol'],
                        item[0].get(
                            item[0].get('business_target') or 'peer'
                        )['address'],
                        item[0].get(
                            item[0].get('business_target') or 'peer'
                        )['port'],
                    )
                    for item in business_connections
                    if item[0].get(item[0].get('business_target') or 'peer')
                }
                summary.update({
                    'status': 'succeeded',
                    'process_count': len(processes),
                    'listener_count': len(listeners),
                    'connection_count': len(connections),
                    'dependency_connection_count': len(dependency_connections),
                    'business_connection_count': len(business_connections),
                    'node_count': (
                        len(business_pids) + len(listener_profiles) +
                        len(target_keys)
                    ),
                    'edge_count': len(listener_profiles) + len(business_connections),
                    'recommendations': _runtime_scan_recommendations(
                        host.id, processes, listeners, listener_profiles,
                        business_connections
                    ),
                })
                summaries.append(summary)
                continue
            with transaction.atomic():
                runtime_nodes = TopologyNode.objects.filter(
                    source_type='runtime',
                    source_id=str(host.id),
                )
                runtime_node_ids = list(runtime_nodes.values_list('id', flat=True))
                for node in runtime_nodes:
                    if _runtime_is_fixed(node):
                        continue
                    node.is_active = False
                    node.updated_at = datetime.now()
                    node.save(update_fields=['is_active', 'updated_at'])
                runtime_edges = TopologyEdge.objects.filter(
                    source_id__in=runtime_node_ids
                ) | TopologyEdge.objects.filter(
                    target_id__in=runtime_node_ids
                )
                for edge in runtime_edges.distinct():
                    if _runtime_is_fixed(edge):
                        continue
                    edge.is_active = False
                    edge.updated_at = datetime.now()
                    edge.save(update_fields=['is_active', 'updated_at'])

                host_node = _upsert_node(
                    user,
                    'host:%s' % host.id,
                    'host',
                    host.name,
                    'healthy',
                    _runtime_metadata(
                        host.id,
                        observed_at,
                        role='host',
                        hostname=host.hostname,
                    ),
                    source_id=host.id,
                    description=host.hostname,
                    source_type='host',
                )
                process_nodes = {}
                for process in processes.values():
                    if process['pid'] not in business_pids:
                        continue
                    process_profile = _runtime_process_profile(process)
                    runtime_layer = (
                        process_profile['runtime_layer']
                        if process_profile else _runtime_process_layer(process['name'])
                    )
                    node = _upsert_node(
                        user,
                        _runtime_key('runtime', 'host', host.id, 'process', process['pid']),
                        'process',
                        '%s[%s]' % (process['name'], process['pid']),
                        _process_status(process.get('stat')),
                        _runtime_metadata(
                            host.id,
                            observed_at,
                            role='process',
                            pid=process['pid'],
                            ppid=process.get('ppid'),
                            process_name=process['name'],
                            framework=process.get('framework') or '',
                            service=process_profile['service']
                            if process_profile else '',
                            stat=process.get('stat'),
                            runtime_layer=runtime_layer or 'application',
                        ),
                        source_id=host.id,
                        description='运行中进程',
                    )
                    process_nodes[process['pid']] = node
                    _upsert_edge(
                        user,
                        node,
                        host_node,
                        'deployed_on',
                        '运行于',
                        node.status,
                        _runtime_metadata(host.id, observed_at, role='process_host'),
                    )

                port_nodes = {}
                for listener in listeners:
                    local = listener['local']
                    port_key = (host.id, listener['protocol'], local['port'])
                    profile = selected_listener_profiles.get(port_key)
                    if not profile:
                        continue
                    port_node = port_nodes.get(port_key)
                    if not port_node:
                        port_node = _upsert_node(
                            user,
                            _runtime_key(
                                'runtime', 'host', host.id, 'port',
                                listener['protocol'], local['port'],
                            ),
                            'port',
                            _runtime_listener_name(
                                profile, listener['protocol'], local['port']
                            ),
                            'healthy',
                            _runtime_metadata(
                                host.id,
                                observed_at,
                                role='listener',
                                monitor_mode='fixed',
                                monitor_role='service',
                                monitor_source='discovery',
                                monitor_key=_runtime_service_monitor_key(
                                    host.id, listener['protocol'], local['port']
                                ),
                                last_checked_at=observed_at,
                                last_result='listening',
                                protocol=listener['protocol'],
                                address=local['address'],
                                port=local['port'],
                                state=listener['state'],
                                connection_kind=profile['kind'],
                                runtime_layer=profile.get('runtime_layer'),
                                service=profile['service'],
                                detected_by=profile.get('detected_by'),
                            ),
                            source_id=host.id,
                            description='监听地址 %s:%s' % (local['address'], local['port']),
                        )
                        port_nodes[port_key] = port_node
                    for process_ref in listener['processes']:
                        process_node = process_nodes.get(process_ref['pid'])
                        if process_node:
                            _upsert_edge(
                                user,
                                process_node,
                                port_node,
                                'listens_on',
                                '%s/%s' % (local['port'], listener['protocol']),
                                'healthy',
                                _runtime_metadata(
                                    host.id,
                                    observed_at,
                                    role='listener_edge',
                                    monitor_mode='fixed',
                                    monitor_role='service_link',
                                    monitor_source='discovery',
                                    pid=process_ref['pid'],
                                    runtime_layer=profile.get('runtime_layer'),
                                    protocol=listener['protocol'],
                                    port=local['port'],
                                ),
                            )
                    if not listener['processes']:
                        _upsert_edge(
                            user,
                            host_node,
                            port_node,
                            'listens_on',
                            '%s/%s' % (local['port'], listener['protocol']),
                            'healthy',
                            _runtime_metadata(
                                host.id,
                                observed_at,
                                role='listener_edge',
                                monitor_mode='fixed',
                                monitor_role='service_link',
                                monitor_source='discovery',
                                protocol=listener['protocol'],
                                port=local['port'],
                                runtime_layer=profile.get('runtime_layer'),
                                process_visible=False,
                            ),
                        )

                for connection, profile in selected_business_connections:
                    monitor_key = _runtime_connection_monitor_key(
                        host.id, connection, profile
                    )
                    target_side = connection.get('business_target') or 'peer'
                    target_endpoint = connection.get(target_side)
                    if target_side == 'local':
                        peer = connection.get('peer')
                        source_nodes = [
                            _runtime_client_for_endpoint(
                                user, host.id, peer, connection['protocol'],
                                observed_at, known_host_ips
                            )
                        ]
                    else:
                        pids = [item['pid'] for item in connection['processes']]
                        source_nodes = [
                            process_nodes[pid] for pid in pids
                            if pid in process_nodes
                        ]
                        if not source_nodes:
                            local = connection.get('local') or {}
                            source_nodes = [
                                port_nodes.get((
                                    host.id,
                                    connection['protocol'],
                                    local.get('port'),
                                )) or host_node
                            ]
                    source_nodes = [item for item in source_nodes if item]
                    if not source_nodes or not target_endpoint:
                        continue
                    target = _runtime_target_for_endpoint(
                        user,
                        host.id,
                        target_endpoint,
                        connection['protocol'],
                        observed_at,
                        port_nodes,
                        known_host_ips,
                        profile,
                    )
                    if not target:
                        continue
                    if target.source_type == 'runtime' and not _runtime_is_fixed(target):
                        target_metadata = target.metadata_data
                        target_metadata.update({
                            'monitor_mode': 'fixed',
                            'monitor_role': 'dependency',
                            'monitor_source': 'discovery',
                            'last_checked_at': observed_at,
                        })
                        target.metadata = json.dumps(
                            target_metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        target.updated_by = user
                        target.updated_at = datetime.now()
                        target.save()
                    display_profile = _runtime_profile_from_node(target, profile)
                    edge_status = 'healthy' if monitor_key in observed_connection_keys else (
                        probe_results.get(monitor_key) or 'unknown'
                    )
                    probe_target = _runtime_connection_probe_target(
                        host.id, connection, display_profile, monitor_key
                    ) or {}
                    for source in source_nodes:
                        _upsert_edge(
                            user,
                            source,
                            target,
                            'calls',
                            _runtime_edge_label(
                                display_profile,
                                connection['protocol'],
                                target_endpoint
                            ),
                            edge_status,
                            _runtime_metadata(
                                host.id,
                                observed_at,
                                role='connection',
                                monitor_mode='fixed',
                                monitor_role='dependency_link',
                                monitor_source='discovery',
                                monitor_key=monitor_key,
                                connection_kind=display_profile['kind'],
                                dependency_service=display_profile['service']
                                if display_profile['kind'] == 'dependency' else '',
                                runtime_layer=display_profile.get('runtime_layer'),
                                service=display_profile['service'],
                                detected_by=connection.get('detected_by') or
                                display_profile.get('detected_by'),
                                config_file=connection.get('config_file') or '',
                                probe_host_id=host.id,
                                probe_address=probe_target.get('address'),
                                probe_port=probe_target.get('port'),
                                pid=int(source.metadata_data.get('pid') or 0),
                                protocol=connection['protocol'],
                                local=connection['local'],
                                peer=connection['peer'],
                                target_side=target_side,
                                state=connection['state'],
                            ),
                        )
                _runtime_update_fixed_services(
                    user, host.id, observed_at, observed_service_keys
                )
                _runtime_update_fixed_calls(
                    user, host.id, observed_at, observed_connection_keys,
                    probe_results
                )

            summary.update({
                'status': 'succeeded',
                'process_count': len(processes),
                'listener_count': len(listeners),
                'connection_count': len(connections),
                'dependency_connection_count': len(dependency_connections),
                'business_connection_count': len(business_connections),
                'node_count': TopologyNode.objects.filter(
                    source_type='runtime',
                    is_active=True,
                    source_id=str(host.id),
                ).count(),
                'edge_count': TopologyEdge.objects.filter(
                    is_active=True,
                ).filter(
                    Q(
                        source__source_type='runtime',
                        source__source_id=str(host.id),
                    ) |
                    Q(
                        target__source_type='runtime',
                        target__source_id=str(host.id),
                    )
                ).count(),
            })
        except Exception as exc:
            summary.update({'status': 'failed', 'error': str(exc)[:255]})
        summaries.append(summary)
    return summaries


def _enrich_graph(nodes, edges):
    host_status, metric_target_status, summary_error = _observability_status(nodes)
    views = {}
    dynamic_status = {}

    for node in nodes:
        view = node.to_view()
        source_id = _source_id(node.source_id)
        detail = None
        if node.source_type == 'host':
            detail = host_status.get(source_id)
        elif node.source_type == 'metric_target':
            detail = metric_target_status.get(source_id)
        if detail:
            view = _apply_status(
                view,
                detail['status'],
                detail['status_source'],
                detail,
            )
            dynamic_status[node.id] = detail['status']
        else:
            dynamic_status[node.id] = node.status
        views[node.id] = view

    for _ in range(max(len(edges), 1)):
        changed = False
        for edge in edges:
            if edge.type in ('deployed_on', 'collects', 'alerts_on'):
                candidates = [
                    dynamic_status.get(edge.source_id),
                    dynamic_status.get(edge.target_id, edge.status),
                ]
            elif edge.type in ('depends_on', 'calls'):
                candidates = [
                    dynamic_status.get(edge.source_id),
                    dynamic_status.get(edge.target_id, edge.status),
                ]
            else:
                continue
            next_status = _highest_status(candidates)
            if next_status != dynamic_status.get(edge.source_id):
                dynamic_status[edge.source_id] = next_status
                changed = True
        if not changed:
            break

    for node in nodes:
        status = dynamic_status.get(node.id, node.status)
        view = views[node.id]
        if status != view['status']:
            _apply_status(view, status, 'inherited', view.get('metadata', {}).get('observability'))

    edge_views = []
    for edge in edges:
        view = edge.to_view()
        if edge.type in ('deployed_on', 'collects', 'alerts_on'):
            inherited = dynamic_status.get(edge.target_id)
        elif edge.type in ('depends_on', 'calls'):
            inherited = _highest_status([
                dynamic_status.get(edge.source_id),
                dynamic_status.get(edge.target_id),
            ])
        else:
            inherited = None
        if inherited and (edge.status == 'unknown' or inherited != 'unknown'):
            _apply_status(view, inherited, 'inherited')
        edge_views.append(view)

    return list(views.values()), edge_views, summary_error


def build_topology_graph(user):
    nodes = visible_topology_nodes(user)
    node_ids = {item.id for item in nodes}
    edges = list(TopologyEdge.objects.filter(
        is_active=True,
        source_id__in=node_ids,
        target_id__in=node_ids,
    ))
    node_views, edge_views, summary_error = _enrich_graph(nodes, edges)
    return {
        'schema': topology_schema(),
        'nodes': node_views,
        'edges': edge_views,
        'source': 'manual_topology_with_observability',
        'summary_error': summary_error,
    }


def _id_list(values, field_name, maximum=50):
    if values in (None, ''):
        return []
    if not isinstance(values, (list, tuple, set)):
        raise ValueError('%s scope must be a list' % field_name)
    result = []
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        if len(value) > 64:
            raise ValueError('%s scope contains an invalid id' % field_name)
        result.append(value)
    result = list(dict.fromkeys(result))
    if len(result) > maximum:
        raise ValueError('%s scope is too large' % field_name)
    return result


def _view_host_id(node):
    source = node.get('source') or {}
    source_id = _source_id(source.get('id'))
    if source.get('type') == 'host' and source_id is not None:
        return source_id
    observability = (node.get('metadata') or {}).get('observability') or {}
    host_id = _source_id(observability.get('host_id'))
    if host_id is not None:
        return host_id
    target = observability.get('target') or {}
    host = target.get('host') or {}
    return _source_id(host.get('id'))


def _evidence_node(node):
    observability = (node.get('metadata') or {}).get('observability') or {}
    return {
        'id': node['id'],
        'key': node.get('key'),
        'type': node.get('type'),
        'name': node.get('name'),
        'description': node.get('description'),
        'source': node.get('source'),
        'status': node.get('status'),
        'status_source': node.get('status_source'),
        'observability': {
            'host_id': observability.get('host_id'),
            'metrics': observability.get('metrics') or {},
            'alert_count': observability.get('alert_count') or 0,
            'alerts': [
                {
                    'id': item.get('id'),
                    'alert_name': item.get('alert_name'),
                    'severity': item.get('severity'),
                    'status': item.get('status'),
                    'summary': item.get('summary'),
                    'last_seen_at': item.get('last_seen_at'),
                }
                for item in (observability.get('alerts') or [])[:10]
            ],
        },
    }


def _evidence_edge(edge):
    return {
        'id': edge['id'],
        'source': edge.get('source'),
        'target': edge.get('target'),
        'type': edge.get('type'),
        'label': edge.get('label'),
        'status': edge.get('status'),
        'status_source': edge.get('status_source'),
        'probed': edge.get('probed'),
    }


def topology_scope_snapshot(user, node_ids=None, edge_ids=None, radius=1):
    graph = build_topology_graph(user)
    nodes = {item['id']: item for item in graph['nodes']}
    edges = {item['id']: item for item in graph['edges']}
    selected_nodes = set(_id_list(node_ids, 'node'))
    selected_edges = set(_id_list(edge_ids, 'edge'))
    try:
        radius = int(radius)
    except (TypeError, ValueError):
        radius = 1
    radius = max(0, min(radius, 3))

    missing_nodes = selected_nodes.difference(nodes)
    missing_edges = selected_edges.difference(edges)
    if missing_nodes or missing_edges:
        raise ValueError('topology scope contains invisible or missing objects')

    included = set(selected_nodes)
    for edge_id in selected_edges:
        edge = edges[edge_id]
        included.add(edge['source'])
        included.add(edge['target'])

    if not included:
        included = {
            item['id'] for item in graph['nodes']
            if item.get('status') not in ('healthy', 'disabled')
        }
        if not included:
            included = {item['id'] for item in graph['nodes'][:20]}

    adjacency = {}
    for edge in edges.values():
        adjacency.setdefault(edge['source'], set()).add(edge['target'])
        adjacency.setdefault(edge['target'], set()).add(edge['source'])

    frontier = set(included)
    for _ in range(radius):
        next_frontier = set()
        for node_id in frontier:
            next_frontier.update(adjacency.get(node_id, set()))
        next_frontier.difference_update(included)
        included.update(next_frontier)
        frontier = next_frontier

    scoped_edges = [
        edge for edge in edges.values()
        if edge['id'] in selected_edges
        or (edge['source'] in included and edge['target'] in included)
    ]
    host_ids = sorted({
        host_id for host_id in (_view_host_id(nodes[node_id]) for node_id in included)
        if host_id is not None
    })
    return {
        'selected_node_ids': sorted(selected_nodes),
        'selected_edge_ids': sorted(selected_edges),
        'radius': radius,
        'host_ids': host_ids,
        'summary_error': graph.get('summary_error') or '',
        'nodes': [_evidence_node(nodes[node_id]) for node_id in sorted(included)],
        'edges': [_evidence_edge(edge) for edge in scoped_edges],
    }

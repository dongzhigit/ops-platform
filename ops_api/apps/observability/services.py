import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta

import requests
import pytz
from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.account.utils import get_host_perms
from apps.app.models import App, Deploy
from apps.audit.services import record_event
from apps.config.models import Service
from apps.host.models import Group, Host
from apps.monitor.utils import seconds_to_human
from apps.notify.models import Notify
from libs.spug import Notification
from .models import AlertEvent, AlertTransition, MetricTarget


METRICS = {
    'availability': {
        'name': '在线状态',
        'unit': '',
        'query': 'up{job="ops-platform-node"%s}',
    },
    'cpu': {
        'name': 'CPU 使用率',
        'unit': '%',
        'query': '100 - (avg by (ops_platform_host_id) (rate(node_cpu_seconds_total{job="ops-platform-node",mode="idle"%s}[5m])) * 100)',
    },
    'memory': {
        'name': '内存使用率',
        'unit': '%',
        'query': '100 * (1 - (node_memory_MemAvailable_bytes{job="ops-platform-node"%s} / node_memory_MemTotal_bytes{job="ops-platform-node"%s}))',
    },
    'disk': {
        'name': '磁盘最高使用率',
        'unit': '%',
        'query': 'max by (ops_platform_host_id) (100 * (1 - (node_filesystem_avail_bytes{job="ops-platform-node",fstype!~"tmpfs|overlay|squashfs|nsfs|erofs|fakeowner|fuse.*"%s} / node_filesystem_size_bytes{job="ops-platform-node",fstype!~"tmpfs|overlay|squashfs|nsfs|erofs|fakeowner|fuse.*"%s})))',
    },
    'network': {
        'name': '网络吞吐',
        'unit': 'B/s',
        'query': 'sum by (ops_platform_host_id) (rate(node_network_receive_bytes_total{job="ops-platform-node",device!~"lo|veth.*|docker.*|br-.*"%s}[5m]) + rate(node_network_transmit_bytes_total{job="ops-platform-node",device!~"lo|veth.*|docker.*|br-.*"%s}[5m]))',
    },
}

METRIC_TARGET_LABELS = (
    ('ops-platform-node', 'ops_platform_host_id'),
    ('spug-node', 'ops_platform_host_id'),
    ('spug-node', 'spug_host_id'),
)


class ObservabilityError(Exception):
    pass


def _safe_json_list(value):
    try:
        data = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _highest_alert_status(alerts):
    severities = {item.severity for item in alerts}
    if 'critical' in severities:
        return 'critical'
    if 'warning' in severities:
        return 'warning'
    if 'info' in severities:
        return 'info'
    return None


def _host_topology_status(target, metrics, alerts):
    if not target:
        return 'unknown'
    if not target.is_active:
        return 'disabled'
    alert_status = _highest_alert_status(alerts)
    if alert_status in ('critical', 'warning'):
        return alert_status
    if str(metrics.get('availability')) == '1':
        return 'healthy'
    if metrics:
        return 'offline'
    return 'unknown'


def _rollup_status(statuses):
    statuses = [item for item in statuses if item]
    if not statuses:
        return 'unknown'
    if 'critical' in statuses or 'offline' in statuses:
        return 'critical'
    if 'warning' in statuses:
        return 'warning'
    if 'info' in statuses:
        return 'info'
    if all(item == 'disabled' for item in statuses):
        return 'disabled'
    if all(item in ('unknown', 'disabled') for item in statuses):
        return 'unknown'
    return 'healthy'


def _group_names(host_ids):
    names = {host_id: [] for host_id in host_ids}
    relations = list(Group.hosts.through.objects.filter(host_id__in=host_ids))
    group_ids = {item.group_id for item in relations}
    groups = {
        item.id: item.name
        for item in Group.objects.filter(id__in=group_ids)
    }
    for item in relations:
        if item.host_id in names and item.group_id in groups:
            names[item.host_id].append(groups[item.group_id])
    return names


def business_topology(user):
    """Build a read-only topology snapshot from existing trusted records."""
    targets = MetricTarget.objects.select_related('host')
    if user.is_supper:
        allowed_host_ids = set(targets.values_list('host_id', flat=True))
    else:
        allowed_host_ids = set(get_host_perms(user, action='metrics.view'))
        targets = targets.filter(host_id__in=allowed_host_ids)
    targets = list(targets)
    target_host_ids = {item.host_id for item in targets}
    allowed_host_ids.update(target_host_ids)

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

    groups = _group_names(target_host_ids)
    nodes = []
    edges = []
    host_status = {}
    for target in targets:
        metrics = summary.get(str(target.host_id), {})
        alerts = alert_map.get(target.host_id, [])
        status = _host_topology_status(target, metrics, alerts)
        host_status[target.host_id] = status
        nodes.append({
            'id': 'host:%s' % target.host_id,
            'type': 'host',
            'name': target.host.name,
            'status': status,
            'description': target.host.hostname,
            'groups': groups.get(target.host_id, []),
            'metrics': metrics,
            'alerts': [item.to_view() for item in alerts[:10]],
            'target': target.to_view(),
        })

    can_view_business = user.is_supper or user.has_perms([
        'deploy.app.view', 'deploy.request.view', 'config.app.view',
        'config.src.view',
    ])
    if not can_view_business:
        return {
            'nodes': nodes,
            'edges': edges,
            'summary_error': summary_error,
            'connection_status_source': 'metrics_alerts_only',
        }

    if user.is_supper:
        apps = list(App.objects.all())
        services = list(Service.objects.all())
        deploys = list(Deploy.objects.select_related('app', 'env').all())
    else:
        perms = user.deploy_perms
        app_ids = set(perms['apps'])
        env_ids = set(perms['envs'])
        apps = list(App.objects.filter(id__in=app_ids))
        services = list(Service.objects.all()) if user.has_perms(['config.src.view']) else []
        deploys = list(Deploy.objects.select_related('app', 'env').filter(
            app_id__in=app_ids,
            env_id__in=env_ids,
        ))

    app_map = {item.id: item for item in apps}
    service_map = {item.id: item for item in services}
    app_deploy_statuses = {item.id: [] for item in apps}
    visible_service_ids = set()
    visible_deploy_host_ids = set()

    for deploy in deploys:
        host_ids = [
            int(item) for item in _safe_json_list(deploy.host_ids)
            if str(item).isdigit()
        ]
        visible_host_ids = [
            item for item in host_ids
            if user.is_supper or item in allowed_host_ids
        ]
        visible_deploy_host_ids.update(visible_host_ids)
        statuses = [host_status.get(item, 'unknown') for item in visible_host_ids]
        app_deploy_statuses.setdefault(deploy.app_id, []).extend(statuses)
        for host_id in visible_host_ids:
            edge_status = host_status.get(host_id, 'unknown')
            edges.append({
                'id': 'deploy:%s:%s' % (deploy.id, host_id),
                'source': 'app:%s' % deploy.app_id,
                'target': 'host:%s' % host_id,
                'type': 'deployed_on',
                'status': edge_status,
                'label': deploy.env.name,
                'probed': bool(summary.get(str(host_id))),
            })

    missing_host_ids = visible_deploy_host_ids.difference(target_host_ids)
    missing_groups = _group_names(missing_host_ids)
    for host in Host.objects.filter(id__in=missing_host_ids):
        host_status[host.id] = 'unknown'
        nodes.append({
            'id': 'host:%s' % host.id,
            'type': 'host',
            'name': host.name,
            'status': 'unknown',
            'description': host.hostname,
            'groups': missing_groups.get(host.id, []),
            'metrics': {},
            'alerts': [],
            'target': None,
        })

    for app in apps:
        rel_apps = [
            int(item) for item in _safe_json_list(app.rel_apps)
            if str(item).isdigit() and int(item) in app_map
        ]
        rel_services = [
            int(item) for item in _safe_json_list(app.rel_services)
            if str(item).isdigit() and int(item) in service_map
        ]
        status = _rollup_status(app_deploy_statuses.get(app.id, []))
        nodes.append({
            'id': 'app:%s' % app.id,
            'type': 'app',
            'name': app.name,
            'key': app.key,
            'status': status,
            'description': app.desc or '',
            'deploy_count': len([
                item for item in deploys if item.app_id == app.id
            ]),
        })
        for rel_id in rel_apps:
            edges.append({
                'id': 'appdep:%s:%s' % (app.id, rel_id),
                'source': 'app:%s' % app.id,
                'target': 'app:%s' % rel_id,
                'type': 'depends_on_app',
                'status': _rollup_status(app_deploy_statuses.get(rel_id, [])),
                'label': 'app dependency',
                'probed': False,
            })
        for rel_id in rel_services:
            visible_service_ids.add(rel_id)
            edges.append({
                'id': 'servicedep:%s:%s' % (app.id, rel_id),
                'source': 'app:%s' % app.id,
                'target': 'service:%s' % rel_id,
                'type': 'depends_on_service',
                'status': 'unknown',
                'label': 'service dependency',
                'probed': False,
            })

    for service in services:
        if service.id not in visible_service_ids and not user.is_supper:
            continue
        dependent_app_statuses = [
            node['status'] for node in nodes
            if node['type'] == 'app' and any(
                edge['source'] == node['id'] and edge['target'] == 'service:%s' % service.id
                for edge in edges
            )
        ]
        nodes.append({
            'id': 'service:%s' % service.id,
            'type': 'service',
            'name': service.name,
            'key': service.key,
            'status': _rollup_status(dependent_app_statuses),
            'description': service.desc or '',
        })

    return {
        'nodes': nodes,
        'edges': edges,
        'summary_error': summary_error,
        'connection_status_source': 'metrics_alerts_deploy_config',
    }


def valid_bearer(request, expected):
    header = request.META.get('HTTP_AUTHORIZATION', '')
    prefix = 'Bearer '
    provided = header[len(prefix):] if header.startswith(prefix) else ''
    return bool(provided and hmac.compare_digest(provided, expected))


def service_discovery_targets():
    result = []
    targets = MetricTarget.objects.select_related('host').filter(is_active=True)
    for target in targets:
        result.append({
            'targets': ['%s:%s' % (
                target.exporter_address, target.exporter_port
            )],
            'labels': {
                '__scheme__': target.scheme,
                '__metrics_path__': target.metrics_path,
                'ops_platform_host_id': str(target.host_id),
                'ops_platform_host_name': target.host.name,
                'ops_platform_notify_group_ids': ','.join(
                    str(item) for item in target.notify_group_ids
                ),
                'ops_platform_notify_modes': ','.join(target.notify_modes),
            },
        })
    return result


def _selector(host_id, host_label='ops_platform_host_id'):
    return ',%s="%s"' % (host_label, int(host_id)) if host_id is not None else ''


def metric_query(metric, host_id=None, job_name='ops-platform-node',
                 host_label='ops_platform_host_id'):
    if metric not in METRICS:
        raise ObservabilityError('不支持的指标类型')
    selector = _selector(host_id, host_label=host_label)
    query = METRICS[metric]['query'].replace(
        'job="ops-platform-node"',
        'job="%s"' % job_name,
    ).replace('ops_platform_host_id', host_label)
    count = METRICS[metric]['query'].count('%s')
    return query % tuple(selector for _ in range(count))


def _prometheus_get(path, params):
    if not settings.SPUG_OBSERVABILITY_ENABLED:
        raise ObservabilityError('可观测性服务尚未启用')
    try:
        response = requests.get(
            settings.SPUG_PROMETHEUS_URL.rstrip('/') + path,
            params=params,
            timeout=settings.SPUG_OBSERVABILITY_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logging.warning('Prometheus request failed: %s', exc)
        raise ObservabilityError('Prometheus 服务暂时不可用')
    if payload.get('status') != 'success':
        raise ObservabilityError('Prometheus 查询失败')
    return payload.get('data', {})


def query_metric(metric, host_id, start=None, end=None, step=None):
    result = []
    result_type = None
    for job_name, host_label in METRIC_TARGET_LABELS:
        query = metric_query(
            metric,
            host_id=host_id,
            job_name=job_name,
            host_label=host_label,
        )
        if start is None:
            data = _prometheus_get('/api/v1/query', {'query': query})
        else:
            data = _prometheus_get('/api/v1/query_range', {
                'query': query,
                'start': start,
                'end': end,
                'step': step,
            })
        result_type = result_type or data.get('resultType')
        result.extend(data.get('result', []))
    return {
        'metric': metric,
        'name': METRICS[metric]['name'],
        'unit': METRICS[metric]['unit'],
        'result_type': result_type,
        'result': result,
    }


def query_summary(host_ids):
    allowed = {str(item) for item in host_ids}
    output = {item: {} for item in allowed}
    for metric in METRICS:
        for job_name, host_label in METRIC_TARGET_LABELS:
            data = _prometheus_get('/api/v1/query', {
                'query': metric_query(
                    metric,
                    job_name=job_name,
                    host_label=host_label,
                ),
            })
            for row in data.get('result', []):
                host_id = str(row.get('metric', {}).get(host_label, ''))
                if host_id in allowed and row.get('value'):
                    output[host_id][metric] = row['value'][1]
    return output


def _bounded_mapping(value, field_name):
    if not isinstance(value, dict) or len(value) > 64:
        raise ObservabilityError('%s 必须是最多包含 64 项的对象' % field_name)
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ObservabilityError('%s 的键和值必须是字符串' % field_name)
        if len(key) > 128 or len(item) > 2048:
            raise ObservabilityError('%s 包含过长内容' % field_name)
        result[key] = item
    return result


def _event_time(value, default=None):
    parsed = parse_datetime(value) if value else None
    if parsed is None:
        return default or datetime.now()
    if parsed.tzinfo:
        parsed = parsed.astimezone(
            pytz.timezone(settings.TIME_ZONE)
        ).replace(tzinfo=None)
    return parsed


def _fingerprint(raw, labels):
    value = str(raw or '').strip()
    if value:
        if len(value) > 128:
            raise ObservabilityError('告警 fingerprint 过长')
        return value
    material = json.dumps(
        labels, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def _severity(value):
    value = str(value or '').lower()
    if value in ('critical', 'fatal', 'emergency'):
        return 'critical'
    if value in ('info', 'information', 'none'):
        return 'info'
    return 'warning'


def _host_label(labels, suffix):
    return (
        labels.get('ops_platform_host_%s' % suffix) or
        labels.get('spug_host_%s' % suffix)
    )


def _target_for_labels(labels):
    try:
        host_id = int(_host_label(labels, 'id'))
    except (TypeError, ValueError):
        return None
    return MetricTarget.objects.select_related('host').filter(
        host_id=host_id, is_active=True
    ).first()


def _transition(event, transition_type, actor=None, details=None):
    return AlertTransition.objects.create(
        alert=event,
        type=transition_type,
        actor=actor,
        details=json.dumps(
            details or {}, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'),
        ),
    )


def ingest_alert(raw, default_status=None):
    if not isinstance(raw, dict):
        raise ObservabilityError('alerts 中的每一项都必须是对象')
    labels = _bounded_mapping(raw.get('labels', {}), 'labels')
    annotations = _bounded_mapping(raw.get('annotations', {}), 'annotations')
    status = raw.get('status') or default_status
    if status not in ('firing', 'resolved'):
        raise ObservabilityError('告警状态必须是 firing 或 resolved')
    fingerprint = _fingerprint(raw.get('fingerprint'), labels)
    target = _target_for_labels(labels)
    now = datetime.now()
    starts_at = _event_time(raw.get('startsAt'), default=now)
    ends_at = _event_time(raw.get('endsAt'), default=now) if status == 'resolved' else None
    transition_type = None

    with transaction.atomic():
        previous = AlertEvent.objects.select_for_update().filter(
            fingerprint=fingerprint
        ).order_by('-episode').first()
        if status == 'firing' and (previous is None or previous.status == 'resolved'):
            event = AlertEvent.objects.create(
                fingerprint=fingerprint,
                episode=(previous.episode + 1 if previous else 1),
                host=target.host if target else None,
                alert_name=labels.get('alertname', '未命名告警')[:255],
                severity=_severity(labels.get('severity')),
                status='firing',
                instance=labels.get('instance', '')[:255] or None,
                summary=annotations.get('summary', '')[:255] or None,
                description=annotations.get('description') or None,
                labels=json.dumps(labels, ensure_ascii=False, sort_keys=True),
                annotations=json.dumps(annotations, ensure_ascii=False, sort_keys=True),
                starts_at=starts_at,
                last_seen_at=now,
                silenced_until=(previous.silenced_until if previous and previous.is_silenced else None),
                silenced_by=(previous.silenced_by if previous and previous.is_silenced else None),
                silence_reason=(previous.silence_reason if previous and previous.is_silenced else None),
                alertmanager_silence_id=(previous.alertmanager_silence_id if previous and previous.is_silenced else None),
            )
            transition_type = 'firing'
            _transition(event, transition_type, details={'source': 'alertmanager'})
        elif previous is None:
            event = AlertEvent.objects.create(
                fingerprint=fingerprint,
                host=target.host if target else None,
                alert_name=labels.get('alertname', '未命名告警')[:255],
                severity=_severity(labels.get('severity')),
                status='resolved',
                instance=labels.get('instance', '')[:255] or None,
                summary=annotations.get('summary', '')[:255] or None,
                description=annotations.get('description') or None,
                labels=json.dumps(labels, ensure_ascii=False, sort_keys=True),
                annotations=json.dumps(annotations, ensure_ascii=False, sort_keys=True),
                starts_at=starts_at,
                ends_at=ends_at,
                last_seen_at=now,
            )
            transition_type = 'resolved'
            _transition(event, transition_type, details={'source': 'alertmanager', 'orphan': True})
        else:
            event = previous
            was_firing = event.status == 'firing'
            event.host = target.host if target else event.host
            event.alert_name = labels.get('alertname', event.alert_name)[:255]
            event.severity = _severity(labels.get('severity'))
            event.instance = labels.get('instance', '')[:255] or None
            event.summary = annotations.get('summary', '')[:255] or None
            event.description = annotations.get('description') or None
            event.labels = json.dumps(labels, ensure_ascii=False, sort_keys=True)
            event.annotations = json.dumps(annotations, ensure_ascii=False, sort_keys=True)
            event.last_seen_at = now
            event.updated_at = now
            event.occurrence_count += 1
            if status == 'resolved' and was_firing:
                event.status = 'resolved'
                event.ends_at = ends_at
                transition_type = 'resolved'
                _transition(event, transition_type, details={'source': 'alertmanager'})
            event.save()

    if transition_type:
        record_event(
            correlation_id=event.correlation_id,
            action='observability.alert.%s' % transition_type,
            resource_type='metric_alert',
            resource_id=event.id,
            result='succeeded',
            details={
                'fingerprint': event.fingerprint,
                'episode': event.episode,
                'host_id': event.host_id,
                'severity': event.severity,
            },
        )
        _notify_transition(event, target, transition_type)
    return event, transition_type


def _notify_transition(event, target, transition_type):
    state_name = '告警发生' if transition_type == 'firing' else '故障恢复'
    Notify.make_monitor_notify(
        '%s：%s' % (state_name, event.alert_name),
        event.summary or event.description or event.instance or '无附加说明',
    )
    if not target or not target.notify_group_ids or not target.notify_modes:
        return
    if event.is_silenced:
        return
    duration_seconds = 0
    if transition_type == 'resolved' and event.ends_at:
        duration_seconds = max(0, int((event.ends_at - event.starts_at).total_seconds()))
    duration = seconds_to_human(duration_seconds) or '0秒'
    try:
        Notification(
            target.notify_group_ids,
            '1' if transition_type == 'firing' else '2',
            event.host.name if event.host_id else (event.instance or '未知目标'),
            event.alert_name,
            event.summary or event.description or '无附加说明',
            duration,
        ).dispatch_monitor(target.notify_modes)
    except Exception:
        logging.exception('Alert notification dispatch failed for event %s', event.id)
        Notify.make_system_notify(
            '指标告警通知发送失败', '告警事件 ID：%s' % event.id
        )


def create_alertmanager_silence(event, minutes, reason, user):
    labels = event.label_data
    matchers = []
    for name in ('alertname', 'ops_platform_host_id', 'spug_host_id', 'instance'):
        if labels.get(name):
            matchers.append({
                'name': name,
                'value': labels[name],
                'isRegex': False,
                'isEqual': True,
            })
    if not matchers:
        raise ObservabilityError('该告警缺少可用于静默的标签')
    now = datetime.now()
    ends_at = now + timedelta(minutes=minutes)
    utc_now = datetime.utcnow()
    utc_ends_at = utc_now + timedelta(minutes=minutes)
    payload = {
        'matchers': matchers,
        'startsAt': utc_now.isoformat() + 'Z',
        'endsAt': utc_ends_at.isoformat() + 'Z',
        'createdBy': user.nickname or user.username,
        'comment': reason,
    }
    try:
        response = requests.post(
            settings.SPUG_ALERTMANAGER_URL.rstrip('/') + '/api/v2/silences',
            json=payload,
            timeout=settings.SPUG_OBSERVABILITY_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        silence_id = response.json().get('silenceID')
    except (requests.RequestException, ValueError) as exc:
        logging.warning('Alertmanager silence creation failed: %s', exc)
        raise ObservabilityError('Alertmanager 暂时不可用，未创建静默')
    if not silence_id:
        raise ObservabilityError('Alertmanager 未返回静默 ID')
    return silence_id, ends_at


def delete_alertmanager_silence(silence_id):
    if not silence_id:
        return
    try:
        response = requests.delete(
            settings.SPUG_ALERTMANAGER_URL.rstrip('/') +
            '/api/v2/silence/' + silence_id,
            timeout=settings.SPUG_OBSERVABILITY_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning('Alertmanager silence deletion failed: %s', exc)
        raise ObservabilityError('Alertmanager 暂时不可用，未取消静默')

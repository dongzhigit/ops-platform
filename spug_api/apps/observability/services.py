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

from apps.audit.services import record_event
from apps.monitor.utils import seconds_to_human
from apps.notify.models import Notify
from libs.spug import Notification
from .models import AlertEvent, AlertTransition, MetricTarget


METRICS = {
    'availability': {
        'name': '在线状态',
        'unit': '',
        'query': 'up{job="spug-node"%s}',
    },
    'cpu': {
        'name': 'CPU 使用率',
        'unit': '%',
        'query': '100 - (avg by (spug_host_id) (rate(node_cpu_seconds_total{job="spug-node",mode="idle"%s}[5m])) * 100)',
    },
    'memory': {
        'name': '内存使用率',
        'unit': '%',
        'query': '100 * (1 - (node_memory_MemAvailable_bytes{job="spug-node"%s} / node_memory_MemTotal_bytes{job="spug-node"%s}))',
    },
    'disk': {
        'name': '磁盘最高使用率',
        'unit': '%',
        'query': 'max by (spug_host_id) (100 * (1 - (node_filesystem_avail_bytes{job="spug-node",fstype!~"tmpfs|overlay|squashfs|nsfs|erofs|fakeowner|fuse.*"%s} / node_filesystem_size_bytes{job="spug-node",fstype!~"tmpfs|overlay|squashfs|nsfs|erofs|fakeowner|fuse.*"%s})))',
    },
    'network': {
        'name': '网络吞吐',
        'unit': 'B/s',
        'query': 'sum by (spug_host_id) (rate(node_network_receive_bytes_total{job="spug-node",device!~"lo|veth.*|docker.*|br-.*"%s}[5m]) + rate(node_network_transmit_bytes_total{job="spug-node",device!~"lo|veth.*|docker.*|br-.*"%s}[5m]))',
    },
}


class ObservabilityError(Exception):
    pass


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
                'spug_host_id': str(target.host_id),
                'spug_host_name': target.host.name,
                'spug_notify_group_ids': ','.join(
                    str(item) for item in target.notify_group_ids
                ),
                'spug_notify_modes': ','.join(target.notify_modes),
            },
        })
    return result


def _selector(host_id):
    return ',spug_host_id="%s"' % int(host_id) if host_id is not None else ''


def metric_query(metric, host_id=None):
    if metric not in METRICS:
        raise ObservabilityError('不支持的指标类型')
    selector = _selector(host_id)
    count = METRICS[metric]['query'].count('%s')
    return METRICS[metric]['query'] % tuple(selector for _ in range(count))


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
    query = metric_query(metric, host_id=host_id)
    if start is None:
        data = _prometheus_get('/api/v1/query', {'query': query})
    else:
        data = _prometheus_get('/api/v1/query_range', {
            'query': query,
            'start': start,
            'end': end,
            'step': step,
        })
    return {
        'metric': metric,
        'name': METRICS[metric]['name'],
        'unit': METRICS[metric]['unit'],
        'result_type': data.get('resultType'),
        'result': data.get('result', []),
    }


def query_summary(host_ids):
    allowed = {str(item) for item in host_ids}
    output = {item: {} for item in allowed}
    for metric in METRICS:
        data = _prometheus_get('/api/v1/query', {
            'query': metric_query(metric),
        })
        for row in data.get('result', []):
            host_id = str(row.get('metric', {}).get('spug_host_id', ''))
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


def _target_for_labels(labels):
    try:
        host_id = int(labels.get('spug_host_id'))
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
    for name in ('alertname', 'spug_host_id', 'instance'):
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

import json
import time
import uuid
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.generic import View

from apps.account.utils import get_host_perms, has_host_perm
from apps.alarm.models import Alarm, Group
from apps.audit.services import record_event
from apps.host.models import Host
from libs import Argument, JsonParser, auth, human_datetime, json_response
from .models import AlertEvent, AlertTransition, MetricTarget
from .services import (
    METRICS,
    ObservabilityError,
    create_alertmanager_silence,
    delete_alertmanager_silence,
    ingest_alert,
    query_metric,
    query_summary,
    service_discovery_targets,
    valid_bearer,
)


def _validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(
            '%s: %s' % (field, ', '.join(messages))
            for field, messages in exc.message_dict.items()
        )
    return '; '.join(exc.messages)


def _service_error(message, status):
    response = JsonResponse({'error': message})
    response.status_code = status
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


def _alert_access(user, event):
    if user.is_supper:
        return True
    return bool(event.host_id and has_host_perm(
        user, event.host_id, action='metrics.view'
    ))


class PrometheusDiscoveryView(View):
    def get(self, request):
        if not settings.SPUG_OBSERVABILITY_ENABLED:
            return _service_error('observability disabled', 503)
        if not valid_bearer(request, settings.SPUG_PROMETHEUS_DISCOVERY_TOKEN):
            return _service_error('unauthorized', 401)
        response = JsonResponse(service_discovery_targets(), safe=False)
        response['Cache-Control'] = 'no-store, max-age=0'
        return response


class AlertmanagerWebhookView(View):
    def post(self, request):
        if not settings.SPUG_OBSERVABILITY_ENABLED:
            return _service_error('observability disabled', 503)
        if not valid_bearer(request, settings.SPUG_ALERTMANAGER_WEBHOOK_TOKEN):
            return _service_error('unauthorized', 401)
        try:
            length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            length = 0
        raw_body = request.body
        if length > settings.SPUG_ALERTMANAGER_MAX_BODY or len(raw_body) > settings.SPUG_ALERTMANAGER_MAX_BODY:
            return _service_error('payload too large', 413)
        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            return _service_error('invalid json payload', 400)
        if not isinstance(payload, dict):
            return _service_error('payload must be an object', 400)
        alerts = payload.get('alerts')
        if not isinstance(alerts, list) or not 1 <= len(alerts) <= 500:
            return _service_error('alerts must contain 1 to 500 items', 400)
        counts = {'created': 0, 'resolved': 0, 'aggregated': 0}
        try:
            for raw in alerts:
                event, transition = ingest_alert(
                    raw, default_status=payload.get('status')
                )
                if transition == 'firing':
                    counts['created'] += 1
                elif transition == 'resolved':
                    counts['resolved'] += 1
                else:
                    counts['aggregated'] += 1
        except ObservabilityError as exc:
            return _service_error(str(exc), 400)
        response = JsonResponse({'status': 'accepted', 'counts': counts})
        response['Cache-Control'] = 'no-store, max-age=0'
        return response


class MetricTargetView(View):
    @auth('monitor.metrics.view')
    def get(self, request):
        targets = MetricTarget.objects.select_related('host')
        if not request.user.is_supper:
            targets = targets.filter(host_id__in=get_host_perms(
                request.user, action='metrics.view'
            ))
        return json_response([item.to_view() for item in targets])

    @auth('monitor.metrics.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('host_id', type=int, help='请选择主机'),
            Argument('exporter_address', required=False),
            Argument(
                'exporter_port', type=int, default=9100,
                filter=lambda x: 1 <= x <= 65535,
                help='Exporter 端口必须在 1 到 65535 之间',
            ),
            Argument('scheme', default='http', filter=lambda x: x in ('http', 'https')),
            Argument('metrics_path', default='/metrics'),
            Argument('notify_grp', type=list, default=[]),
            Argument('notify_mode', type=list, default=[]),
            Argument('is_active', type=bool, default=True),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        if not has_host_perm(request.user, form.host_id, action='metrics.view'):
            return json_response(error='无权配置该主机的指标采集')
        host = Host.objects.filter(pk=form.host_id).first()
        if not host:
            return json_response(error='未找到指定主机')
        try:
            groups = sorted({int(item) for item in form.notify_grp})
        except (TypeError, ValueError):
            return json_response(error='报警联系组格式错误')
        if Group.objects.filter(id__in=groups).count() != len(groups):
            return json_response(error='包含不存在的报警联系组')
        modes = sorted({str(item) for item in form.notify_mode})
        if set(modes).difference(dict(Alarm.MODES)):
            return json_response(error='包含不支持的报警方式')
        target = MetricTarget.objects.filter(pk=form.id).first() if form.id else None
        if form.id and target is None:
            return json_response(error='未找到指定指标采集目标')
        if target and not has_host_perm(
                request.user, target.host_id, action='metrics.view'):
            return json_response(error='无权修改该指标采集目标')
        if MetricTarget.objects.filter(host=host).exclude(
                pk=target.pk if target else None).exists():
            return json_response(error='该主机已经配置指标采集目标')
        if target is None:
            target = MetricTarget(host=host, created_by=request.user)
        else:
            target.host = host
            target.updated_by = request.user
            target.updated_at = human_datetime()
        target.exporter_address = (
            str(form.exporter_address).strip()
            if form.exporter_address else host.hostname
        )
        target.exporter_port = form.exporter_port
        target.scheme = form.scheme
        target.metrics_path = str(form.metrics_path).strip()
        target.notify_grp = json.dumps(groups)
        target.notify_mode = json.dumps(modes)
        target.is_active = form.is_active
        try:
            target.full_clean()
            target.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='observability.target.write',
            resource_type='metric_target',
            resource_id=target.id,
            result='succeeded',
            details={
                'host_id': target.host_id,
                'exporter_address': target.exporter_address,
                'exporter_port': target.exporter_port,
                'scheme': target.scheme,
                'metrics_path': target.metrics_path,
                'is_active': target.is_active,
            },
            request=request,
        )
        target.host = host
        return json_response(target.to_view())

    @auth('monitor.metrics.manage')
    def delete(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定指标采集目标')
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        target = MetricTarget.objects.filter(pk=form.id).first()
        if not target:
            return json_response(error='未找到指定指标采集目标')
        if not has_host_perm(request.user, target.host_id, action='metrics.view'):
            return json_response(error='无权删除该主机的指标采集目标')
        target_id = target.id
        host_id = target.host_id
        target.delete()
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='observability.target.delete',
            resource_type='metric_target',
            resource_id=target_id,
            result='succeeded',
            details={'host_id': host_id},
            request=request,
        )
        return json_response()


class MetricCatalogView(View):
    @auth('monitor.metrics.view')
    def get(self, request):
        return json_response([
            {'key': key, 'name': item['name'], 'unit': item['unit']}
            for key, item in METRICS.items()
        ])


class MetricQueryView(View):
    @auth('monitor.metrics.view')
    def get(self, request):
        form, error = JsonParser(
            Argument('host_id', type=int, help='请选择主机'),
            Argument('metric', filter=lambda x: x in METRICS, help='不支持的指标类型'),
            Argument('start', type=float, required=False),
            Argument('end', type=float, required=False),
            Argument('step', type=int, required=False),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        if not has_host_perm(request.user, form.host_id, action='metrics.view'):
            return json_response(error='无权查看该主机指标')
        if not MetricTarget.objects.filter(host_id=form.host_id, is_active=True).exists():
            return json_response(error='该主机未启用指标采集')
        ranged = form.start is not None or form.end is not None or form.step is not None
        if ranged:
            if form.start is None or form.end is None or form.step is None:
                return json_response(error='范围查询必须同时提供 start、end 和 step')
            if form.start >= form.end or form.end - form.start > 31 * 86400:
                return json_response(error='指标查询时间范围必须在 31 天以内')
            if not 15 <= form.step <= 3600:
                return json_response(error='查询步长必须在 15 到 3600 秒之间')
            if form.end > time.time() + 300:
                return json_response(error='查询结束时间不能晚于当前时间')
        try:
            data = query_metric(
                form.metric,
                form.host_id,
                start=form.start if ranged else None,
                end=form.end if ranged else None,
                step=form.step if ranged else None,
            )
        except ObservabilityError as exc:
            return json_response(error=str(exc))
        return json_response(data)


class MetricSummaryView(View):
    @auth('monitor.metrics.view')
    def get(self, request):
        targets = MetricTarget.objects.select_related('host').filter(is_active=True)
        if not request.user.is_supper:
            targets = targets.filter(host_id__in=get_host_perms(
                request.user, action='metrics.view'
            ))
        targets = list(targets)
        if not targets:
            return json_response([])
        try:
            values = query_summary([item.host_id for item in targets])
        except ObservabilityError as exc:
            return json_response(error=str(exc))
        return json_response([
            {
                'target': item.to_view(),
                'metrics': values.get(str(item.host_id), {}),
            }
            for item in targets
        ])


class AlertEventView(View):
    @auth('alarm.event.view')
    def get(self, request):
        events = AlertEvent.objects.select_related(
            'host', 'claimed_by', 'silenced_by'
        )
        if not request.user.is_supper:
            events = events.filter(host_id__in=get_host_perms(
                request.user, action='metrics.view'
            ))
        status = request.GET.get('status')
        if status in dict(AlertEvent.STATUSES):
            events = events.filter(status=status)
        severity = request.GET.get('severity')
        if severity in dict(AlertEvent.SEVERITIES):
            events = events.filter(severity=severity)
        host_id = request.GET.get('host_id')
        if host_id:
            try:
                events = events.filter(host_id=int(host_id))
            except (TypeError, ValueError):
                return json_response(error='主机 ID 格式错误')
        event_id = request.GET.get('id')
        if event_id:
            try:
                event = events.filter(pk=int(event_id)).first()
            except (TypeError, ValueError):
                return json_response(error='告警事件 ID 格式错误')
            if not event:
                return json_response(error='未找到指定告警事件')
            data = event.to_view()
            data['transitions'] = [
                item.to_view() for item in event.transitions.select_related('actor')
            ]
            return json_response(data)
        return json_response([item.to_view() for item in events[:1000]])

    def patch(self, request):
        form, error = JsonParser(
            Argument('id', type=int, help='请指定告警事件'),
            Argument(
                'action', filter=lambda x: x in (
                    'claim', 'unclaim', 'silence', 'unsilence'
                ), help='不支持的告警操作'
            ),
            Argument('minutes', type=int, required=False),
            Argument('reason', required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        required_perm = (
            'alarm.event.silence'
            if form.action in ('silence', 'unsilence')
            else 'alarm.event.claim'
        )
        if not request.user.has_perms([required_perm]):
            return json_response(error='权限拒绝')
        event = AlertEvent.objects.select_related(
            'host', 'claimed_by', 'silenced_by'
        ).filter(pk=form.id).first()
        if not event:
            return json_response(error='未找到指定告警事件')
        if not _alert_access(request.user, event):
            return json_response(error='无权操作该主机的告警事件')
        now = datetime.now()
        details = {}
        if form.action == 'claim':
            if event.claimed_by_id and event.claimed_by_id != request.user.id and not request.user.is_supper:
                return json_response(error='该告警已被其他用户认领')
            event.claimed_by = request.user
            event.claimed_at = now
            transition_type = 'claimed'
        elif form.action == 'unclaim':
            if event.claimed_by_id not in (None, request.user.id) and not request.user.is_supper:
                return json_response(error='只能取消自己认领的告警')
            event.claimed_by = None
            event.claimed_at = None
            transition_type = 'unclaimed'
        elif form.action == 'silence':
            if event.status != 'firing':
                return json_response(error='只能静默告警中的事件')
            if form.minutes is None or not 5 <= form.minutes <= 7 * 24 * 60:
                return json_response(error='静默时长必须在 5 分钟到 7 天之间')
            reason = str(form.reason or '').strip()
            if not reason or len(reason) > 255:
                return json_response(error='请输入 1 到 255 字的静默原因')
            try:
                silence_id, ends_at = create_alertmanager_silence(
                    event, form.minutes, reason, request.user
                )
            except ObservabilityError as exc:
                return json_response(error=str(exc))
            event.silenced_until = ends_at
            event.silenced_by = request.user
            event.silence_reason = reason
            event.alertmanager_silence_id = silence_id
            transition_type = 'silenced'
            details = {'minutes': form.minutes, 'reason': reason}
        else:
            try:
                delete_alertmanager_silence(event.alertmanager_silence_id)
            except ObservabilityError as exc:
                return json_response(error=str(exc))
            event.silenced_until = None
            event.silenced_by = None
            event.silence_reason = None
            event.alertmanager_silence_id = None
            transition_type = 'unsilenced'
        event.updated_at = now
        event.save()
        AlertTransition.objects.create(
            alert=event,
            type=transition_type,
            actor=request.user,
            details=json.dumps(details, ensure_ascii=False, sort_keys=True),
        )
        record_event(
            correlation_id=event.correlation_id,
            actor=request.user,
            action='observability.alert.%s' % form.action,
            resource_type='metric_alert',
            resource_id=event.id,
            result='succeeded',
            details=details,
            request=request,
        )
        event.refresh_from_db()
        event.host = Host.objects.filter(pk=event.host_id).first() if event.host_id else None
        return json_response(event.to_view())

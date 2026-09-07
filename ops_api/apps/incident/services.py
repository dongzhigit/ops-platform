import json
from datetime import datetime

from django.db.models import Q

from apps.account.models import User
from apps.account.utils import has_host_perm
from apps.aiops.models import AIInvestigation
from apps.audit.services import record_event
from apps.observability.models import AlertEvent
from apps.topology.services import (
    topology_scope_snapshot,
    visible_topology_nodes,
)

from .models import IncidentRoom, IncidentTimeline


class IncidentError(Exception):
    pass


def _json_list(values, field_name, maximum=50):
    values = values or []
    if not isinstance(values, list) or len(values) > maximum:
        raise IncidentError('%s 格式错误' % field_name)
    result = []
    for value in values:
        value = str(value).strip()
        if value:
            result.append(value)
    return list(dict.fromkeys(result))


def _json_text(value):
    return json.dumps(value or [], ensure_ascii=False, sort_keys=True)


def _alert_or_error(user, alert_id):
    if not alert_id:
        return None
    if not user.has_perms(['alarm.event.view']):
        raise IncidentError('无权关联该告警事件')
    alert = AlertEvent.objects.select_related('host').filter(pk=alert_id).first()
    if not alert:
        raise IncidentError('未找到指定告警事件')
    if not user.is_supper and (
            not alert.host_id or
            not has_host_perm(user, alert.host_id, action='metrics.view')):
        raise IncidentError('无权关联该告警事件')
    return alert


def _visible_ai_ids(user, values):
    ids = _json_list(values, 'AI 调查范围', maximum=20)
    if not ids:
        return []
    query = AIInvestigation.objects.filter(pk__in=ids)
    if not user.is_supper:
        query = query.filter(created_by=user)
    found = {str(item.id) for item in query}
    if set(ids).difference(found):
        raise IncidentError('AI 调查范围包含无权访问或不存在的记录')
    return ids


def _visible_topology_ids(user, values):
    ids = _json_list(values, '拓扑节点范围', maximum=50)
    if not ids:
        return []
    visible = {str(item.id) for item in visible_topology_nodes(user)}
    if set(ids).difference(visible):
        raise IncidentError('拓扑节点范围包含无权访问或不存在的节点')
    return ids


def visible_rooms(user):
    query = IncidentRoom.objects.select_related(
        'alert', 'alert__host', 'owner', 'created_by'
    )
    if user.is_supper:
        return query
    visible = []
    for room in query:
        if room.created_by_id == user.id or room.owner_id == user.id:
            visible.append(room.id)
            continue
        if room.alert_id and room.alert.host_id and has_host_perm(
                user, room.alert.host_id, action='metrics.view'):
            visible.append(room.id)
    return query.filter(id__in=visible)


def room_or_error(user, room_id):
    room = visible_rooms(user).filter(pk=room_id).first()
    if not room:
        raise IncidentError('未找到可访问的事件作战室')
    return room


def _timeline(room, event_type, message, user, details=None):
    return IncidentTimeline.objects.create(
        room=room,
        type=event_type,
        message=message,
        details=json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
        created_by=user,
    )


def create_room(user, values, request=None):
    alert = _alert_or_error(user, values.get('alert_id'))
    topology_ids = _visible_topology_ids(user, values.get('topology_node_ids'))
    ai_ids = _visible_ai_ids(user, values.get('ai_investigation_ids'))
    title = str(values.get('title') or '').strip()
    if not title:
        title = alert.summary or alert.alert_name if alert else ''
    if not title:
        title = '未命名事件'
    if len(title) > 200:
        raise IncidentError('事件标题不能超过 200 个字符')
    severity = values.get('severity') or (alert.severity if alert else 'warning')
    if severity not in dict(IncidentRoom.SEVERITIES):
        raise IncidentError('事件级别无效')
    room = IncidentRoom.objects.create(
        title=title,
        severity=severity,
        status='open',
        alert=alert,
        topology_node_ids=_json_text(topology_ids),
        ai_investigation_ids=_json_text(ai_ids),
        owner=user,
        postmortem_draft=str(values.get('postmortem_draft') or '')[:20000],
        created_by=user,
    )
    _timeline(room, 'created', '事件作战室已创建', user, {
        'alert_id': room.alert_id,
        'topology_node_ids': topology_ids,
        'ai_investigation_ids': ai_ids,
    })
    record_event(
        correlation_id=room.correlation_id,
        actor=user,
        action='incident.room.create',
        resource_type='incident_room',
        resource_id=str(room.id),
        result='succeeded',
        details={'alert_id': room.alert_id, 'severity': room.severity},
        request=request,
    )
    return room


def update_room(user, room, values, request=None):
    changes = {}
    if 'title' in values:
        title = str(values.get('title') or '').strip()
        if not title or len(title) > 200:
            raise IncidentError('事件标题长度无效')
        room.title = title
        changes['title'] = title
    if 'severity' in values:
        severity = values.get('severity')
        if severity not in dict(IncidentRoom.SEVERITIES):
            raise IncidentError('事件级别无效')
        room.severity = severity
        changes['severity'] = severity
    if 'status' in values:
        status = values.get('status')
        if status not in dict(IncidentRoom.STATUSES):
            raise IncidentError('事件状态无效')
        room.status = status
        changes['status'] = status
        room.closed_at = datetime.now() if status == 'closed' else None
    if 'topology_node_ids' in values:
        topology_ids = _visible_topology_ids(user, values.get('topology_node_ids'))
        room.topology_node_ids = _json_text(topology_ids)
        changes['topology_node_ids'] = topology_ids
    if 'ai_investigation_ids' in values:
        ai_ids = _visible_ai_ids(user, values.get('ai_investigation_ids'))
        room.ai_investigation_ids = _json_text(ai_ids)
        changes['ai_investigation_ids'] = ai_ids
    if 'postmortem_draft' in values:
        room.postmortem_draft = str(values.get('postmortem_draft') or '')[:20000]
        changes['postmortem_draft'] = True
    if values.get('claim'):
        room.owner = user
        changes['owner_id'] = user.id
    room.updated_by = user
    room.updated_at = datetime.now()
    room.save()
    if changes:
        _timeline(room, 'status' if 'status' in changes else 'note', '事件作战室已更新', user, changes)
    record_event(
        correlation_id=room.correlation_id,
        actor=user,
        action='incident.room.update',
        resource_type='incident_room',
        resource_id=str(room.id),
        result='succeeded',
        details=changes,
        request=request,
    )
    return room


def add_note(user, room, message, request=None):
    message = str(message or '').strip()
    if not message or len(message) > 4000:
        raise IncidentError('备注长度必须在 1 到 4000 个字符之间')
    item = _timeline(room, 'note', message, user)
    room.updated_by = user
    room.updated_at = datetime.now()
    room.save(update_fields=('updated_by', 'updated_at'))
    record_event(
        correlation_id=room.correlation_id,
        actor=user,
        action='incident.timeline.note',
        resource_type='incident_room',
        resource_id=str(room.id),
        result='succeeded',
        details={'timeline_id': str(item.id)},
        request=request,
    )
    return item


def room_detail(user, room):
    data = room.to_view(include_detail=True)
    topology_ids = room.topology_node_id_list
    if topology_ids:
        try:
            data['topology'] = topology_scope_snapshot(
                user, node_ids=topology_ids, radius=1
            )
        except ValueError:
            data['topology'] = None
    else:
        data['topology'] = None
    ai_ids = room.ai_investigation_id_list
    ai_query = AIInvestigation.objects.filter(pk__in=ai_ids).select_related('created_by')
    if not user.is_supper:
        ai_query = ai_query.filter(created_by=user)
    data['ai_investigations'] = [item.to_view() for item in ai_query]
    from apps.aiops.services import refresh_remediation_status, visible_remediation_proposals
    remediation_query = visible_remediation_proposals(user).filter(
        Q(incident_room=room) | Q(action_plan__investigation_id__in=ai_ids)
    ).distinct()
    data['remediation_proposals'] = [
        refresh_remediation_status(item).to_view()
        for item in remediation_query[:50]
    ]
    data['timeline'] = [
        item.to_view()
        for item in room.timeline.select_related('created_by')
    ]
    return data


def incident_scope(user):
    alerts = []
    if user.has_perms(['alarm.event.view']):
        query = AlertEvent.objects.select_related('host').all()
        if not user.is_supper:
            query = [
                item for item in query
                if item.host_id and has_host_perm(user, item.host_id, action='metrics.view')
            ]
        alerts = [{
            'id': item.id,
            'alert_name': item.alert_name,
            'severity': item.severity,
            'status': item.status,
            'summary': item.summary,
            'host_name': item.host.name if item.host_id else None,
            'last_seen_at': item.last_seen_at,
        } for item in list(query)[:100]]
    nodes = [
        item.to_view()
        for item in visible_topology_nodes(user)[:1000]
    ]
    ai_query = AIInvestigation.objects.select_related('created_by')
    if not user.is_supper:
        ai_query = ai_query.filter(created_by=user)
    investigations = [item.to_view() for item in ai_query[:100]]
    users = []
    if user.is_supper:
        users = [
            {'id': item.id, 'name': item.nickname or item.username}
            for item in User.objects.filter(is_active=True).order_by('nickname', 'username')[:200]
        ]
    return {
        'alerts': alerts,
        'topology_nodes': nodes,
        'ai_investigations': investigations,
        'users': users,
    }

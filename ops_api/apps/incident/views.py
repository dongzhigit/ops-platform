import json
import uuid

from django.views.generic import View

from libs import Argument, JsonParser, auth, json_response

from .models import IncidentRoom
from .services import (
    IncidentError,
    add_note,
    create_room,
    incident_scope,
    room_detail,
    room_or_error,
    update_room,
    visible_rooms,
)


def _response(data='', error=''):
    response = json_response(data=data, error=error)
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


ROOM_FIELDS = (
    'title', 'severity', 'status', 'alert_id', 'topology_node_ids',
    'ai_investigation_ids', 'postmortem_draft', 'claim',
)


def _submitted_fields(body, form):
    try:
        raw = json.loads((body or b'{}').decode('utf-8'))
    except (AttributeError, TypeError, ValueError):
        raw = {}
    return {
        key: getattr(form, key)
        for key in ROOM_FIELDS
        if isinstance(raw, dict) and key in raw
    }


class IncidentScopeView(View):
    @auth('incident.room.view|incident.room.manage')
    def get(self, request):
        return _response(incident_scope(request.user))


class IncidentRoomView(View):
    @auth('incident.room.view|incident.room.manage')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument(
                'status', required=False,
                filter=lambda x: x in dict(IncidentRoom.STATUSES),
                help='事件状态无效',
            ),
            Argument('limit', type=int, default=100, filter=lambda x: 1 <= x <= 200),
        ).parse(request.GET)
        if error:
            return _response(error=error)
        rooms = visible_rooms(request.user)
        if form.id:
            try:
                room = room_or_error(request.user, form.id)
            except IncidentError as exc:
                return _response(error=str(exc))
            return _response(room_detail(request.user, room))
        if form.status:
            rooms = rooms.filter(status=form.status)
        return _response([
            item.to_view()
            for item in rooms[:form.limit]
        ])

    @auth('incident.room.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument('title', type=str, required=False),
            Argument(
                'severity', required=False,
                filter=lambda x: x in dict(IncidentRoom.SEVERITIES),
                help='事件级别无效',
            ),
            Argument(
                'status', required=False,
                filter=lambda x: x in dict(IncidentRoom.STATUSES),
                help='事件状态无效',
            ),
            Argument('alert_id', type=int, required=False),
            Argument('topology_node_ids', type=list, default=[]),
            Argument('ai_investigation_ids', type=list, default=[]),
            Argument('postmortem_draft', type=str, required=False),
            Argument('claim', type=bool, default=False),
        ).parse(request.body)
        if error:
            return _response(error=error)
        try:
            if form.id:
                room = update_room(
                    request.user,
                    room_or_error(request.user, form.id),
                    _submitted_fields(request.body, form),
                    request=request,
                )
            else:
                room = create_room(request.user, dict(form), request=request)
        except IncidentError as exc:
            return _response(error=str(exc))
        return _response(room_detail(request.user, room))


class IncidentTimelineView(View):
    @auth('incident.room.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('room_id', type=uuid.UUID, help='请指定事件作战室'),
            Argument('message', type=str, handler=str.strip, help='请输入备注'),
        ).parse(request.body)
        if error:
            return _response(error=error)
        try:
            item = add_note(
                request.user,
                room_or_error(request.user, form.room_id),
                form.message,
                request=request,
            )
        except IncidentError as exc:
            return _response(error=str(exc))
        return _response(item.to_view())

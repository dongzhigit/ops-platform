import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.views.generic import View

from libs import Argument, JsonParser, human_datetime, json_response
from libs.decorators import auth
from apps.assets.models import Identity
from apps.audit.services import record_event, request_source
from apps.host.models import Host
from .models import RemoteAccessEndpoint, RemoteAccessSession
from .services import (
    RemoteAccessDenied,
    build_guacamole_data,
    ensure_remote_access,
    ensure_session_access,
    ticket_digest,
    valid_ticket_format,
)


def _validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(
            '%s: %s' % (field, ', '.join(messages))
            for field, messages in exc.message_dict.items()
        )
    return '; '.join(exc.messages)


def _secure_response(response):
    response['Cache-Control'] = 'no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _launch_error(status_code=403):
    response = json_response(error='远程访问票据无效或已失效')
    response.status_code = status_code
    return _secure_response(response)


class EndpointView(View):
    @auth('host.console.remote')
    def get(self, request):
        form, error = JsonParser(
            Argument('host_id', type=int, help='请选择主机'),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        if not settings.SPUG_REMOTE_GATEWAY_ENABLED:
            return json_response(error='远程桌面网关尚未启用')

        endpoints = RemoteAccessEndpoint.objects.select_related(
            'host', 'identity', 'identity__credential'
        ).filter(host_id=form.host_id)
        if request.user.is_supper:
            return json_response([item.to_view() for item in endpoints])

        available = []
        for endpoint in endpoints:
            try:
                ensure_remote_access(request.user, endpoint)
            except RemoteAccessDenied:
                continue
            available.append(endpoint.to_view())
        return json_response(available)

    def post(self, request):
        if not request.user.is_supper:
            return json_response(error='只有系统管理员可以配置远程桌面端点')
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('host_id', type=int, help='请选择主机'),
            Argument('protocol', filter=lambda x: x in dict(RemoteAccessEndpoint.PROTOCOLS),
                     help='不支持的远程桌面协议'),
            Argument('port', type=int, help='请输入远程桌面端口'),
            Argument('identity_id', type=int, help='请选择远程桌面身份'),
            Argument('display_name', handler=str.strip, help='请输入端点名称'),
            Argument('settings', type=dict, default={}),
            Argument('is_active', type=bool, default=True),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        host = Host.objects.filter(pk=form.host_id).first()
        identity = Identity.objects.select_related('credential').filter(pk=form.identity_id).first()
        if not host or not identity:
            return json_response(error='未找到指定主机或身份')

        endpoint = RemoteAccessEndpoint.objects.filter(pk=form.id).first() if form.id else None
        if form.id and not endpoint:
            return json_response(error='未找到指定远程桌面端点')
        if endpoint is None:
            endpoint = RemoteAccessEndpoint(created_by=request.user)
        else:
            endpoint.updated_by = request.user
            endpoint.updated_at = human_datetime()
        endpoint.host = host
        endpoint.identity = identity
        endpoint.protocol = form.protocol
        endpoint.port = form.port
        endpoint.display_name = form.display_name
        endpoint.settings = json.dumps(form.settings, ensure_ascii=False, sort_keys=True)
        endpoint.is_active = form.is_active
        try:
            endpoint.full_clean()
            endpoint.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))

        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='gateway.endpoint.write',
            resource_type='remote_endpoint',
            resource_id=endpoint.id,
            result='succeeded',
            details={
                'host_id': endpoint.host_id,
                'identity_id': endpoint.identity_id,
                'protocol': endpoint.protocol,
                'port': endpoint.port,
                'is_active': endpoint.is_active,
            },
            request=request,
        )
        endpoint.host = host
        endpoint.identity = identity
        return json_response(endpoint.to_view())

    def delete(self, request):
        if not request.user.is_supper:
            return json_response(error='只有系统管理员可以删除远程桌面端点')
        form, error = JsonParser(
            Argument('id', type=int, help='请指定远程桌面端点'),
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        endpoint = RemoteAccessEndpoint.objects.filter(pk=form.id).first()
        if not endpoint:
            return json_response(error='未找到指定远程桌面端点')
        endpoint_id = endpoint.id
        if endpoint.sessions.exists():
            endpoint.is_active = False
            endpoint.updated_by = request.user
            endpoint.updated_at = human_datetime()
            endpoint.save(update_fields=('is_active', 'updated_by', 'updated_at'))
            operation = 'disabled'
        else:
            endpoint.delete()
            operation = 'deleted'
        record_event(
            correlation_id=uuid.uuid4(),
            actor=request.user,
            action='gateway.endpoint.delete',
            resource_type='remote_endpoint',
            resource_id=endpoint_id,
            result='succeeded',
            details={'operation': operation},
            request=request,
        )
        return json_response({'operation': operation})


class SessionView(View):
    @auth('host.console.remote')
    def get(self, request):
        sessions = RemoteAccessSession.objects.select_related(
            'requester', 'endpoint', 'host', 'identity'
        )
        if not request.user.is_supper:
            sessions = sessions.filter(requester=request.user)
        return json_response([item.to_view() for item in sessions[:100]])

    @auth('host.console.remote')
    def post(self, request):
        form, error = JsonParser(
            Argument('endpoint_id', type=int, help='请选择远程桌面端点'),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        endpoint = RemoteAccessEndpoint.objects.select_related(
            'host', 'identity', 'identity__credential'
        ).filter(pk=form.endpoint_id).first()
        if not endpoint:
            return json_response(error='未找到远程桌面端点')
        try:
            ensure_remote_access(request.user, endpoint)
        except RemoteAccessDenied as exc:
            correlation_id = uuid.uuid4()
            record_event(
                correlation_id=correlation_id,
                actor=request.user,
                action='%s.connect' % endpoint.protocol,
                resource_type='host',
                resource_id=endpoint.host_id,
                result='denied',
                details={'endpoint_id': endpoint.id, 'reason': str(exc)},
                request=request,
            )
            return json_response(error=str(exc))

        ticket = secrets.token_urlsafe(32)
        now = datetime.now()
        session = RemoteAccessSession.objects.create(
            requester=request.user,
            endpoint=endpoint,
            host=endpoint.host,
            identity=endpoint.identity,
            protocol=endpoint.protocol,
            ticket_hash=ticket_digest(ticket),
            issued_at=now,
            expires_at=now + timedelta(seconds=settings.SPUG_REMOTE_TICKET_TTL),
            source_ip=request_source(request)['source_ip'],
        )
        record_event(
            correlation_id=session.correlation_id,
            actor=request.user,
            action='%s.connect' % session.protocol,
            resource_type='host',
            resource_id=session.host_id,
            result='issued',
            details={
                'session_id': str(session.id),
                'endpoint_id': session.endpoint_id,
                'identity_id': session.identity_id,
                'expires_at': session.expires_at,
            },
            request=request,
        )
        data = session.to_view()
        data['launch_url'] = '/api/v1/gateway/sessions/%s/launch/?%s' % (
            session.id, urlencode({'ticket': ticket})
        )
        return _secure_response(json_response(data))


class SessionDetailView(View):
    def _get_session(self, request, session_id):
        session = RemoteAccessSession.objects.select_related(
            'requester', 'endpoint', 'host', 'identity'
        ).filter(pk=session_id).first()
        if not session:
            return None, json_response(error='未找到远程桌面会话')
        if not request.user.is_supper and session.requester_id != request.user.id:
            return None, json_response(error='权限拒绝')
        return session, None

    @auth('host.console.remote')
    def get(self, request, session_id):
        session, error = self._get_session(request, session_id)
        return error or json_response(session.to_view())

    @auth('host.console.remote')
    def delete(self, request, session_id):
        with transaction.atomic():
            session = RemoteAccessSession.objects.select_for_update().select_related(
                'requester', 'endpoint', 'host', 'identity'
            ).filter(pk=session_id).first()
            if not session:
                return json_response(error='未找到远程桌面会话')
            if not request.user.is_supper and session.requester_id != request.user.id:
                return json_response(error='权限拒绝')
            if session.status in ('issued', 'launched'):
                session.status = 'closed'
                session.ended_at = datetime.now()
                session.save(update_fields=('status', 'ended_at'))
                changed = True
            else:
                changed = False
        if changed:
            record_event(
                correlation_id=session.correlation_id,
                actor=request.user,
                action='%s.connect' % session.protocol,
                resource_type='host',
                resource_id=session.host_id,
                result='closed',
                details={
                    'session_id': str(session.id),
                    'disconnect_enforced': False,
                },
                request=request,
            )
        data = session.to_view()
        data['disconnect_enforced'] = False
        return json_response(data)


class SessionLaunchView(View):
    def get(self, request, session_id):
        ticket = request.GET.get('ticket', '')
        actor = None
        audit_result = 'denied'
        audit_reason = 'invalid_ticket'
        redirect_data = None
        session = None
        now = datetime.now()

        with transaction.atomic():
            session = RemoteAccessSession.objects.select_for_update().select_related(
                'requester', 'endpoint', 'host', 'identity', 'identity__credential',
                'endpoint__host', 'endpoint__identity', 'endpoint__identity__credential',
            ).filter(pk=session_id).first()
            if session:
                ticket_matches = valid_ticket_format(ticket) and hmac.compare_digest(
                    ticket_digest(ticket), session.ticket_hash
                )
                if ticket_matches:
                    actor = session.requester
                    if session.status != 'issued':
                        audit_reason = 'ticket_already_consumed'
                    elif session.expires_at <= now:
                        session.status = 'expired'
                        session.ended_at = now
                        session.failure_reason = '启动票据已过期'
                        session.save(update_fields=('status', 'ended_at', 'failure_reason'))
                        audit_result = 'expired'
                        audit_reason = 'ticket_expired'
                    else:
                        try:
                            ensure_session_access(session.requester, session)
                            redirect_data = build_guacamole_data(session, now=now)
                        except RemoteAccessDenied as exc:
                            session.status = 'failed'
                            session.ended_at = now
                            session.failure_reason = str(exc)[:255]
                            session.save(update_fields=('status', 'ended_at', 'failure_reason'))
                            audit_reason = 'authorization_denied'
                        except Exception:
                            session.status = 'failed'
                            session.ended_at = now
                            session.failure_reason = 'Guacamole 启动数据生成失败'
                            session.save(update_fields=('status', 'ended_at', 'failure_reason'))
                            audit_result = 'failed'
                            audit_reason = 'gateway_payload_failed'
                        else:
                            session.status = 'launched'
                            session.launched_at = now
                            session.save(update_fields=('status', 'launched_at'))
                            audit_result = 'launched'
                            audit_reason = None

        if session:
            record_event(
                correlation_id=session.correlation_id,
                actor=actor,
                action='%s.connect' % session.protocol,
                resource_type='host',
                resource_id=session.host_id,
                result=audit_result,
                details={
                    'session_id': str(session.id),
                    'endpoint_id': session.endpoint_id,
                    'reason': audit_reason,
                },
                request=request,
            )
        if not redirect_data:
            return _launch_error(403 if session else 404)

        location = '%s/?%s' % (
            settings.SPUG_GUACAMOLE_PUBLIC_URL.rstrip('/'),
            urlencode({'data': redirect_data}),
        )
        return _secure_response(HttpResponseRedirect(location))

import json
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.views.generic import View

from apps.audit.services import record_event
from libs import Argument, JsonParser, auth, json_response

from .models import AIInvestigation
from .services import (
    AIOpsError,
    PROMPT_VERSION,
    PROVIDER,
    available_scope,
    collect_evidence,
    execute_investigation,
)


def _response(data='', error=''):
    response = json_response(data=data, error=error)
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


def _investigations(user):
    query = AIInvestigation.objects.select_related(
        'created_by', 'alert', 'action_plan'
    )
    if not user.is_supper:
        query = query.filter(created_by=user)
    return query


def _parse_scope(body):
    return JsonParser(
        Argument(
            'question', type=str, handler=str.strip,
            filter=lambda x: 10 <= len(x) <= 4000,
            help='调查问题长度必须在 10 到 4000 个字符之间',
        ),
        Argument('host_ids', type=list, default=[]),
        Argument('alert_id', type=int, required=False),
    ).parse(body)


class AIConfigView(View):
    @auth('aiops.investigation.view')
    def get(self, request):
        return _response({
            'enabled': settings.SPUG_AIOPS_ENABLED,
            'provider': PROVIDER,
            'model': settings.SPUG_AIOPS_MODEL,
            'prompt_version': PROMPT_VERSION,
            'api_key_configured': bool(settings.SPUG_AIOPS_API_KEY),
            'max_hosts': settings.SPUG_AIOPS_MAX_HOSTS,
            'rate_limit_per_minute': settings.SPUG_AIOPS_RATE_LIMIT_PER_MINUTE,
            'read_only': True,
            'execution_enabled': False,
        })


class AIScopeView(View):
    @auth('aiops.investigation.view')
    def get(self, request):
        return _response(available_scope(request.user))


class AIEvidencePreviewView(View):
    @auth('aiops.investigation.run')
    def post(self, request):
        form, error = _parse_scope(request.body)
        if error:
            return _response(error=error)
        try:
            evidence = collect_evidence(
                request.user, form.question, form.host_ids, form.alert_id
            )
        except AIOpsError as exc:
            return _response(error=str(exc))
        correlation_id = uuid.uuid4()
        record_event(
            correlation_id=correlation_id,
            actor=request.user,
            action='aiops.evidence.preview',
            resource_type='ai_evidence',
            result='succeeded',
            details={
                'host_count': len(set(form.host_ids)),
                'alert_id': form.alert_id,
                'evidence_count': len(evidence),
                'citations': [item['citation'] for item in evidence],
            },
            request=request,
        )
        return _response({
            'correlation_id': str(correlation_id),
            'read_only': True,
            'evidence': evidence,
        })


class AIInvestigationView(View):
    @auth('aiops.investigation.view')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument(
                'status', required=False,
                filter=lambda x: x in dict(AIInvestigation.STATUSES),
                help='不支持的调查状态',
            ),
            Argument('limit', type=int, default=100, filter=lambda x: 1 <= x <= 200),
        ).parse(request.GET)
        if error:
            return _response(error=error)
        investigations = _investigations(request.user)
        if form.id:
            investigation = investigations.filter(pk=form.id).first()
            if not investigation:
                return _response(error='未找到可访问的 AI 调查')
            return _response(investigation.to_view(include_detail=True))
        if form.status:
            investigations = investigations.filter(status=form.status)
        return _response([
            item.to_view() for item in investigations[:form.limit]
        ])

    @auth('aiops.investigation.run')
    def post(self, request):
        if not settings.SPUG_AIOPS_ENABLED:
            return _response(error='AI 运维功能尚未启用，请先配置大模型服务')
        form, error = _parse_scope(request.body)
        if error:
            return _response(error=error)
        try:
            host_ids = sorted({int(item) for item in form.host_ids})
        except (TypeError, ValueError):
            return _response(error='主机范围格式错误')
        recent = AIInvestigation.objects.filter(
            created_by=request.user,
            created_at__gte=datetime.now() - timedelta(minutes=1),
        ).count()
        if recent >= settings.SPUG_AIOPS_RATE_LIMIT_PER_MINUTE:
            return _response(error='AI 调查请求过于频繁，请稍后再试')
        lock_key = 'aiops:investigation:user:%s' % request.user.id
        if not cache.add(
                lock_key, 'running', timeout=settings.SPUG_AIOPS_REQUEST_TIMEOUT + 30):
            return _response(error='当前账户已有 AI 调查正在运行')
        try:
            try:
                evidence = collect_evidence(
                    request.user, form.question, host_ids, form.alert_id
                )
            except AIOpsError as exc:
                return _response(error=str(exc))
            investigation = AIInvestigation.objects.create(
                created_by=request.user,
                question=form.question,
                requested_host_ids=json.dumps(host_ids),
                alert_id=form.alert_id,
                status='running',
                provider=PROVIDER,
                model=settings.SPUG_AIOPS_MODEL,
                prompt_version=PROMPT_VERSION,
            )
            record_event(
                correlation_id=investigation.correlation_id,
                actor=request.user,
                action='aiops.investigation.run',
                resource_type='ai_investigation',
                resource_id=investigation.id,
                result='started',
                details={
                    'host_count': len(host_ids),
                    'alert_id': form.alert_id,
                    'model': settings.SPUG_AIOPS_MODEL,
                    'prompt_version': PROMPT_VERSION,
                    'read_only': True,
                },
                request=request,
            )
            investigation = execute_investigation(investigation, evidence=evidence)
            record_event(
                correlation_id=investigation.correlation_id,
                actor=request.user,
                action='aiops.investigation.run',
                resource_type='ai_investigation',
                resource_id=investigation.id,
                result='succeeded' if investigation.status == 'completed' else 'failed',
                details={
                    'evidence_count': len(investigation.evidence_data),
                    'citation_count': len(investigation.citation_list),
                    'status': investigation.status,
                    'error': investigation.error,
                    'read_only': True,
                },
                request=request,
            )
            investigation = _investigations(request.user).get(pk=investigation.pk)
            return _response(investigation.to_view(include_detail=True))
        finally:
            cache.delete(lock_key)

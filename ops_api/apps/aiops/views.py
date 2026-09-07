import json
import uuid
from datetime import datetime, timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.views.generic import View

from apps.audit.services import record_event
from apps.assets.encryption import CredentialEncryptionError
from libs import Argument, JsonParser, auth, json_response

from .config import (
    AIConfigError,
    get_runtime_config,
    save_runtime_config,
)
from .models import AIInvestigation, AIProviderConfig
from .services import (
    AIOpsError,
    PROMPT_VERSION,
    available_scope,
    collect_evidence,
    create_remediation_execution,
    create_remediation_proposal,
    execute_investigation,
    lookup_platform_references,
    provider_name,
    refresh_remediation_status,
    remediation_action_options,
    request_remediation_approval,
    update_remediation_execution,
    visible_remediation_executions,
    visible_remediation_proposals,
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
        Argument('topology_node_ids', type=list, default=[]),
        Argument('topology_edge_ids', type=list, default=[]),
        Argument('topology_radius', type=int, default=1, filter=lambda x: 0 <= x <= 3),
    ).parse(body)


class AIConfigView(View):
    @auth('aiops.investigation.view|aiops.config.manage')
    def get(self, request):
        config = get_runtime_config()
        config.update({
            'provider': provider_name(config['api_format']),
            'prompt_version': PROMPT_VERSION,
            'read_only': True,
            'execution_enabled': False,
            'can_manage': bool(request.user.has_perms(['aiops.config.manage'])),
        })
        return _response(config)

    @auth('aiops.config.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('enabled', type=bool),
            Argument(
                'api_format',
                filter=lambda x: x in dict(AIProviderConfig.API_FORMATS),
                help='模型 API 格式必须是 OpenAI 或 Anthropic',
            ),
            Argument(
                'base_url', handler=str.strip,
                filter=lambda x: 1 <= len(x) <= 500,
                help='模型服务地址长度必须在 1 到 500 个字符之间',
            ),
            Argument(
                'model', default='', handler=str.strip,
                filter=lambda x: len(x) <= 100,
            ),
            Argument('api_key', type=str, required=False),
            Argument('clear_api_key', type=bool, default=False),
            Argument('json_mode', type=bool),
            Argument('request_timeout', type=int, filter=lambda x: 1 <= x <= 120),
            Argument('max_hosts', type=int, filter=lambda x: 1 <= x <= 100),
            Argument('knowledge_limit', type=int, filter=lambda x: 1 <= x <= 20),
            Argument('max_output_tokens', type=int, filter=lambda x: 256 <= x <= 8192),
            Argument(
                'max_response_bytes', type=int,
                filter=lambda x: 4096 <= x <= 4194304,
            ),
            Argument(
                'rate_limit_per_minute', type=int,
                filter=lambda x: 1 <= x <= 60,
            ),
        ).parse(request.body)
        if error:
            return _response(error=error)
        values = dict(form)
        try:
            config = save_runtime_config(actor=request.user, values=values)
        except (AIConfigError, CredentialEncryptionError, ValidationError, ValueError) as exc:
            return _response(error=str(exc))
        correlation_id = uuid.uuid4()
        record_event(
            correlation_id=correlation_id,
            actor=request.user,
            action='aiops.config.write',
            resource_type='ai_provider_config',
            resource_id='1',
            result='succeeded',
            details={
                'enabled': config['enabled'],
                'api_format': config['api_format'],
                'base_url': config['base_url'],
                'model': config['model'],
                'json_mode': config['json_mode'],
                'api_key_updated': bool(values.get('api_key')),
                'api_key_cleared': bool(values.get('clear_api_key')),
            },
            request=request,
        )
        config.update({
            'provider': provider_name(config['api_format']),
            'prompt_version': PROMPT_VERSION,
            'read_only': True,
            'execution_enabled': False,
            'can_manage': True,
            'correlation_id': str(correlation_id),
        })
        return _response(config)


class AIScopeView(View):
    @auth('aiops.investigation.view')
    def get(self, request):
        return _response(available_scope(request.user))


class AIRemediationProposalView(View):
    @auth('aiops.remediation.view|aiops.remediation.manage')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument('action_plan_id', type=uuid.UUID, required=False),
            Argument('incident_room_id', type=uuid.UUID, required=False),
            Argument('limit', type=int, default=100, filter=lambda x: 1 <= x <= 200),
        ).parse(request.GET)
        if error:
            return _response(error=error)
        proposals = visible_remediation_proposals(request.user)
        if form.id:
            proposal = proposals.filter(pk=form.id).first()
            if not proposal:
                return _response(error='未找到可访问的修复提案')
            return _response(refresh_remediation_status(proposal).to_view())
        if form.action_plan_id:
            proposals = proposals.filter(action_plan_id=form.action_plan_id)
        if form.incident_room_id:
            proposals = proposals.filter(incident_room_id=form.incident_room_id)
        return _response({
            'actions': remediation_action_options(),
            'proposals': [
                refresh_remediation_status(item).to_view()
                for item in proposals[:form.limit]
            ],
        })

    @auth('aiops.remediation.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('action_plan_id', type=uuid.UUID, help='请指定 AI 行动方案'),
            Argument('incident_room_id', type=uuid.UUID, required=False),
            Argument('proposed_action', required=False),
            Argument('title', type=str, required=False),
            Argument('summary', type=str, required=False),
            Argument('target_refs', type=list, default=[]),
            Argument('validation_plan', type=str, required=False),
            Argument('rollback_plan', type=str, required=False),
            Argument('citation_refs', type=list, default=[]),
        ).parse(request.body)
        if error:
            return _response(error=error)
        try:
            proposal = create_remediation_proposal(
                request.user, dict(form), request=request
            )
        except AIOpsError as exc:
            return _response(error=str(exc))
        return _response(proposal.to_view())


class AIRemediationApprovalView(View):
    @auth('aiops.remediation.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, help='请指定修复提案')
        ).parse(request.body)
        if error:
            return _response(error=error)
        try:
            proposal = request_remediation_approval(
                request.user, form.id, request=request
            )
        except AIOpsError as exc:
            return _response(error=str(exc))
        return _response(refresh_remediation_status(proposal).to_view())


class AIRemediationExecutionView(View):
    @auth('aiops.remediation.view|aiops.remediation.manage')
    def get(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument('proposal_id', type=uuid.UUID, required=False),
            Argument('limit', type=int, default=100, filter=lambda x: 1 <= x <= 200),
        ).parse(request.GET)
        if error:
            return _response(error=error)
        executions = visible_remediation_executions(request.user)
        if form.id:
            execution = executions.filter(pk=form.id).first()
            if not execution:
                return _response(error='未找到可访问的修复执行记录')
            return _response(execution.to_view())
        if form.proposal_id:
            executions = executions.filter(proposal_id=form.proposal_id)
        return _response([item.to_view() for item in executions[:form.limit]])

    @auth('aiops.remediation.manage')
    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=uuid.UUID, required=False),
            Argument('proposal_id', type=uuid.UUID, required=False),
            Argument('mode', required=False),
            Argument('platform_action', required=False),
            Argument('execution_ref', required=False),
            Argument('execution_summary', type=str, required=False),
            Argument('status', required=False),
            Argument('validation_result', type=str, required=False),
            Argument('rollback_result', type=str, required=False),
        ).parse(request.body)
        if error:
            return _response(error=error)
        try:
            if form.id:
                execution = update_remediation_execution(
                    request.user, form.id, dict(form), request=request
                )
            else:
                execution = create_remediation_execution(
                    request.user, dict(form), request=request
                )
        except AIOpsError as exc:
            return _response(error=str(exc))
        return _response(execution.to_view())


class AIRemediationPlatformReferenceView(View):
    @auth('aiops.remediation.view|aiops.remediation.manage')
    def get(self, request):
        form, error = JsonParser(
            Argument('platform_action', required=False),
            Argument('keyword', required=False),
            Argument('limit', type=int, default=50, filter=lambda x: 1 <= x <= 100),
        ).parse(request.GET)
        if error:
            return _response(error=error)
        try:
            records = lookup_platform_references(
                request.user,
                platform_action=form.platform_action,
                keyword=form.keyword,
                limit=form.limit,
            )
        except AIOpsError as exc:
            return _response(error=str(exc))
        return _response(records)


class AIEvidencePreviewView(View):
    @auth('aiops.investigation.run')
    def post(self, request):
        form, error = _parse_scope(request.body)
        if error:
            return _response(error=error)
        config = get_runtime_config()
        try:
            evidence = collect_evidence(
                request.user, form.question, form.host_ids, form.alert_id,
                topology_node_ids=form.topology_node_ids,
                topology_edge_ids=form.topology_edge_ids,
                topology_radius=form.topology_radius,
                config=config,
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
                'topology_node_count': len(set(form.topology_node_ids)),
                'topology_edge_count': len(set(form.topology_edge_ids)),
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
        try:
            config = get_runtime_config(include_secret=True)
        except AIConfigError as exc:
            return _response(error=str(exc))
        if not config['enabled']:
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
        if recent >= config['rate_limit_per_minute']:
            return _response(error='AI 调查请求过于频繁，请稍后再试')
        lock_key = 'aiops:investigation:user:%s' % request.user.id
        if not cache.add(
                lock_key, 'running', timeout=config['request_timeout'] + 30):
            return _response(error='当前账户已有 AI 调查正在运行')
        try:
            try:
                evidence = collect_evidence(
                    request.user, form.question, host_ids, form.alert_id,
                    topology_node_ids=form.topology_node_ids,
                    topology_edge_ids=form.topology_edge_ids,
                    topology_radius=form.topology_radius,
                    config=config,
                )
            except AIOpsError as exc:
                return _response(error=str(exc))
            investigation = AIInvestigation.objects.create(
                created_by=request.user,
                question=form.question,
                requested_host_ids=json.dumps(host_ids),
                alert_id=form.alert_id,
                status='running',
                provider=provider_name(config['api_format']),
                model=config['model'],
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
                    'topology_node_count': len(set(form.topology_node_ids)),
                    'topology_edge_count': len(set(form.topology_edge_ids)),
                    'api_format': config['api_format'],
                    'model': config['model'],
                    'prompt_version': PROMPT_VERSION,
                    'read_only': True,
                },
                request=request,
            )
            investigation = execute_investigation(
                investigation, evidence=evidence, config=config
            )
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

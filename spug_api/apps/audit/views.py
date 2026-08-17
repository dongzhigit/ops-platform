from django.db.models import Q
from django.views.generic import View

from apps.account.utils import has_host_perm
from libs import AdminView, Argument, JsonParser, json_response

from .models import ApprovalRequest, AuditEvent
from .services import (
    APPROVABLE_ACTIONS,
    ApprovalError,
    cancel_approval,
    create_approval,
    decide_approval,
    assess_risk,
    find_matching_approvals,
    requires_approval,
    verify_audit_chain,
)


HOST_PERMISSION_ACTIONS = {
    'file.delete': 'file.write',
    'schedule.write': 'schedule.run',
}


def _validate_host_scope(user, resource_type, resource_ids, action):
    if resource_type != 'host':
        return None
    permission_action = HOST_PERMISSION_ACTIONS.get(action, action)
    local_targets = [item for item in resource_ids if str(item) == 'local']
    host_targets = [item for item in resource_ids if str(item) != 'local']
    if local_targets and not user.is_supper:
        return '只有系统管理员可以申请在平台本机执行任务'
    if host_targets and not has_host_perm(user, host_targets, action=permission_action):
        return '无权为目标主机申请该操作'
    return None


class ApprovalView(View):
    def get(self, request):
        approvals = ApprovalRequest.objects.select_related('requester', 'decided_by')
        if not request.user.is_supper:
            approvals = approvals.filter(requester=request.user)
        status = request.GET.get('status')
        if status:
            approvals = approvals.filter(status=status)
        keyword = request.GET.get('keyword')
        if keyword:
            approvals = approvals.filter(Q(summary__icontains=keyword) | Q(action__icontains=keyword))
        data = []
        for approval in approvals[:500]:
            approval.refresh_expiry_status()
            data.append(approval.to_view())
        return json_response(data)

    def post(self, request):
        form, error = JsonParser(
            Argument('action', filter=lambda x: x in APPROVABLE_ACTIONS, help='不支持的审批动作'),
            Argument('resource_type', filter=lambda x: x in ('host', 'host_group', 'deploy', 'schedule')),
            Argument('resource_ids', type=list, filter=lambda x: 0 < len(x) <= 500, help='请选择操作对象'),
            Argument('payload', type=dict, help='请输入待审批的结构化操作参数'),
            Argument('summary', filter=lambda x: 0 < len(x.strip()) <= 255, help='请输入变更摘要'),
            Argument('rollback_plan', required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)

        scope_error = _validate_host_scope(
            request.user, form.resource_type, form.resource_ids, form.action
        )
        if scope_error:
            return json_response(error=scope_error)
        try:
            approval = create_approval(
                requester=request.user,
                action=form.action,
                resource_type=form.resource_type,
                resource_ids=form.resource_ids,
                payload=form.payload,
                summary=form.summary,
                rollback_plan=form.rollback_plan,
                request=request,
            )
        except ApprovalError as exc:
            return json_response(error=exc.message)
        return json_response(approval.to_view())

    def delete(self, request):
        form, error = JsonParser(
            Argument('id', help='请指定审批单')
        ).parse(request.GET)
        if error:
            return json_response(error=error)
        try:
            cancel_approval(approval_id=form.id, requester=request.user, request=request)
        except ApprovalError as exc:
            return json_response(error=exc.message)
        return json_response()


class OperationPreviewView(View):
    def post(self, request):
        form, error = JsonParser(
            Argument('action', filter=lambda x: x in APPROVABLE_ACTIONS, help='不支持的审批动作'),
            Argument('resource_type', filter=lambda x: x in ('host', 'host_group', 'deploy', 'schedule')),
            Argument('resource_ids', type=list, filter=lambda x: len(x) <= 500, help='操作对象数量超限'),
            Argument('payload', type=dict, help='请输入结构化操作参数'),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        scope_error = _validate_host_scope(
            request.user, form.resource_type, form.resource_ids, form.action
        )
        if scope_error:
            return json_response(error=scope_error)
        risk_level = assess_risk(form.action, form.payload, len(form.resource_ids))
        matches = find_matching_approvals(
            requester=request.user,
            action=form.action,
            resource_type=form.resource_type,
            resource_ids=form.resource_ids,
            payload=form.payload,
        ) if requires_approval(risk_level) else []
        return json_response({
            'risk_level': risk_level,
            'approval_required': requires_approval(risk_level),
            'rollback_required': risk_level == 'critical',
            'matching_approvals': [item.to_view() for item in matches],
        })


class ApprovalDecisionView(AdminView):
    def patch(self, request, approval_id):
        form, error = JsonParser(
            Argument('is_pass', type=bool, help='请选择审批结果'),
            Argument('comment', required=False),
            Argument(
                'ttl_minutes', type=int, default=30,
                filter=lambda x: 1 <= x <= 120,
                help='批准有效期必须在 1 到 120 分钟之间',
            ),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        try:
            approval = decide_approval(
                approval_id=approval_id,
                approver=request.user,
                is_pass=form.is_pass,
                comment=form.comment,
                ttl_minutes=form.ttl_minutes,
                request=request,
            )
        except ApprovalError as exc:
            return json_response(error=exc.message)
        return json_response(approval.to_view())


class AuditEventView(AdminView):
    def get(self, request):
        events = AuditEvent.objects.all()
        correlation_id = request.GET.get('correlation_id')
        if correlation_id:
            events = events.filter(correlation_id=correlation_id)
        action = request.GET.get('action')
        if action:
            events = events.filter(action=action)
        result = request.GET.get('result')
        if result:
            events = events.filter(result=result)
        return json_response([event.to_view() for event in events[:1000]])


class AuditVerifyView(AdminView):
    def get(self, request):
        valid, message = verify_audit_chain()
        return json_response({'valid': valid, 'message': message})

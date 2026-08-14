import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction

from .models import ApprovalRequest, AuditChain, AuditEvent


RISK_ORDER = ('low', 'medium', 'high', 'critical')
ACTION_RISKS = {
    'host.view': 'low',
    'ssh.connect': 'medium',
    'file.read': 'low',
    'file.write': 'high',
    'file.delete': 'critical',
    'file.distribute': 'high',
    'schedule.write': 'high',
    'schedule.run': 'high',
    'deploy.run': 'critical',
    'config.write': 'high',
}
APPROVABLE_ACTIONS = frozenset(ACTION_RISKS).union({'exec.run'})
CRITICAL_COMMAND_PATTERNS = (
    r'(^|[;&|]\s*)rm\s+[^\n]*(?:-rf|-fr)',
    r'\bmkfs(?:\.[a-z0-9]+)?\b',
    r'\bdd\b[^\n]*\bof=/dev/',
    r'\b(?:shutdown|poweroff|halt|reboot)\b',
    r'\biptables\b[^\n]*(?:\s-F\b|--flush\b)',
    r'\b(?:drop|truncate)\s+(?:database|table)\b',
    r'\b(?:userdel|groupdel)\b',
)
HIGH_COMMAND_PATTERNS = (
    r'\bsudo\b',
    r'\bsystemctl\s+(?:stop|restart|disable|mask)\b',
    r'\b(?:yum|dnf|apt|apt-get)\s+(?:remove|purge|install|upgrade)\b',
    r'\bchmod\b[^\n]*\s-[rR]\b',
    r'\bchown\b[^\n]*\s-[rR]\b',
    r'\bkill\s+-9\b',
    r'\bdocker\s+(?:rm|rmi|system\s+prune)\b',
    r'\bkubectl\s+(?:delete|apply|replace|patch|drain)\b',
)
SENSITIVE_KEYS = (
    'authorization', 'cookie', 'credential', 'password', 'passwd', 'pkey',
    'private_key', 'secret', 'secret_data', 'token',
)


class ApprovalError(Exception):
    def __init__(self, message, correlation_id=None, risk_level=None):
        super().__init__(message)
        self.message = message
        self.correlation_id = correlation_id
        self.risk_level = risk_level


def normalize_resource_ids(values):
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return sorted({str(item) for item in values if item is not None})


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def operation_payload_hash(payload):
    return hashlib.sha256(canonical_json(payload or {}).encode('utf-8')).hexdigest()


def sanitize_details(value, key=''):
    lowered = str(key).lower()
    if any(item in lowered for item in SENSITIVE_KEYS):
        return '[REDACTED]'
    if isinstance(value, dict):
        return {str(k): sanitize_details(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_details(item) for item in value]
    if isinstance(value, bytes):
        return '[BINARY %d bytes]' % len(value)
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + '...[TRUNCATED]'
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _raise_risk(current, requested):
    return RISK_ORDER[max(RISK_ORDER.index(current), RISK_ORDER.index(requested))]


def assess_risk(action, payload=None, target_count=None):
    payload = payload or {}
    risk = ACTION_RISKS.get(action, 'medium')
    if action == 'exec.run':
        command = str(payload.get('command', '')).lower()
        risk = 'medium'
        if any(re.search(pattern, command, re.I) for pattern in CRITICAL_COMMAND_PATTERNS):
            risk = 'critical'
        elif any(re.search(pattern, command, re.I) for pattern in HIGH_COMMAND_PATTERNS):
            risk = 'high'
    if target_count is None:
        target_count = len(payload.get('host_ids') or payload.get('resource_ids') or [])
    if target_count >= 50:
        risk = _raise_risk(risk, 'critical')
    elif target_count >= 10:
        risk = _raise_risk(risk, 'high')
    return risk


def requires_approval(risk_level):
    return risk_level in ('high', 'critical')


def request_source(request):
    if request is None:
        return {'source_ip': None, 'request_method': None, 'request_path': None}
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    source_ip = forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')
    return {
        'source_ip': source_ip,
        'request_method': request.method,
        'request_path': request.path[:255],
    }


def _event_material(event):
    return canonical_json({
        'id': str(event.id),
        'sequence': event.sequence,
        'correlation_id': str(event.correlation_id),
        'actor_id': event.actor_id,
        'actor_name': event.actor_name,
        'action': event.action,
        'resource_type': event.resource_type,
        'resource_id': event.resource_id,
        'result': event.result,
        'source_ip': event.source_ip,
        'request_method': event.request_method,
        'request_path': event.request_path,
        'details': event.details,
        'previous_hash': event.previous_hash,
        'created_at': event.created_at.isoformat(),
    })


def _sign_event(event):
    key = settings.SPUG_AUDIT_SIGNING_KEY.encode('utf-8')
    return hmac.new(key, _event_material(event).encode('utf-8'), hashlib.sha256).hexdigest()


def record_event(*, correlation_id, action, resource_type, result, actor=None,
                 resource_id=None, details=None, request=None):
    correlation_id = uuid.UUID(str(correlation_id))
    safe_details = sanitize_details(details or {})
    source = request_source(request)
    with transaction.atomic():
        AuditChain.objects.get_or_create(pk=1)
        chain = AuditChain.objects.select_for_update().get(pk=1)
        event = AuditEvent(
            sequence=chain.last_sequence + 1,
            correlation_id=correlation_id,
            actor=actor if getattr(actor, 'pk', None) else None,
            actor_name=getattr(actor, 'nickname', None) or getattr(actor, 'username', None),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id)[:100] if resource_id is not None else None,
            result=result,
            details=canonical_json(safe_details),
            previous_hash=chain.last_hash,
            created_at=datetime.now(),
            **source
        )
        event.event_hash = _sign_event(event)
        event.save(force_insert=True)
        chain.last_sequence = event.sequence
        chain.last_hash = event.event_hash
        chain.save(update_fields=('last_sequence', 'last_hash', 'updated_at'))
    return event


def verify_audit_chain():
    previous_hash = ''
    expected_sequence = 1
    for event in AuditEvent.objects.order_by('sequence').iterator():
        if event.sequence != expected_sequence:
            return False, '审计序号不连续，期望 %s，实际 %s' % (expected_sequence, event.sequence)
        if event.previous_hash != previous_hash:
            return False, '审计事件 %s 的前置哈希不匹配' % event.sequence
        if not hmac.compare_digest(event.event_hash, _sign_event(event)):
            return False, '审计事件 %s 的签名不匹配' % event.sequence
        previous_hash = event.event_hash
        expected_sequence += 1
    chain = AuditChain.objects.filter(pk=1).first()
    if chain and (chain.last_sequence != expected_sequence - 1 or chain.last_hash != previous_hash):
        return False, '审计链游标与事件记录不匹配'
    return True, '审计链完整，共 %s 条事件' % (expected_sequence - 1)


def create_approval(*, requester, action, resource_type, resource_ids, payload,
                    summary, rollback_plan=None, request=None):
    resource_ids = normalize_resource_ids(resource_ids)
    risk_level = assess_risk(action, payload, len(resource_ids))
    if risk_level == 'critical' and not str(rollback_plan or '').strip():
        raise ApprovalError('严重风险操作必须填写回滚方案', risk_level=risk_level)
    approval = ApprovalRequest.objects.create(
        requester=requester,
        action=action,
        resource_type=resource_type,
        resource_ids=canonical_json(resource_ids),
        risk_level=risk_level,
        summary=str(summary).strip(),
        rollback_plan=str(rollback_plan).strip() if rollback_plan else None,
        payload_hash=operation_payload_hash(payload),
    )
    record_event(
        correlation_id=approval.correlation_id,
        actor=requester,
        action=action,
        resource_type=resource_type,
        result='requested',
        details={
            'approval_id': str(approval.id),
            'resource_ids': resource_ids,
            'risk_level': risk_level,
            'summary': approval.summary,
            'payload_hash': approval.payload_hash,
        },
        request=request,
    )
    return approval


def decide_approval(*, approval_id, approver, is_pass, comment=None, ttl_minutes=30, request=None):
    with transaction.atomic():
        approval = ApprovalRequest.objects.select_for_update().select_related('requester').filter(
            pk=approval_id
        ).first()
        if not approval:
            raise ApprovalError('未找到指定审批单')
        if approval.refresh_expiry_status():
            raise ApprovalError('审批单已过期', approval.correlation_id, approval.risk_level)
        if approval.status != 'pending':
            raise ApprovalError('审批单当前状态不允许审批', approval.correlation_id, approval.risk_level)
        if approval.requester_id == approver.id:
            raise ApprovalError('申请人与审批人不能是同一用户', approval.correlation_id, approval.risk_level)
        if not is_pass and not str(comment or '').strip():
            raise ApprovalError('驳回时必须填写原因', approval.correlation_id, approval.risk_level)
        approval.status = 'approved' if is_pass else 'rejected'
        approval.decided_at = datetime.now()
        approval.decided_by = approver
        approval.decision_comment = str(comment).strip() if comment else None
        if is_pass:
            approval.approved_until = datetime.now() + timedelta(minutes=ttl_minutes)
        approval.save(update_fields=(
            'status', 'decided_at', 'decided_by', 'decision_comment', 'approved_until'
        ))
    record_event(
        correlation_id=approval.correlation_id,
        actor=approver,
        action=approval.action,
        resource_type=approval.resource_type,
        result='approved' if is_pass else 'rejected',
        details={
            'approval_id': str(approval.id),
            'risk_level': approval.risk_level,
            'comment': approval.decision_comment,
            'approved_until': approval.approved_until,
        },
        request=request,
    )
    return approval


def cancel_approval(*, approval_id, requester, request=None):
    with transaction.atomic():
        approval = ApprovalRequest.objects.select_for_update().filter(
            pk=approval_id, requester=requester
        ).first()
        if not approval:
            raise ApprovalError('未找到指定审批单')
        if approval.status != 'pending':
            raise ApprovalError('只有待审批申请可以撤销', approval.correlation_id, approval.risk_level)
        approval.status = 'cancelled'
        approval.save(update_fields=('status',))
    record_event(
        correlation_id=approval.correlation_id,
        actor=requester,
        action=approval.action,
        resource_type=approval.resource_type,
        result='cancelled',
        details={'approval_id': str(approval.id)},
        request=request,
    )
    return approval


def authorize_operation(*, requester, action, resource_type, resource_ids, payload,
                        approval_id=None, execution_ref=None, request=None):
    normalized_ids = normalize_resource_ids(resource_ids)
    risk_level = assess_risk(action, payload, len(normalized_ids))
    if not approval_id and not requires_approval(risk_level):
        return {
            'approval_id': None,
            'correlation_id': uuid.uuid4(),
            'risk_level': risk_level,
            'payload_hash': operation_payload_hash(payload),
        }
    if not approval_id:
        correlation_id = uuid.uuid4()
        record_event(
            correlation_id=correlation_id,
            actor=requester,
            action=action,
            resource_type=resource_type,
            result='denied',
            details={
                'reason': 'approval_required',
                'risk_level': risk_level,
                'resource_ids': normalized_ids,
            },
            request=request,
        )
        raise ApprovalError(
            '该操作风险等级为 %s，需要先创建并通过审批' % risk_level,
            correlation_id,
            risk_level,
        )

    with transaction.atomic():
        approval = ApprovalRequest.objects.select_for_update().filter(pk=approval_id).first()
        if not approval:
            raise ApprovalError('未找到指定审批单', risk_level=risk_level)
        approval.refresh_expiry_status()
        mismatch = (
            approval.requester_id != requester.id or
            approval.action != action or
            approval.resource_type != resource_type or
            normalize_resource_ids(approval.resource_id_list) != normalized_ids or
            approval.payload_hash != operation_payload_hash(payload) or
            approval.risk_level != risk_level
        )
        if mismatch:
            message = '审批单与当前操作、目标或参数不匹配'
        elif approval.status != 'approved':
            message = '审批单尚未批准、已过期或已经使用'
        else:
            message = None
        if message:
            correlation_id = approval.correlation_id
        else:
            approval.status = 'consumed'
            approval.consumed_at = datetime.now()
            approval.execution_ref = str(execution_ref)[:100] if execution_ref else None
            approval.save(update_fields=('status', 'consumed_at', 'execution_ref'))

    if message:
        record_event(
            correlation_id=correlation_id,
            actor=requester,
            action=action,
            resource_type=resource_type,
            result='denied',
            details={'approval_id': str(approval.id), 'reason': message},
            request=request,
        )
        raise ApprovalError(message, correlation_id, risk_level)
    return {
        'approval_id': approval.id,
        'correlation_id': approval.correlation_id,
        'risk_level': risk_level,
        'payload_hash': approval.payload_hash,
    }


def verify_consumed_approval(*, approval_id, requester_id, correlation_id, action,
                             execution_ref=None):
    if not approval_id:
        return True
    query = ApprovalRequest.objects.filter(
        pk=approval_id,
        requester_id=requester_id,
        correlation_id=correlation_id,
        action=action,
        status='consumed',
    )
    if execution_ref is not None:
        query = query.filter(execution_ref=str(execution_ref))
    return query.exists()

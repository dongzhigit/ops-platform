from datetime import datetime
import logging

from django.db import transaction
from django.db.models import Q

from apps.audit.services import record_event

from .models import FileTransfer


logger = logging.getLogger(__name__)
MAX_CLEANUP_ATTEMPTS = 10


def _candidate_query(at):
    return FileTransfer.objects.filter(
        Q(status='active', expires_at__lte=at) |
        Q(status__in=('expired', 'failed', 'cancelled')),
        cleanup_status__in=('pending', 'failed'),
        cleanup_attempts__lt=MAX_CLEANUP_ATTEMPTS,
    )


def cleanup_transfer_temporary_file(transfer, *, at=None):
    """Attempt one SFTP temporary-file cleanup and update its model state."""
    if transfer.cleanup_status in ('removed', 'missing') or (
        transfer.cleanup_attempts >= MAX_CLEANUP_ATTEMPTS
    ):
        return None
    at = at or datetime.now()
    transfer.cleanup_attempts += 1
    transfer.cleanup_attempted_at = at
    try:
        with transfer.host.get_ssh() as ssh:
            removed = ssh.remove_file_if_exists(transfer.temporary_path)
    except Exception as exc:
        outcome = 'failed'
        transfer.cleanup_status = outcome
        transfer.cleanup_error = 'cleanup failed: %s' % type(exc).__name__
        transfer.cleanup_completed_at = None
    else:
        outcome = 'removed' if removed else 'missing'
        transfer.cleanup_status = outcome
        transfer.cleanup_error = None
        transfer.cleanup_completed_at = at
    return outcome


def cleanup_expired_file_transfers(*, execute=False, limit=100, at=None):
    at = at or datetime.now()
    limit = max(1, min(int(limit), 1000))
    candidate_ids = list(
        _candidate_query(at).order_by('expires_at').values_list('id', flat=True)[:limit]
    )
    summary = {
        'eligible': len(candidate_ids),
        'removed': 0,
        'missing': 0,
        'failed': 0,
        'skipped': 0,
    }
    if not execute:
        return summary

    for transfer_id in candidate_ids:
        event = None
        with transaction.atomic():
            transfer = FileTransfer.objects.select_for_update().select_related(
                'host', 'uploader'
            ).filter(pk=transfer_id).first()
            if not transfer or transfer.cleanup_status not in ('pending', 'failed') or (
                transfer.cleanup_attempts >= MAX_CLEANUP_ATTEMPTS
            ):
                summary['skipped'] += 1
                continue
            if transfer.status == 'active':
                if transfer.expires_at > at:
                    summary['skipped'] += 1
                    continue
                transfer.status = 'expired'
                transfer.error = '上传会话已过期'
            elif transfer.status not in ('expired', 'failed', 'cancelled'):
                summary['skipped'] += 1
                continue

            outcome = cleanup_transfer_temporary_file(transfer, at=at)
            transfer.save(update_fields=(
                'status', 'error', 'cleanup_status', 'cleanup_attempts',
                'cleanup_attempted_at', 'cleanup_completed_at',
                'cleanup_error', 'updated_at',
            ))
            summary[outcome] += 1
            event = {
                'correlation_id': transfer.correlation_id,
                'actor': transfer.uploader,
                'resource_id': transfer.host_id,
                'result': 'failed' if outcome == 'failed' else 'succeeded',
                'details': {
                    'upload_id': str(transfer.id),
                    'filename': transfer.filename,
                    'cleanup_outcome': outcome,
                    'cleanup_attempt': transfer.cleanup_attempts,
                },
            }
        if event:
            try:
                record_event(
                    action='file.cleanup',
                    resource_type='host',
                    **event
                )
            except Exception:
                logger.exception('failed to record file transfer cleanup audit event')
    return summary

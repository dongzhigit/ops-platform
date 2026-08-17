import uuid
from datetime import datetime, timedelta

from django.db import models

from libs import ModelMixin


def file_transfer_expiry():
    return datetime.now() + timedelta(hours=24)


class FileTransferBatch(models.Model, ModelMixin):
    STATUSES = (
        ('active', '传输中'),
        ('paused', '已暂停'),
        ('completed', '已完成'),
        ('failed', '失败'),
        ('cancelled', '已取消'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(db_index=True, editable=False)
    creator = models.ForeignKey(
        'account.User', models.PROTECT, related_name='file_transfer_batches'
    )
    host = models.ForeignKey(
        'host.Host', models.PROTECT, related_name='file_transfer_batches'
    )
    approval = models.OneToOneField(
        'audit.ApprovalRequest', models.PROTECT,
        related_name='file_transfer_batch'
    )
    directory = models.CharField(max_length=1024)
    file_count = models.PositiveSmallIntegerField()
    total_size = models.BigIntegerField()
    max_concurrency = models.PositiveSmallIntegerField(default=2)
    status = models.CharField(
        max_length=16, choices=STATUSES, default='active', db_index=True
    )
    error = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def to_view(self, include_transfers=True):
        data = {
            'id': str(self.id),
            'correlation_id': str(self.correlation_id),
            'host_id': self.host_id,
            'directory': self.directory,
            'file_count': self.file_count,
            'total_size': self.total_size,
            'max_concurrency': self.max_concurrency,
            'status': self.status,
            'error': self.error,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
        }
        if include_transfers:
            transfers = getattr(self, '_view_transfers', None)
            if transfers is None:
                transfers = self.transfers.order_by('sequence', 'filename')
            data['transfers'] = [
                item.to_view() for item in transfers
            ]
        return data

    def to_dict(self, *args, **kwargs):
        return self.to_view()

    class Meta:
        db_table = 'file_transfer_batches'
        ordering = ('-created_at',)
        indexes = (
            models.Index(
                fields=('creator', 'status'), name='file_batch_creator_status_idx'
            ),
        )


class FileTransfer(models.Model, ModelMixin):
    STATUSES = (
        ('active', '上传中'),
        ('completed', '已完成'),
        ('failed', '失败'),
        ('cancelled', '已取消'),
        ('expired', '已过期'),
    )
    CONFLICT_STRATEGIES = (
        ('overwrite', '覆盖'),
        ('reject', '拒绝'),
    )
    CLEANUP_STATUSES = (
        ('pending', '待清理'),
        ('removed', '已删除'),
        ('missing', '文件不存在'),
        ('failed', '清理失败'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    correlation_id = models.UUIDField(db_index=True, editable=False)
    uploader = models.ForeignKey(
        'account.User', models.PROTECT, related_name='file_transfers'
    )
    host = models.ForeignKey(
        'host.Host', models.PROTECT, related_name='file_transfers'
    )
    approval = models.ForeignKey(
        'audit.ApprovalRequest', models.PROTECT, related_name='file_transfers'
    )
    batch = models.ForeignKey(
        FileTransferBatch, models.PROTECT, related_name='transfers',
        null=True, blank=True
    )
    sequence = models.PositiveSmallIntegerField(default=0)
    directory = models.CharField(max_length=1024)
    filename = models.CharField(max_length=255)
    remote_path = models.CharField(max_length=1280)
    temporary_path = models.CharField(max_length=1280)
    size = models.BigIntegerField()
    offset = models.BigIntegerField(default=0)
    chunk_size = models.PositiveIntegerField()
    expected_sha256 = models.CharField(max_length=64)
    actual_sha256 = models.CharField(max_length=64, null=True, blank=True)
    conflict_strategy = models.CharField(
        max_length=16, choices=CONFLICT_STRATEGIES, default='overwrite'
    )
    status = models.CharField(
        max_length=16, choices=STATUSES, default='active', db_index=True
    )
    error = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(default=file_transfer_expiry, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cleanup_status = models.CharField(
        max_length=16, choices=CLEANUP_STATUSES, default='pending', db_index=True
    )
    cleanup_attempts = models.PositiveSmallIntegerField(default=0)
    cleanup_attempted_at = models.DateTimeField(null=True, blank=True)
    cleanup_completed_at = models.DateTimeField(null=True, blank=True)
    cleanup_error = models.CharField(max_length=255, null=True, blank=True)

    def refresh_expiry_status(self, at=None):
        at = at or datetime.now()
        if self.status == 'active' and self.expires_at <= at:
            self.status = 'expired'
            self.error = '上传会话已过期'
            self.save(update_fields=('status', 'error', 'updated_at'))
            return True
        return False

    def to_view(self):
        return {
            'id': str(self.id),
            'correlation_id': str(self.correlation_id),
            'host_id': self.host_id,
            'batch_id': str(self.batch_id) if self.batch_id else None,
            'sequence': self.sequence,
            'directory': self.directory,
            'filename': self.filename,
            'size': self.size,
            'offset': self.offset,
            'chunk_size': self.chunk_size,
            'expected_sha256': self.expected_sha256,
            'actual_sha256': self.actual_sha256,
            'conflict_strategy': self.conflict_strategy,
            'status': self.status,
            'error': self.error,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'expires_at': self.expires_at,
            'completed_at': self.completed_at,
            'cleanup_status': self.cleanup_status,
            'cleanup_attempts': self.cleanup_attempts,
            'cleanup_attempted_at': self.cleanup_attempted_at,
            'cleanup_completed_at': self.cleanup_completed_at,
        }

    def to_dict(self, *args, **kwargs):
        return self.to_view()

    class Meta:
        db_table = 'file_transfers'
        ordering = ('-created_at',)
        indexes = (
            models.Index(fields=('uploader', 'status'), name='file_uploader_status_idx'),
            models.Index(fields=('batch', 'status'), name='file_batch_status_idx'),
        )
        unique_together = (('batch', 'filename'),)

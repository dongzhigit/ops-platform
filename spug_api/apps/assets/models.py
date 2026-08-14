import json
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models, transaction

from libs import ModelMixin, human_datetime
from .encryption import get_default_cipher


class Credential(models.Model, ModelMixin):
    TYPES = (
        ('ssh_key', 'SSH private key'),
        ('password', 'Password'),
        ('token', 'API token'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    type = models.CharField(max_length=20, choices=TYPES)
    key_id = models.CharField(max_length=50, default='primary')
    secret_data = models.TextField()
    description = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.CharField(max_length=20, default=human_datetime)
    updated_at = models.CharField(max_length=20, default=human_datetime)
    created_by = models.ForeignKey('account.User', models.PROTECT, related_name='+')
    updated_by = models.ForeignKey('account.User', models.PROTECT, related_name='+', null=True, blank=True)

    @classmethod
    def create_with_secret(cls, secret, **kwargs):
        obj = cls(**kwargs)
        obj.set_secret(secret)
        obj.save(force_insert=True)
        return obj

    def set_secret(self, secret, key_id=None):
        self.key_id = key_id or settings.SPUG_CREDENTIAL_PRIMARY_KEY_ID
        self.secret_data = get_default_cipher(self.key_id).encrypt(secret, self.id)
        self.updated_at = human_datetime()

    def reveal_secret(self):
        value = get_default_cipher(self.key_id).decrypt(self.secret_data, self.id)
        return value.decode('utf-8')

    def to_view(self):
        return {
            'id': str(self.id),
            'name': self.name,
            'type': self.type,
            'key_id': self.key_id,
            'description': self.description,
            'is_active': self.is_active,
            'has_secret': bool(self.secret_data),
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    def to_dict(self, *args, **kwargs):
        return self.to_view()

    class Meta:
        db_table = 'asset_credentials'
        ordering = ('name',)


class Identity(models.Model, ModelMixin):
    PROTOCOLS = (
        ('ssh', 'SSH'),
        ('sftp', 'SFTP'),
        ('rdp', 'RDP'),
        ('vnc', 'VNC'),
    )
    PRIVILEGE_MODES = (
        ('none', 'None'),
        ('sudo', 'sudo'),
        ('su', 'su'),
    )

    name = models.CharField(max_length=100, unique=True)
    protocol = models.CharField(max_length=10, choices=PROTOCOLS)
    username = models.CharField(max_length=100)
    credential = models.ForeignKey(Credential, models.PROTECT, related_name='identities')
    privilege_mode = models.CharField(max_length=10, choices=PRIVILEGE_MODES, default='none')
    constraints = models.TextField(default='{}')
    description = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.CharField(max_length=20, default=human_datetime)
    updated_at = models.CharField(max_length=20, default=human_datetime)
    created_by = models.ForeignKey('account.User', models.PROTECT, related_name='+')
    updated_by = models.ForeignKey('account.User', models.PROTECT, related_name='+', null=True, blank=True)

    def clean(self):
        if self.protocol in ('ssh', 'sftp') and self.credential.type not in ('ssh_key', 'password'):
            raise ValidationError('SSH/SFTP identities require an SSH key or password credential')
        if self.protocol in ('rdp', 'vnc') and self.credential.type != 'password':
            raise ValidationError('RDP/VNC identities require a password credential')
        if not self.credential.is_active:
            raise ValidationError('the selected credential is inactive')
        try:
            value = json.loads(self.constraints or '{}')
            if not isinstance(value, dict):
                raise ValueError
        except ValueError:
            raise ValidationError('identity constraints must be a JSON object')

    def to_view(self):
        return {
            'id': self.id,
            'name': self.name,
            'protocol': self.protocol,
            'username': self.username,
            'credential': {
                'id': str(self.credential_id),
                'name': self.credential.name,
                'type': self.credential.type,
            },
            'privilege_mode': self.privilege_mode,
            'constraints': json.loads(self.constraints or '{}'),
            'description': self.description,
            'is_active': self.is_active,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    class Meta:
        db_table = 'asset_identities'
        ordering = ('name',)


class AssetIdentityBinding(models.Model, ModelMixin):
    host = models.ForeignKey('host.Host', models.CASCADE, related_name='asset_identity_bindings')
    identity = models.ForeignKey(Identity, models.PROTECT, related_name='asset_bindings')
    is_default = models.BooleanField(default=False)
    created_at = models.CharField(max_length=20, default=human_datetime)
    created_by = models.ForeignKey('account.User', models.PROTECT, related_name='+')

    @classmethod
    def bind(cls, host, identity, created_by, is_default=False):
        with transaction.atomic():
            binding, _ = cls.objects.select_for_update().get_or_create(
                host=host,
                identity=identity,
                defaults={'created_by': created_by},
            )
            if is_default:
                cls.objects.filter(
                    host=host,
                    identity__protocol=identity.protocol,
                ).exclude(pk=binding.pk).update(is_default=False)
                binding.is_default = True
                binding.save(update_fields=('is_default',))
            return binding

    def to_view(self):
        return {
            'id': self.id,
            'host': {'id': self.host_id, 'name': self.host.name},
            'identity': self.identity.to_view(),
            'is_default': self.is_default,
            'created_at': self.created_at,
        }

    class Meta:
        db_table = 'asset_identity_bindings'
        unique_together = ('host', 'identity')
        ordering = ('host_id', '-is_default', 'id')


class AccessGrant(models.Model, ModelMixin):
    EFFECTS = (('allow', 'Allow'), ('deny', 'Deny'))
    STATUSES = (('active', 'Active'), ('revoked', 'Revoked'))
    ACTIONS = {
        'host.view',
        'ssh.connect',
        'file.read',
        'file.write',
        'file.distribute',
        'exec.run',
        'schedule.run',
        'monitor.run',
        'deploy.run',
        '*',
    }

    subject_user = models.ForeignKey(
        'account.User', models.CASCADE, related_name='asset_access_grants', null=True, blank=True
    )
    subject_role = models.ForeignKey(
        'account.Role', models.CASCADE, related_name='asset_access_grants', null=True, blank=True
    )
    host = models.ForeignKey('host.Host', models.CASCADE, related_name='access_grants', null=True, blank=True)
    group = models.ForeignKey('host.Group', models.CASCADE, related_name='access_grants', null=True, blank=True)
    identity = models.ForeignKey(Identity, models.PROTECT, related_name='access_grants', null=True, blank=True)
    effect = models.CharField(max_length=10, choices=EFFECTS, default='allow')
    actions = models.TextField(default='[]')
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default='active')
    reason = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.CharField(max_length=20, default=human_datetime)
    created_by = models.ForeignKey('account.User', models.PROTECT, related_name='+')
    revoked_at = models.CharField(max_length=20, null=True, blank=True)
    revoked_by = models.ForeignKey('account.User', models.PROTECT, related_name='+', null=True, blank=True)

    def clean(self):
        if bool(self.subject_user_id) == bool(self.subject_role_id):
            raise ValidationError('exactly one subject user or role is required')
        if bool(self.host_id) == bool(self.group_id):
            raise ValidationError('exactly one host or group resource is required')
        try:
            actions = json.loads(self.actions)
        except ValueError:
            raise ValidationError('grant actions must be valid JSON')
        if not isinstance(actions, list) or not actions:
            raise ValidationError('at least one grant action is required')
        invalid = set(actions).difference(self.ACTIONS)
        if invalid:
            raise ValidationError('unsupported grant actions: %s' % ', '.join(sorted(invalid)))
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValidationError('valid_until must be later than valid_from')
        if self.identity_id and self.group_id:
            raise ValidationError('identity-scoped grants require a single host resource')
        if self.identity_id and self.host_id:
            if not AssetIdentityBinding.objects.filter(host_id=self.host_id, identity_id=self.identity_id).exists():
                raise ValidationError('the selected identity is not bound to the host')

    @property
    def action_list(self):
        return json.loads(self.actions)

    def is_effective(self, at=None):
        at = at or datetime.now()
        if self.status != 'active':
            return False
        if self.valid_from and self.valid_from > at:
            return False
        if self.valid_until and self.valid_until <= at:
            return False
        return True

    def matches_action(self, action):
        actions = self.action_list
        return '*' in actions or action in actions

    def revoke(self, user):
        self.status = 'revoked'
        self.revoked_by = user
        self.revoked_at = human_datetime()
        self.save(update_fields=('status', 'revoked_by', 'revoked_at'))

    def to_view(self):
        subject = (
            {'type': 'user', 'id': self.subject_user_id, 'name': self.subject_user.username}
            if self.subject_user_id
            else {'type': 'role', 'id': self.subject_role_id, 'name': self.subject_role.name}
        )
        resource = (
            {'type': 'host', 'id': self.host_id, 'name': self.host.name}
            if self.host_id
            else {'type': 'group', 'id': self.group_id, 'name': self.group.name}
        )
        return {
            'id': self.id,
            'subject': subject,
            'resource': resource,
            'identity_id': self.identity_id,
            'effect': self.effect,
            'actions': self.action_list,
            'valid_from': self.valid_from,
            'valid_until': self.valid_until,
            'status': self.status,
            'reason': self.reason,
            'created_at': self.created_at,
            'revoked_at': self.revoked_at,
        }

    class Meta:
        db_table = 'asset_access_grants'
        ordering = ('-id',)
        index_together = (
            ('subject_user', 'status'),
            ('subject_role', 'status'),
            ('host', 'status'),
            ('group', 'status'),
        )

from datetime import datetime

from django.db.models import Q

from apps.host.models import Group
from .models import AccessGrant, AssetIdentityBinding


def _descendant_group_ids(root_ids):
    result = set(root_ids)
    pending = set(root_ids)
    while pending:
        children = set(Group.objects.filter(parent_id__in=pending).values_list('id', flat=True))
        pending = children.difference(result)
        result.update(pending)
    return result


def _grant_host_ids(grant, identity_id=None):
    if grant.host_id:
        host_ids = {grant.host_id}
    else:
        group_ids = _descendant_group_ids({grant.group_id})
        host_ids = set(
            Group.hosts.through.objects.filter(group_id__in=group_ids).values_list('host_id', flat=True)
        )

    if not grant.identity_id:
        return host_ids
    if identity_id is not None and grant.identity_id != identity_id:
        return set()

    bindings = AssetIdentityBinding.objects.filter(
        host_id__in=host_ids,
        identity_id=grant.identity_id,
    )
    # Callers that do not explicitly select an identity use the host's default
    # identity. An identity-scoped grant must therefore follow that same path.
    if identity_id is None:
        bindings = bindings.filter(is_default=True)
    return set(bindings.values_list('host_id', flat=True))


def get_access_adjustments(user, action, identity_id=None, at=None):
    at = at or datetime.now()
    role_ids = user.roles.values_list('id', flat=True)
    grants = AccessGrant.objects.filter(status='active').filter(
        Q(subject_user_id=user.id) | Q(subject_role_id__in=role_ids)
    ).select_related('group')

    allowed = set()
    denied = set()
    for grant in grants:
        matches = grant.matches_action(action)
        # An allow for an operational action must make the target discoverable
        # in host pickers. A deny for one action must not accidentally hide the
        # host from unrelated allowed operations.
        if action == 'host.view' and grant.effect == 'allow' and grant.action_list:
            matches = True
        if not grant.is_effective(at) or not matches:
            continue
        host_ids = _grant_host_ids(grant, identity_id=identity_id)
        if grant.effect == 'deny':
            denied.update(host_ids)
        else:
            allowed.update(host_ids)
    return allowed, denied


def apply_access_grants(user, legacy_host_ids, action='host.view', identity_id=None, at=None):
    allowed, denied = get_access_adjustments(
        user,
        action=action,
        identity_id=identity_id,
        at=at,
    )
    return set(legacy_host_ids).union(allowed).difference(denied)

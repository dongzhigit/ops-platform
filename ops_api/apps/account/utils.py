# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from apps.host.models import Group
import re


def get_host_perms(user, action='host.view', identity_id=None):
    ids = sub_ids = set(user.group_perms)
    while sub_ids:
        sub_ids = [x.id for x in Group.objects.filter(parent_id__in=sub_ids)]
        ids.update(sub_ids)
    legacy_host_ids = set(x.host_id for x in Group.hosts.through.objects.filter(group_id__in=ids))
    from apps.assets.permissions import apply_access_grants
    return apply_access_grants(
        user,
        legacy_host_ids,
        action=action,
        identity_id=identity_id,
    )


def has_host_perm(user, target, action='host.view', identity_id=None):
    if user.is_supper:
        return True
    host_ids = get_host_perms(user, action=action, identity_id=identity_id)
    if isinstance(target, (list, set, tuple)):
        try:
            targets = {int(item) for item in target}
        except (TypeError, ValueError):
            return False
        return targets.issubset(host_ids)
    return int(target) in host_ids


def verify_password(password):
    if len(password) < 8:
        return False
    if not all(map(lambda x: re.findall(x, password), ['[0-9]', '[a-z]', '[A-Z]'])):
        return False
    return True

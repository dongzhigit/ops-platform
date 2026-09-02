import json

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from libs import AdminView, Argument, JsonParser, human_datetime, json_response
from apps.account.models import Role, User
from apps.host.models import Group, Host
from .models import AccessGrant, AssetIdentityBinding, Credential, Identity


def _validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(
            '%s: %s' % (field, ', '.join(messages))
            for field, messages in exc.message_dict.items()
        )
    return '; '.join(exc.messages)


def _optional_datetime(value):
    if not value:
        return None
    result = parse_datetime(value)
    if result is None:
        return value
    if timezone.is_aware(result):
        result = timezone.make_naive(result)
    return result


class CredentialView(AdminView):
    def get(self, request):
        credentials = Credential.objects.all()
        return json_response([item.to_view() for item in credentials])

    def post(self, request):
        form, error = JsonParser(
            Argument('id', required=False),
            Argument('name', help='请输入凭据名称'),
            Argument('type', filter=lambda x: x in dict(Credential.TYPES), help='不支持的凭据类型'),
            Argument('secret', required=False),
            Argument('description', required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)

        credential = Credential.objects.filter(pk=form.id).first() if form.id else None
        if form.id and credential is None:
            return json_response(error='未找到指定凭据')
        if credential is None and not form.secret:
            return json_response(error='新建凭据必须提供密钥内容')
        if credential and credential.type != form.type and credential.identities.exists():
            return json_response(error='已有身份使用该凭据，不能修改凭据类型')

        try:
            if credential is None:
                credential = Credential(
                    name=form.name,
                    type=form.type,
                    description=form.description,
                    created_by=request.user,
                )
            else:
                credential.name = form.name
                credential.type = form.type
                credential.description = form.description
                credential.updated_by = request.user
                credential.updated_at = human_datetime()
            if form.secret:
                credential.set_secret(form.secret)
            credential.full_clean()
            credential.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))
        return json_response(credential.to_view())

    def delete(self, request):
        form, error = JsonParser(Argument('id', help='请指定凭据')).parse(request.GET)
        if error:
            return json_response(error=error)
        credential = Credential.objects.filter(pk=form.id).first()
        if not credential:
            return json_response(error='未找到指定凭据')
        credential.is_active = False
        credential.updated_by = request.user
        credential.updated_at = human_datetime()
        credential.save(update_fields=('is_active', 'updated_by', 'updated_at'))
        return json_response()


class IdentityView(AdminView):
    def get(self, request):
        identities = Identity.objects.select_related('credential')
        return json_response([item.to_view() for item in identities])

    def post(self, request):
        form, error = JsonParser(
            Argument('id', type=int, required=False),
            Argument('name', help='请输入身份名称'),
            Argument('protocol', filter=lambda x: x in dict(Identity.PROTOCOLS), help='不支持的连接协议'),
            Argument('username', handler=str.strip, help='请输入登录用户名'),
            Argument('credential_id', help='请选择凭据'),
            Argument('privilege_mode', default='none', filter=lambda x: x in dict(Identity.PRIVILEGE_MODES)),
            Argument('constraints', type=dict, default={}, handler=json.dumps),
            Argument('description', required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        credential = Credential.objects.filter(pk=form.pop('credential_id'), is_active=True).first()
        if not credential:
            return json_response(error='未找到可用凭据')

        identity = Identity.objects.filter(pk=form.id).first() if form.id else None
        if form.id and identity is None:
            return json_response(error='未找到指定身份')
        form.pop('id')
        try:
            if identity is None:
                identity = Identity(created_by=request.user, credential=credential, **form)
            else:
                for key, value in form.items():
                    setattr(identity, key, value)
                identity.credential = credential
                identity.updated_by = request.user
                identity.updated_at = human_datetime()
            identity.full_clean()
            identity.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))
        return json_response(identity.to_view())

    def delete(self, request):
        form, error = JsonParser(Argument('id', type=int, help='请指定身份')).parse(request.GET)
        if error:
            return json_response(error=error)
        identity = Identity.objects.filter(pk=form.id).first()
        if not identity:
            return json_response(error='未找到指定身份')
        if identity.asset_bindings.exists():
            return json_response(error='该身份仍绑定资产，不能停用')
        identity.is_active = False
        identity.updated_by = request.user
        identity.updated_at = human_datetime()
        identity.save(update_fields=('is_active', 'updated_by', 'updated_at'))
        return json_response()


class BindingView(AdminView):
    def get(self, request):
        bindings = AssetIdentityBinding.objects.select_related(
            'host', 'identity', 'identity__credential'
        )
        host_id = request.GET.get('host_id')
        if host_id:
            bindings = bindings.filter(host_id=host_id)
        return json_response([item.to_view() for item in bindings])

    def post(self, request):
        form, error = JsonParser(
            Argument('host_id', type=int, help='请选择主机'),
            Argument('identity_id', type=int, help='请选择身份'),
            Argument('is_default', type=bool, default=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)
        host = Host.objects.filter(pk=form.host_id).first()
        identity = Identity.objects.select_related('credential').filter(
            pk=form.identity_id,
            is_active=True,
            credential__is_active=True,
        ).first()
        if not host or not identity:
            return json_response(error='未找到可用主机或身份')
        binding = AssetIdentityBinding.bind(
            host=host,
            identity=identity,
            created_by=request.user,
            is_default=form.is_default,
        )
        binding.host = host
        binding.identity = identity
        return json_response(binding.to_view())

    def delete(self, request):
        form, error = JsonParser(Argument('id', type=int, help='请指定绑定关系')).parse(request.GET)
        if error:
            return json_response(error=error)
        AssetIdentityBinding.objects.filter(pk=form.id).delete()
        return json_response()


class GrantView(AdminView):
    def get(self, request):
        grants = AccessGrant.objects.select_related(
            'subject_user', 'subject_role', 'host', 'group', 'identity'
        )
        return json_response([item.to_view() for item in grants])

    def post(self, request):
        form, error = JsonParser(
            Argument('subject_type', filter=lambda x: x in ('user', 'role'), help='不支持的授权主体'),
            Argument('subject_id', type=int, help='请选择授权主体'),
            Argument('resource_type', filter=lambda x: x in ('host', 'group'), help='不支持的授权资源'),
            Argument('resource_id', type=int, help='请选择授权资源'),
            Argument('identity_id', type=int, required=False),
            Argument('effect', default='allow', filter=lambda x: x in dict(AccessGrant.EFFECTS)),
            Argument('actions', type=list, filter=lambda x: bool(x), handler=json.dumps, help='请选择授权动作'),
            Argument('valid_from', required=False, handler=_optional_datetime, help='生效时间格式错误'),
            Argument('valid_until', required=False, handler=_optional_datetime, help='失效时间格式错误'),
            Argument('reason', required=False),
        ).parse(request.body)
        if error:
            return json_response(error=error)

        subject_user = User.objects.filter(pk=form.subject_id).first() if form.subject_type == 'user' else None
        subject_role = Role.objects.filter(pk=form.subject_id).first() if form.subject_type == 'role' else None
        host = Host.objects.filter(pk=form.resource_id).first() if form.resource_type == 'host' else None
        group = Group.objects.filter(pk=form.resource_id).first() if form.resource_type == 'group' else None
        identity = Identity.objects.filter(pk=form.identity_id, is_active=True).first() if form.identity_id else None
        if not (subject_user or subject_role):
            return json_response(error='未找到授权主体')
        if not (host or group):
            return json_response(error='未找到授权资源')
        if form.identity_id and not identity:
            return json_response(error='未找到可用身份')
        if identity and group:
            return json_response(error='身份级授权当前仅支持单台主机')

        grant = AccessGrant(
            subject_user=subject_user,
            subject_role=subject_role,
            host=host,
            group=group,
            identity=identity,
            effect=form.effect,
            actions=form.actions,
            valid_from=form.valid_from,
            valid_until=form.valid_until,
            reason=form.reason,
            created_by=request.user,
        )
        try:
            grant.full_clean()
            grant.save()
        except ValidationError as exc:
            return json_response(error=_validation_error(exc))
        return json_response(grant.to_view())

    def patch(self, request):
        form, error = JsonParser(Argument('id', type=int, help='请指定授权记录')).parse(request.body)
        if error:
            return json_response(error=error)
        grant = AccessGrant.objects.filter(pk=form.id, status='active').first()
        if not grant:
            return json_response(error='未找到有效授权记录')
        grant.revoke(request.user)
        return json_response()

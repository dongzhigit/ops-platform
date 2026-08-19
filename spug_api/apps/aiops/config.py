import hmac
from datetime import datetime

from django.conf import settings
from django.db import transaction

from apps.assets.encryption import CredentialEncryptionError
from spug.env_settings import validate_model_name, validate_service_url

from .models import AIProviderConfig


class AIConfigError(ValueError):
    pass


FIELDS = (
    'enabled', 'base_url', 'model', 'json_mode', 'request_timeout',
    'max_hosts', 'knowledge_limit', 'max_output_tokens',
    'max_response_bytes', 'rate_limit_per_minute',
)


def _environment_config():
    return {
        'enabled': settings.SPUG_AIOPS_ENABLED,
        'base_url': settings.SPUG_AIOPS_BASE_URL,
        'model': settings.SPUG_AIOPS_MODEL,
        'json_mode': settings.SPUG_AIOPS_JSON_MODE,
        'request_timeout': settings.SPUG_AIOPS_REQUEST_TIMEOUT,
        'max_hosts': settings.SPUG_AIOPS_MAX_HOSTS,
        'knowledge_limit': settings.SPUG_AIOPS_KNOWLEDGE_LIMIT,
        'max_output_tokens': settings.SPUG_AIOPS_MAX_OUTPUT_TOKENS,
        'max_response_bytes': settings.SPUG_AIOPS_MAX_RESPONSE_BYTES,
        'rate_limit_per_minute': settings.SPUG_AIOPS_RATE_LIMIT_PER_MINUTE,
        'api_key_configured': bool(settings.SPUG_AIOPS_API_KEY),
        'api_key_source': 'environment' if settings.SPUG_AIOPS_API_KEY else None,
        'source': 'environment',
        'updated_at': None,
        'updated_by': None,
    }


def get_runtime_config(include_secret=False):
    result = _environment_config()
    provider = AIProviderConfig.objects.select_related('updated_by').filter(pk=1).first()
    if provider:
        for field in FIELDS:
            result[field] = getattr(provider, field)
        result.update({
            'source': 'database',
            'api_key_configured': provider.has_api_key or bool(settings.SPUG_AIOPS_API_KEY),
            'api_key_source': (
                'database' if provider.has_api_key
                else ('environment' if settings.SPUG_AIOPS_API_KEY else None)
            ),
            'updated_at': provider.updated_at,
            'updated_by': {
                'id': provider.updated_by_id,
                'name': provider.updated_by.nickname,
            },
        })
    if include_secret:
        try:
            result['api_key'] = (
                provider.reveal_api_key()
                if provider and provider.has_api_key
                else settings.SPUG_AIOPS_API_KEY
            )
        except CredentialEncryptionError:
            raise AIConfigError('模型 API Key 无法解密，请检查主密钥配置')
    return result


def _validate_independent_key(value):
    protected = (
        settings.SECRET_KEY,
        settings.SPUG_CREDENTIAL_MASTER_KEY,
        settings.SPUG_AUDIT_SIGNING_KEY,
        settings.SPUG_GUACAMOLE_JSON_SECRET_KEY,
        settings.SPUG_PROMETHEUS_DISCOVERY_TOKEN,
        settings.SPUG_ALERTMANAGER_WEBHOOK_TOKEN,
    )
    if any(item and hmac.compare_digest(value, item) for item in protected):
        raise AIConfigError('模型 API Key 必须与应用、凭据、审计和监控密钥相互独立')


def save_runtime_config(*, actor, values):
    try:
        base_url = validate_service_url(values['base_url'], name='模型服务地址')
        model = validate_model_name(values['model'], name='模型名称', required=values['enabled'])
    except ValueError as exc:
        raise AIConfigError(str(exc))
    if settings.SPUG_ENV == 'production' and not base_url.startswith('https://'):
        raise AIConfigError('生产模式下模型服务地址必须使用 HTTPS')

    with transaction.atomic():
        defaults = _environment_config()
        provider, _ = AIProviderConfig.objects.get_or_create(
            pk=1,
            defaults={
                'enabled': defaults['enabled'],
                'base_url': defaults['base_url'],
                'model': defaults['model'],
                'json_mode': defaults['json_mode'],
                'request_timeout': defaults['request_timeout'],
                'max_hosts': defaults['max_hosts'],
                'knowledge_limit': defaults['knowledge_limit'],
                'max_output_tokens': defaults['max_output_tokens'],
                'max_response_bytes': defaults['max_response_bytes'],
                'rate_limit_per_minute': defaults['rate_limit_per_minute'],
                'updated_by': actor,
            },
        )
        provider = AIProviderConfig.objects.select_for_update().get(pk=provider.pk)
        for field in FIELDS:
            if field in ('base_url', 'model'):
                continue
            setattr(provider, field, values[field])
        provider.base_url = base_url
        provider.model = model

        api_key = str(values.get('api_key') or '').strip()
        clear_api_key = bool(values.get('clear_api_key'))
        if api_key and clear_api_key:
            raise AIConfigError('不能同时更新并清除模型 API Key')
        if api_key:
            _validate_independent_key(api_key)
            provider.set_api_key(api_key)
        elif clear_api_key:
            provider.api_key_data = None

        effective_key = (
            provider.reveal_api_key() if provider.has_api_key
            else settings.SPUG_AIOPS_API_KEY
        )
        if provider.enabled and not effective_key:
            raise AIConfigError('启用 AI 运维前必须配置模型 API Key')
        provider.updated_by = actor
        provider.updated_at = datetime.now()
        provider.full_clean()
        provider.save()
    return get_runtime_config()

import os
import base64
import binascii
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit


TRUE_VALUES = {'1', 'true', 'yes', 'on'}
FALSE_VALUES = {'0', 'false', 'no', 'off'}


def parse_bool(value, *, name):
    value = str(value).strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError(f'{name} must be one of: true, false, 1, 0, yes, no, on, off')


def env_bool(name, default=False, environ=None):
    if environ is None:
        environ = os.environ
    value = environ.get(name)
    if value is None or str(value).strip() == '':
        return default
    return parse_bool(value, name=name)


def env_list(name, default=None, environ=None):
    if environ is None:
        environ = os.environ
    value = environ.get(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(',') if item.strip()]


def env_int(name, default, minimum, maximum, environ=None):
    if environ is None:
        environ = os.environ
    value = environ.get(name, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError('%s must be an integer' % name)
    if not minimum <= value <= maximum:
        raise ValueError('%s must be between %s and %s' % (name, minimum, maximum))
    return value


def validate_master_key(value, *, name='SPUG_CREDENTIAL_MASTER_KEY'):
    try:
        decoded = base64.b64decode(str(value).encode('ascii'), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise ValueError('%s must be a valid base64 value' % name)
    if len(decoded) != 32:
        raise ValueError('%s must decode to exactly 32 bytes' % name)
    return str(value)


def development_master_key(secret_key):
    material = ('spug-development-credential-key:' + str(secret_key)).encode('utf-8')
    return base64.b64encode(hashlib.sha256(material).digest()).decode('ascii')


def development_audit_signing_key(secret_key):
    material = ('spug-development-audit-key:' + str(secret_key)).encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def development_guacamole_key(secret_key):
    material = ('spug-development-guacamole-key:' + str(secret_key)).encode('utf-8')
    return hashlib.sha256(material).digest()[:16].hex()


def development_service_token(secret_key, scope):
    material = ('spug-development-%s-token:%s' % (scope, secret_key)).encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def load_secret_value(environ, name):
    direct = str(environ.get(name, '')).strip()
    file_path = str(environ.get(name + '_FILE', '')).strip()
    if direct and file_path:
        raise ValueError('%s and %s_FILE cannot both be configured' % (name, name))
    if not file_path:
        return direct
    try:
        with open(file_path, 'r') as stream:
            return stream.read().strip()
    except OSError as exc:
        raise ValueError('cannot read %s_FILE: %s' % (name, exc))


def validate_service_token(value, *, name):
    value = str(value).strip()
    if len(value) < 32:
        raise ValueError('%s must contain at least 32 characters' % name)
    return value


def validate_service_url(value, *, name):
    value = str(value).strip().rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('%s must be an absolute HTTP(S) URL' % name)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('%s cannot contain credentials, a query string or fragment' % name)
    return value


def is_private_http_service_url(value):
    """Return whether an HTTP URL is confined to a well-known private target.

    Hostnames are intentionally not resolved here: accepting an arbitrary DNS
    name based on its current answer would make this check vulnerable to DNS
    rebinding. Docker Desktop's two stable host aliases are explicit exceptions.
    """
    parsed = urlsplit(str(value).strip())
    if parsed.scheme != 'http':
        return False
    hostname = (parsed.hostname or '').strip().lower().rstrip('.')
    if hostname in {'localhost', 'host.docker.internal', 'gateway.docker.internal'}:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    networks = (
        ipaddress.ip_network('10.0.0.0/8'),
        ipaddress.ip_network('172.16.0.0/12'),
        ipaddress.ip_network('192.168.0.0/16'),
        ipaddress.ip_network('127.0.0.0/8'),
        ipaddress.ip_network('169.254.0.0/16'),
        ipaddress.ip_network('::1/128'),
        ipaddress.ip_network('fc00::/7'),
        ipaddress.ip_network('fe80::/10'),
    )
    return any(address in network for network in networks if address.version == network.version)


def validate_model_name(value, *, name='SPUG_AIOPS_MODEL', required=False):
    value = str(value or '').strip()
    if required and not value:
        raise ValueError('%s is required when AI operations is enabled' % name)
    if value and (
            len(value) > 100
            or not all(char.isalnum() or char in '._:/-' for char in value)):
        raise ValueError('%s contains unsupported characters or is too long' % name)
    return value


def validate_guacamole_key(value, *, name='SPUG_GUACAMOLE_JSON_SECRET_KEY'):
    value = str(value).strip().lower()
    if len(value) != 32:
        raise ValueError('%s must contain exactly 32 hexadecimal characters' % name)
    try:
        bytes.fromhex(value)
    except ValueError:
        raise ValueError('%s must contain exactly 32 hexadecimal characters' % name)
    return value


def validate_public_path(value, *, name='SPUG_GUACAMOLE_PUBLIC_URL'):
    value = str(value).strip()
    parsed = urlsplit(value)
    if not value.startswith('/') or value.startswith('//') or parsed.scheme or parsed.netloc:
        raise ValueError('%s must be a same-origin absolute path' % name)
    if parsed.query or parsed.fragment:
        raise ValueError('%s cannot contain a query string or fragment' % name)
    return value.rstrip('/') or '/'


def load_credential_keyring(environ, fallback_key):
    keyring = {'primary': fallback_key}
    raw = str(environ.get('SPUG_CREDENTIAL_KEYRING', '')).strip()
    if raw:
        try:
            values = json.loads(raw)
        except ValueError:
            raise ValueError('SPUG_CREDENTIAL_KEYRING must be a JSON object')
        if not isinstance(values, dict) or not values:
            raise ValueError('SPUG_CREDENTIAL_KEYRING must be a non-empty JSON object')
        for key_id, value in values.items():
            key_id = str(key_id).strip()
            if not key_id or len(key_id) > 50:
                raise ValueError('credential key IDs must contain 1 to 50 characters')
            keyring[key_id] = validate_master_key(
                value,
                name='SPUG_CREDENTIAL_KEYRING[%s]' % key_id,
            )
    primary_key_id = str(environ.get('SPUG_CREDENTIAL_PRIMARY_KEY_ID', 'primary')).strip()
    if primary_key_id not in keyring:
        raise ValueError('SPUG_CREDENTIAL_PRIMARY_KEY_ID is not present in SPUG_CREDENTIAL_KEYRING')
    return keyring, primary_key_id


def load_security_settings(*, default_secret, default_debug, default_allowed_hosts, environ=None):
    if environ is None:
        environ = os.environ
    environment = environ.get('SPUG_ENV', 'development').strip().lower()
    if environment not in {'development', 'test', 'production'}:
        raise ValueError('SPUG_ENV must be development, test or production')

    is_production = environment == 'production'
    provided_secret = str(environ.get('SPUG_SECRET_KEY', '')).strip()
    secret_key = provided_secret or default_secret
    provided_master_key = str(environ.get('SPUG_CREDENTIAL_MASTER_KEY', '')).strip()
    provided_audit_key = str(environ.get('SPUG_AUDIT_SIGNING_KEY', '')).strip()
    provided_guacamole_key = str(environ.get('SPUG_GUACAMOLE_JSON_SECRET_KEY', '')).strip()
    provided_discovery_token = load_secret_value(
        environ, 'SPUG_PROMETHEUS_DISCOVERY_TOKEN'
    )
    provided_webhook_token = load_secret_value(
        environ, 'SPUG_ALERTMANAGER_WEBHOOK_TOKEN'
    )
    provided_aiops_key = load_secret_value(environ, 'SPUG_AIOPS_API_KEY')
    debug = env_bool('SPUG_DEBUG', default=False if is_production else default_debug, environ=environ)
    allowed_hosts = env_list('SPUG_ALLOWED_HOSTS', default_allowed_hosts, environ=environ)
    remote_gateway_enabled = env_bool(
        'SPUG_REMOTE_GATEWAY_ENABLED', default=not is_production, environ=environ
    )
    observability_enabled = env_bool(
        'SPUG_OBSERVABILITY_ENABLED', default=not is_production, environ=environ
    )
    aiops_enabled = env_bool(
        'SPUG_AIOPS_ENABLED', default=False, environ=environ
    )
    aiops_api_format = environ.get(
        'SPUG_AIOPS_API_FORMAT', 'openai'
    ).strip().lower()
    if aiops_api_format not in {'openai', 'anthropic'}:
        raise ValueError('SPUG_AIOPS_API_FORMAT must be openai or anthropic')
    aiops_model = validate_model_name(
        environ.get('SPUG_AIOPS_MODEL', ''), required=aiops_enabled
    )
    aiops_base_url = validate_service_url(
        environ.get('SPUG_AIOPS_BASE_URL', 'http://127.0.0.1:11434/v1'),
        name='SPUG_AIOPS_BASE_URL',
    )

    if is_production:
        if not provided_secret:
            raise ValueError('SPUG_SECRET_KEY is required when SPUG_ENV=production')
        if len(provided_secret) < 32:
            raise ValueError('SPUG_SECRET_KEY must contain at least 32 characters in production')
        if debug:
            raise ValueError('SPUG_DEBUG must be false when SPUG_ENV=production')
        if not allowed_hosts:
            raise ValueError('SPUG_ALLOWED_HOSTS is required when SPUG_ENV=production')
        if '*' in allowed_hosts:
            raise ValueError('SPUG_ALLOWED_HOSTS cannot contain * in production')
        if not provided_master_key:
            raise ValueError('SPUG_CREDENTIAL_MASTER_KEY is required when SPUG_ENV=production')
        if len(provided_audit_key) < 32:
            raise ValueError('SPUG_AUDIT_SIGNING_KEY must contain at least 32 characters in production')
        if provided_audit_key in (provided_secret, provided_master_key):
            raise ValueError('SPUG_AUDIT_SIGNING_KEY must be independent from other production keys')
        if remote_gateway_enabled and not provided_guacamole_key:
            raise ValueError(
                'SPUG_GUACAMOLE_JSON_SECRET_KEY is required when the production remote gateway is enabled'
            )
        if provided_guacamole_key and provided_guacamole_key in (
                provided_secret, provided_master_key, provided_audit_key):
            raise ValueError('SPUG_GUACAMOLE_JSON_SECRET_KEY must be an independent production key')
        if observability_enabled:
            if not provided_discovery_token:
                raise ValueError(
                    'SPUG_PROMETHEUS_DISCOVERY_TOKEN or its _FILE variant is required '
                    'when production observability is enabled'
                )
            if not provided_webhook_token:
                raise ValueError(
                    'SPUG_ALERTMANAGER_WEBHOOK_TOKEN or its _FILE variant is required '
                    'when production observability is enabled'
                )
            independent_values = {
                provided_secret,
                provided_master_key,
                provided_audit_key,
                provided_guacamole_key,
                provided_discovery_token,
                provided_webhook_token,
            }
            if len(independent_values) != 6:
                raise ValueError('all production observability and application keys must be independent')
        if aiops_enabled:
            if not str(environ.get('SPUG_AIOPS_BASE_URL', '')).strip():
                raise ValueError(
                    'SPUG_AIOPS_BASE_URL is required when production AI operations is enabled'
                )
            if (
                    urlsplit(aiops_base_url).scheme != 'https'
                    and not is_private_http_service_url(aiops_base_url)):
                raise ValueError(
                    'SPUG_AIOPS_BASE_URL must use HTTPS unless it points to a loopback, '
                    'Docker-internal, or RFC1918 private address'
                )
            if not provided_aiops_key:
                raise ValueError(
                    'SPUG_AIOPS_API_KEY or its _FILE variant is required '
                    'when production AI operations is enabled'
                )
            if provided_aiops_key in {
                    provided_secret, provided_master_key, provided_audit_key,
                    provided_guacamole_key, provided_discovery_token,
                    provided_webhook_token}:
                raise ValueError('SPUG_AIOPS_API_KEY must be an independent production key')

    fallback_master_key = (
        validate_master_key(provided_master_key)
        if provided_master_key
        else development_master_key(secret_key)
    )
    credential_master_keys, credential_primary_key_id = load_credential_keyring(
        environ,
        fallback_master_key,
    )

    return {
        'environment': environment,
        'secret_key': secret_key,
        'debug': debug,
        'allowed_hosts': allowed_hosts,
        'credential_master_key': credential_master_keys[credential_primary_key_id],
        'credential_master_keys': credential_master_keys,
        'credential_primary_key_id': credential_primary_key_id,
        'audit_signing_key': provided_audit_key or development_audit_signing_key(secret_key),
        'csrf_trusted_origins': env_list('SPUG_CSRF_TRUSTED_ORIGINS', environ=environ),
        'secure_cookies': env_bool('SPUG_SECURE_COOKIES', default=is_production, environ=environ),
        'ssl_redirect': env_bool('SPUG_SSL_REDIRECT', default=False, environ=environ),
        'remote_gateway_enabled': remote_gateway_enabled,
        'observability_enabled': observability_enabled,
        'aiops_enabled': aiops_enabled,
        'aiops_api_format': aiops_api_format,
        'aiops_api_key': provided_aiops_key,
        'aiops_base_url': aiops_base_url,
        'aiops_model': aiops_model,
        'aiops_json_mode': env_bool(
            'SPUG_AIOPS_JSON_MODE', default=True, environ=environ
        ),
        'aiops_request_timeout': env_int(
            'SPUG_AIOPS_REQUEST_TIMEOUT', 30, 1, 120, environ=environ
        ),
        'aiops_max_hosts': env_int(
            'SPUG_AIOPS_MAX_HOSTS', 20, 1, 100, environ=environ
        ),
        'aiops_knowledge_limit': env_int(
            'SPUG_AIOPS_KNOWLEDGE_LIMIT', 8, 1, 20, environ=environ
        ),
        'aiops_max_output_tokens': env_int(
            'SPUG_AIOPS_MAX_OUTPUT_TOKENS', 2000, 256, 8192, environ=environ
        ),
        'aiops_rate_limit_per_minute': env_int(
            'SPUG_AIOPS_RATE_LIMIT_PER_MINUTE', 5, 1, 60, environ=environ
        ),
        'aiops_max_response_bytes': env_int(
            'SPUG_AIOPS_MAX_RESPONSE_BYTES', 1048576, 4096, 4194304,
            environ=environ,
        ),
        'guacamole_json_secret_key': (
            validate_guacamole_key(provided_guacamole_key)
            if provided_guacamole_key
            else development_guacamole_key(secret_key)
        ),
        'guacamole_public_url': validate_public_path(
            environ.get('SPUG_GUACAMOLE_PUBLIC_URL', '/guacamole/')
        ),
        'remote_ticket_ttl': env_int(
            'SPUG_REMOTE_TICKET_TTL', 60, 15, 300, environ=environ
        ),
        'guacamole_auth_ttl': env_int(
            'SPUG_GUACAMOLE_AUTH_TTL', 60, 15, 300, environ=environ
        ),
        'prometheus_discovery_token': validate_service_token(
            provided_discovery_token or development_service_token(secret_key, 'prometheus'),
            name='SPUG_PROMETHEUS_DISCOVERY_TOKEN',
        ),
        'alertmanager_webhook_token': validate_service_token(
            provided_webhook_token or development_service_token(secret_key, 'alertmanager'),
            name='SPUG_ALERTMANAGER_WEBHOOK_TOKEN',
        ),
        'prometheus_url': validate_service_url(
            environ.get('SPUG_PROMETHEUS_URL', 'http://127.0.0.1:9090'),
            name='SPUG_PROMETHEUS_URL',
        ),
        'alertmanager_url': validate_service_url(
            environ.get('SPUG_ALERTMANAGER_URL', 'http://127.0.0.1:9093'),
            name='SPUG_ALERTMANAGER_URL',
        ),
        'observability_request_timeout': env_int(
            'SPUG_OBSERVABILITY_REQUEST_TIMEOUT', 10, 1, 60, environ=environ
        ),
        'alertmanager_max_body': env_int(
            'SPUG_ALERTMANAGER_MAX_BODY', 524288, 1024, 5242880,
            environ=environ,
        ),
        'alert_retention_days': env_int(
            'SPUG_ALERT_RETENTION_DAYS', 90, 30, 3650, environ=environ
        ),
    }

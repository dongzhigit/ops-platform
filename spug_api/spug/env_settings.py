import os
import base64
import binascii
import hashlib
import json


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
    debug = env_bool('SPUG_DEBUG', default=False if is_production else default_debug, environ=environ)
    allowed_hosts = env_list('SPUG_ALLOWED_HOSTS', default_allowed_hosts, environ=environ)

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
    }

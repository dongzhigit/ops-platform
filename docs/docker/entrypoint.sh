#!/bin/bash
#
set -euo pipefail

require_env() {
    local name="$1"
    if [ -z "${!name:-}" ]; then
        echo "error: ${name} is required" >&2
        exit 1
    fi
}

require_env SPUG_SECRET_KEY
require_env SPUG_CREDENTIAL_MASTER_KEY
require_env SPUG_ALLOWED_HOSTS
require_env MYSQL_DATABASE
require_env MYSQL_USER
require_env MYSQL_PASSWORD
require_env MYSQL_HOST
require_env MYSQL_PORT

if [ "${#SPUG_SECRET_KEY}" -lt 32 ]; then
    echo "error: SPUG_SECRET_KEY must contain at least 32 characters" >&2
    exit 1
fi

if ! python3 -c 'import base64, os; assert len(base64.b64decode(os.environ["SPUG_CREDENTIAL_MASTER_KEY"], validate=True)) == 32' 2>/dev/null; then
    echo "error: SPUG_CREDENTIAL_MASTER_KEY must be valid base64 that decodes to 32 bytes" >&2
    exit 1
fi

if [[ "$SPUG_ALLOWED_HOSTS" == *"*"* ]]; then
    echo "error: SPUG_ALLOWED_HOSTS cannot contain * in production" >&2
    exit 1
fi

if [ -e /root/.bashrc ]; then
    set +u
    source /root/.bashrc
    set -u
fi

cat > /data/spug/spug_api/spug/overrides.py << 'PYTHON'
import os


DEBUG = os.environ.get('SPUG_DEBUG', 'false').strip().lower() in ('1', 'true', 'yes', 'on')
ALLOWED_HOSTS = [x.strip() for x in os.environ['SPUG_ALLOWED_HOSTS'].split(',') if x.strip()]
SECRET_KEY = os.environ['SPUG_SECRET_KEY']
if DEBUG:
    raise RuntimeError('SPUG_DEBUG must be false in the production container')
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise RuntimeError('SPUG_ALLOWED_HOSTS must contain explicit production hosts')
if len(SECRET_KEY) < 32:
    raise RuntimeError('SPUG_SECRET_KEY must contain at least 32 characters')
CSRF_TRUSTED_ORIGINS = [
    x.strip()
    for x in os.environ.get('SPUG_CSRF_TRUSTED_ORIGINS', '').split(',')
    if x.strip()
]
SESSION_COOKIE_SECURE = os.environ.get('SPUG_SECURE_COOKIES', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = os.environ.get('SPUG_SSL_REDIRECT', 'false').strip().lower() in ('1', 'true', 'yes', 'on')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

DATABASES = {
    'default': {
        'ATOMIC_REQUESTS': True,
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('MYSQL_DATABASE'),
        'USER': os.environ.get('MYSQL_USER'),
        'PASSWORD': os.environ.get('MYSQL_PASSWORD'),
        'HOST': os.environ.get('MYSQL_HOST'),
        'PORT': os.environ.get('MYSQL_PORT'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'sql_mode': 'STRICT_TRANS_TABLES',
        }
    }
}
PYTHON

cd /data/spug/spug_api
python3 manage.py updatedb

exec supervisord -c /etc/supervisord.conf

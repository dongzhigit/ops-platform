from spug.settings import *
import os


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

if os.environ.get('SPUG_TEST_MYSQL_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'HOST': os.environ['SPUG_TEST_MYSQL_HOST'],
            'PORT': int(os.environ.get('SPUG_TEST_MYSQL_PORT', '3306')),
            'NAME': os.environ.get('SPUG_TEST_MYSQL_DATABASE', 'spug_test'),
            'USER': os.environ.get('SPUG_TEST_MYSQL_USER', 'spug'),
            'PASSWORD': os.environ['SPUG_TEST_MYSQL_PASSWORD'],
            'OPTIONS': {'charset': 'utf8mb4'},
        }
    }

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}

# Keep model-focused unit tests fast and independent of MySQL. The checked-in
# migration graph is validated separately with ``migration_settings`` against
# an empty MariaDB database.
MIGRATION_MODULES = {
    item.split('.')[-1]: None
    for item in INSTALLED_APPS
    if item.startswith('apps.')
}

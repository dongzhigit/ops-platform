"""Database-backed settings used to validate the repository migration graph."""
from ops_platform.settings import *
import os


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

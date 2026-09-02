import os
from datetime import datetime, timedelta
from unittest import TestCase, skipUnless

import requests

from apps.gateway.services import encrypt_guacamole_document


GUACAMOLE_URL = os.environ.get('SPUG_TEST_GUACAMOLE_URL')
GUACAMOLE_KEY = os.environ.get('SPUG_TEST_GUACAMOLE_KEY')


@skipUnless(
    GUACAMOLE_URL and GUACAMOLE_KEY,
    'set SPUG_TEST_GUACAMOLE_URL and SPUG_TEST_GUACAMOLE_KEY to run Guacamole integration tests',
)
class GuacamoleJsonAuthIntegrationTest(TestCase):
    def test_official_container_accepts_generated_json_auth_data(self):
        document = {
            'username': 'spug-integration-test',
            'expires': int((datetime.now() + timedelta(seconds=60)).timestamp() * 1000),
            'connections': {},
        }
        data = encrypt_guacamole_document(document, GUACAMOLE_KEY)

        response = requests.post(
            GUACAMOLE_URL.rstrip('/') + '/api/tokens',
            data={'data': data},
            timeout=15,
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result.get('authToken'))
        self.assertEqual(result.get('username'), document['username'])

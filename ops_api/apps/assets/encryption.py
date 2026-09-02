import base64
import binascii
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings


class CredentialEncryptionError(ValueError):
    pass


def _decode(value, name):
    try:
        return base64.b64decode(value.encode('ascii'), validate=True)
    except (AttributeError, UnicodeEncodeError, binascii.Error, ValueError):
        raise CredentialEncryptionError('%s is not valid base64' % name)


def _encode(value):
    return base64.b64encode(value).decode('ascii')


class EnvelopeCipher(object):
    VERSION = 1
    MAX_SECRET_BYTES = 1024 * 1024

    def __init__(self, master_key, key_id='primary'):
        key = _decode(master_key, 'master key')
        if len(key) != 32:
            raise CredentialEncryptionError('master key must decode to exactly 32 bytes')
        self.master_key = key
        self.key_id = key_id

    def encrypt(self, secret, record_id):
        if isinstance(secret, str):
            secret = secret.encode('utf-8')
        if not isinstance(secret, bytes) or not secret:
            raise CredentialEncryptionError('credential secret must not be empty')
        if len(secret) > self.MAX_SECRET_BYTES:
            raise CredentialEncryptionError('credential secret exceeds the 1 MiB limit')

        data_key = os.urandom(32)
        wrap_nonce = os.urandom(12)
        data_nonce = os.urandom(12)
        wrap_aad = self._aad('key', record_id)
        data_aad = self._aad('data', record_id)

        return json.dumps({
            'version': self.VERSION,
            'wrap_nonce': _encode(wrap_nonce),
            'wrapped_key': _encode(AESGCM(self.master_key).encrypt(wrap_nonce, data_key, wrap_aad)),
            'data_nonce': _encode(data_nonce),
            'ciphertext': _encode(AESGCM(data_key).encrypt(data_nonce, secret, data_aad)),
        }, sort_keys=True, separators=(',', ':'))

    def decrypt(self, payload, record_id):
        try:
            data = json.loads(payload)
            if data.get('version') != self.VERSION:
                raise CredentialEncryptionError('unsupported credential encryption version')
            wrap_nonce = _decode(data['wrap_nonce'], 'wrap nonce')
            wrapped_key = _decode(data['wrapped_key'], 'wrapped key')
            data_nonce = _decode(data['data_nonce'], 'data nonce')
            ciphertext = _decode(data['ciphertext'], 'ciphertext')
            data_key = AESGCM(self.master_key).decrypt(
                wrap_nonce,
                wrapped_key,
                self._aad('key', record_id),
            )
            return AESGCM(data_key).decrypt(
                data_nonce,
                ciphertext,
                self._aad('data', record_id),
            )
        except CredentialEncryptionError:
            raise
        except Exception as exc:
            raise CredentialEncryptionError('credential decryption failed') from exc

    def _aad(self, purpose, record_id):
        value = 'spug:credential:%s:%s:v%s' % (record_id, purpose, self.VERSION)
        return value.encode('utf-8')


def get_default_cipher(key_id='primary'):
    try:
        master_key = settings.SPUG_CREDENTIAL_MASTER_KEYS[key_id]
    except KeyError:
        raise CredentialEncryptionError('credential master key %r is not configured' % key_id)
    return EnvelopeCipher(master_key, key_id=key_id)

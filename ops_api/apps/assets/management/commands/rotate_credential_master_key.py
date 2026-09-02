import hmac

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.assets.models import Credential
from apps.aiops.models import AIProviderConfig


class Command(BaseCommand):
    help = '将凭据重新包裹到已配置的目标主密钥；默认只做预检查'

    def add_arguments(self, parser):
        parser.add_argument('--target-key-id', required=True, help='SPUG_CREDENTIAL_KEYRING 中的目标密钥 ID')
        parser.add_argument('--execute', action='store_true', help='执行轮换；省略时仅显示影响范围')

    def handle(self, *args, **options):
        target_key_id = options['target_key_id']
        if target_key_id not in settings.SPUG_CREDENTIAL_MASTER_KEYS:
            raise CommandError('目标密钥 ID 未配置')

        credentials = Credential.objects.exclude(key_id=target_key_id).order_by('id')
        ai_configs = AIProviderConfig.objects.exclude(
            api_key_data__isnull=True,
        ).exclude(api_key_data='').exclude(key_id=target_key_id).order_by('id')
        count = credentials.count()
        ai_count = ai_configs.count()
        if not options['execute']:
            self.stdout.write(
                '预检查完成：%s 条凭据、%s 条 AI 模型密钥需要轮换到 %s；'
                '添加 --execute 后执行' % (count, ai_count, target_key_id)
            )
            return

        rotated = 0
        with transaction.atomic():
            for credential in credentials.select_for_update():
                secret = credential.reveal_secret()
                credential.set_secret(secret, key_id=target_key_id)
                if not hmac.compare_digest(credential.reveal_secret(), secret):
                    raise CommandError('凭据 %s 轮换后回读验证失败' % credential.id)
                credential.save(update_fields=('key_id', 'secret_data', 'updated_at'))
                rotated += 1
            ai_rotated = 0
            for config in ai_configs.select_for_update():
                secret = config.reveal_api_key()
                config.set_api_key(secret, key_id=target_key_id)
                if not hmac.compare_digest(config.reveal_api_key(), secret):
                    raise CommandError('AI 模型密钥轮换后回读验证失败')
                config.save(update_fields=('key_id', 'api_key_data'))
                ai_rotated += 1
        self.stdout.write(self.style.SUCCESS(
            '已轮换 %s 条凭据、%s 条 AI 模型密钥到 %s'
            % (rotated, ai_rotated, target_key_id)
        ))

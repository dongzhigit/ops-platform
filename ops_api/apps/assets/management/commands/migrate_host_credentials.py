import hmac

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.assets.models import AssetIdentityBinding, Credential, Identity
from apps.host.models import Host


class Command(BaseCommand):
    help = '将 Host.pkey 明文私钥迁移到信封加密凭据；默认只做预检查'

    def add_arguments(self, parser):
        parser.add_argument(
            '--execute',
            action='store_true',
            help='执行迁移；未提供时只显示待迁移数量',
        )
        parser.add_argument(
            '--clear-legacy',
            action='store_true',
            help='验证加密数据可解密后清空 Host.pkey；必须与 --execute 同时使用',
        )

    def handle(self, *args, **options):
        execute = options['execute']
        clear_legacy = options['clear_legacy']
        if clear_legacy and not execute:
            raise CommandError('--clear-legacy 必须与 --execute 同时使用')

        hosts = Host.objects.exclude(pkey__isnull=True).exclude(pkey='').order_by('id')
        pending = [host for host in hosts if not host.asset_identity_bindings.exists()]
        if not execute:
            self.stdout.write('待迁移主机数：%s' % len(pending))
            self.stdout.write('这是预检查；添加 --execute 后才会写入数据库。')
            return

        migrated = 0
        for host in pending:
            with transaction.atomic():
                credential = Credential(
                    name='host-%s-legacy-ssh-key' % host.id,
                    type='ssh_key',
                    description='Migrated from Host.pkey for %s' % host.name,
                    created_by=host.created_by,
                )
                credential.set_secret(host.pkey)
                credential.full_clean()
                credential.save(force_insert=True)
                if not hmac.compare_digest(credential.reveal_secret(), host.pkey):
                    raise CommandError('主机 %s 的凭据回读校验失败' % host.id)

                identity = Identity(
                    name='host-%s-legacy-ssh' % host.id,
                    protocol='ssh',
                    username=host.username,
                    credential=credential,
                    description='Migrated legacy identity for %s' % host.name,
                    created_by=host.created_by,
                )
                identity.full_clean()
                identity.save(force_insert=True)
                AssetIdentityBinding.bind(
                    host=host,
                    identity=identity,
                    created_by=host.created_by,
                    is_default=True,
                )

                if clear_legacy:
                    host.pkey = None
                    host.save(update_fields=('pkey',))
                migrated += 1

        self.stdout.write(self.style.SUCCESS(
            '已迁移 %s 台主机；旧私钥%s清空。' % (
                migrated,
                '已' if clear_legacy else '未',
            )
        ))

from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import verify_audit_chain


class Command(BaseCommand):
    help = '验证审计事件的序号、前置哈希和 HMAC 签名链'

    def handle(self, *args, **options):
        valid, message = verify_audit_chain()
        if not valid:
            raise CommandError(message)
        self.stdout.write(self.style.SUCCESS(message))

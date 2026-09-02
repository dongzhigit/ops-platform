from django.core.management.base import BaseCommand

from apps.file.services import cleanup_expired_file_transfers


class Command(BaseCommand):
    help = 'Find or remove expired SFTP upload temporary files'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        result = cleanup_expired_file_transfers(
            execute=options['execute'], limit=options['limit']
        )
        message = (
            'eligible={eligible} removed={removed} missing={missing} '
            'failed={failed} skipped={skipped}'
        ).format(**result)
        if options['execute']:
            self.stdout.write(self.style.SUCCESS(message))
        else:
            self.stdout.write(self.style.WARNING('dry-run ' + message))

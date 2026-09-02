# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.core.management.base import BaseCommand
from django.core.management import execute_from_command_line
from django.conf import settings


class Command(BaseCommand):
    help = '初始化/更新数据库'

    def handle(self, *args, **options):
        apps = [x.split('.')[-1] for x in settings.INSTALLED_APPS if x.startswith('apps.')]
        # Production releases must ship their migration files. Generating them
        # inside a container makes schema history depend on an ephemeral
        # filesystem and can silently skip changes after an image replacement.
        execute_from_command_line(['manage.py', 'makemigrations', '--check', '--dry-run'] + apps)
        execute_from_command_line(['manage.py', 'migrate', '--noinput'])
        self.stdout.write(self.style.SUCCESS('初始化/更新成功'))

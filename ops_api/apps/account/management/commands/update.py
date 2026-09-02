# Copyright: (c) OpenSpug Organization. https://github.com/openspug/spug
# Copyright: (c) <spug.dev@gmail.com>
# Released under the AGPL-3.0 License.
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = '升级 ops-platform 版本'

    def handle(self, *args, **options):
        raise CommandError('ops-platform 不再支持从旧上游发布服务执行在线升级，请使用当前仓库的发布流程。')

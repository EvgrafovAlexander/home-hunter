"""Проверка и безопасная распаковка полного backup на локальном компьютере."""

import tarfile

from django.core.management.base import BaseCommand, CommandError
from listings.services.database_backup import BackupError
from listings.services.project_backup import verify_project_backup


class Command(BaseCommand):
    help = "Проверить полный backup и, при необходимости, распаковать в пустой каталог; БД не изменяется"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("archive")
        parser.add_argument("--extract-to")

    def handle(self, *args, **options):
        try:
            manifest = verify_project_backup(options["archive"], extract_to=options["extract_to"])
        except (BackupError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Backup проверен: {len(manifest['files'])} файлов, создан {manifest['created_at']}"))

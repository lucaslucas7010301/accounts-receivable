"""MF会計の売掛金 補助元帳CSV(CP932)を取り込む。

    python manage.py import_mf_ledger "/path/to/補助元帳.csv"

取込仕様は docs/db_design.md §6.1、ロジックは accounting/importers.py を参照。
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounting.importers import import_mf_ledger, parse_csv_bytes


class Command(BaseCommand):
    help = 'MF会計の売掛金補助元帳CSVを取り込む'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--keep', action='store_true',
                            help='既存データを消さずに追記（既定は全取引データを置換）')

    def handle(self, *args, **opts):
        try:
            raw = open(opts['csv_path'], 'rb').read()
        except FileNotFoundError:
            raise CommandError(f'ファイルが見つかりません: {opts["csv_path"]}')

        rows = parse_csv_bytes(raw)
        User = get_user_model()
        admin, _ = User.objects.get_or_create(
            username='admin', defaults={'is_staff': True, 'is_superuser': True})

        r = import_mf_ledger(rows, admin, source_ref=opts['csv_path'].split('/')[-1],
                             replace=not opts['keep'])

        self.stdout.write(self.style.SUCCESS(
            f'取込完了: 顧問先{r["clients"]}件 / 請求{r["invoices"]}件 / 入金{r["receipts"]}件'))
        self.stdout.write('除外(顧問先コードなし): '
                          + ', '.join(f'{k}×{v}' for k, v in r['skipped'].items()))
        if r['mismatches']:
            self.stdout.write(self.style.WARNING(f'⚠ MF残高と不一致 {len(r["mismatches"])}件'))
        else:
            self.stdout.write(self.style.SUCCESS('✅ 全顧問先の残高がMFと一致しました。'))

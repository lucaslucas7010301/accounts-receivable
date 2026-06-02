"""デモ用サンプルデータを投入する。

    python manage.py seed_demo

ユーザー（admin/admin, 佐藤/鈴木）、顧問先、請求、入金を作成し、FIFO自動消込と
督促アクションを登録する。冪等ではないので、再実行前に既存データの削除を推奨。
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounting import services
from accounting.models import (Client, CollectionAction, ImportBatch, Invoice,
                               Receipt)

TODAY = date(2026, 6, 1)  # context の現在日に合わせる


class Command(BaseCommand):
    help = 'デモ用のサンプルデータを投入する'

    def handle(self, *args, **opts):
        User = get_user_model()

        admin, created = User.objects.get_or_create(
            username='admin', defaults={'is_staff': True, 'is_superuser': True, 'email': 'admin@example.com'})
        if created:
            admin.set_password('admin'); admin.save()
        sato, _ = User.objects.get_or_create(username='佐藤', defaults={'is_staff': True})
        suzu, _ = User.objects.get_or_create(username='鈴木', defaults={'is_staff': True})
        for u in (sato, suzu):
            if not u.has_usable_password():
                u.set_password('pass1234'); u.save()

        # 顧問先
        clients = {}
        for code, name, who in [
            ('C001', '○○商事',     sato),
            ('C002', '△△工業',     suzu),
            ('C003', '□□物産',     sato),
            ('C004', '◇◇システム', suzu),
            ('C005', '☆☆コンサル', sato),
        ]:
            c, _ = Client.objects.get_or_create(
                code=code, defaults={'name': name, 'assignee': who, 'created_by': admin})
            clients[code] = c

        def inv(code, bid, bdate, ddate, amount, desc):
            Invoice.objects.get_or_create(
                board_invoice_id=bid,
                defaults=dict(client=clients[code], billing_date=bdate, due_date=ddate,
                              amount=Decimal(amount), description=desc, created_by=admin))

        def rcp(code, xid, rdate, amount, src='mf_bank'):
            Receipt.objects.get_or_create(
                external_id=xid,
                defaults=dict(client=clients[code], receipt_date=rdate, amount=Decimal(amount),
                              source=src, description='', created_by=admin))

        # ○○商事: 古い請求が一部しか入金されず90日超で滞留
        inv('C001', 'B1001', date(2026, 1, 31), date(2026, 2, 28), 330000, '1月分顧問料')
        inv('C001', 'B1002', date(2026, 4, 30), date(2026, 5, 31), 220000, '4月分顧問料')
        rcp('C001', 'R9001', date(2026, 5, 20), 110000)  # FIFOで古い請求へ一部充当
        # △△工業: 1ヶ月超の延滞
        inv('C002', 'B1003', date(2026, 3, 31), date(2026, 4, 30), 210000, '3月分顧問料')
        # □□物産: 期日直後
        inv('C003', 'B1004', date(2026, 4, 30), date(2026, 5, 31), 120000, '4月分顧問料')
        # ◇◇システム: 期日未到来
        inv('C004', 'B1005', date(2026, 5, 15), date(2026, 6, 15), 480000, 'スポット支援')
        # ☆☆コンサル: 完済済み
        inv('C005', 'B1006', date(2026, 2, 28), date(2026, 3, 31), 150000, '2月分顧問料')
        rcp('C005', 'R9002', date(2026, 4, 10), 150000)

        # FIFO自動消込
        for c in clients.values():
            services.reconcile_fifo(c)

        # 督促アクション
        def action(code, days_ago, who, method, content, result, status, next_in=None):
            CollectionAction.objects.get_or_create(
                client=clients[code], method=method, content=content,
                defaults=dict(
                    acted_at=timezone.make_aware(datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time()).replace(hour=14)),
                    actor=who, result=result, status=status,
                    next_action_date=(TODAY + timedelta(days=next_in)) if next_in is not None else None,
                    created_by=who))

        action('C001', 1, sato, 'email', '督促状を送付', '反応待ち', 'in_progress', next_in=-1)
        action('C001', 0, suzu, 'call',  '電話したが不在', '折返し依頼', 'in_progress', next_in=4)
        action('C002', 4, suzu, 'call',  '入金予定を確認', '6/10入金の約束', 'in_progress', next_in=2)

        # 取込バッチのログ（取込状況画面用）
        ImportBatch.objects.get_or_create(
            source='board', defaults=dict(source_ref='2026-06-01 自動', row_total=12,
                                          row_imported=12, status='success', created_by=admin))
        ImportBatch.objects.get_or_create(
            source='mf', defaults=dict(source_ref='2026-06-01 自動', row_total=11, row_imported=8,
                                       row_skipped=1, row_error=2, status='partial',
                                       log='3行目: 顧問先コード"X9"未登録 → スキップ\n7行目: 金額が不正', created_by=admin))

        self.stdout.write(self.style.SUCCESS(
            'サンプルデータを投入しました。ログイン: admin / admin'))

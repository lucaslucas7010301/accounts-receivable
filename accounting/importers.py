"""MF会計 売掛金補助元帳CSV の取込ロジック（コマンド・画面アップロード共通）。

仕様の詳細は docs/db_design.md §6.1 を参照。
"""
import calendar
import collections
import csv
import io
import re
from datetime import date, datetime

from django.db import transaction

from . import services
from .models import (Allocation, Client, CollectionAction, ImportBatch,
                     Invoice, Receipt)

OPENING_DATE = date(2025, 10, 31)              # 繰越残高の計上日
CODE_PATTERN = re.compile(r'^(\S+)\s+(.+)$')   # 「コード 名称」


def _yen(s):
    s = (s or '').replace(',', '').strip()
    return int(s) if s else 0


def _month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _next_month_end(d):
    y, m = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
    return _month_end(y, m)


def _due_date(billing_date, desc, contra_sub):
    """顧問料は当月末、その他は翌月末。"""
    if '顧問料' in (desc or '') or '顧問料' in (contra_sub or ''):
        return _month_end(billing_date.year, billing_date.month)
    return _next_month_end(billing_date)


def _receipt_source(contra_account, contra_sub):
    if 'NSS' in (contra_sub or ''):
        return 'nss'
    if any(k in (contra_account or '') for k in ('預金', '現金', '銀行')):
        return 'mf_bank'
    return 'manual'


def parse_csv_bytes(raw):
    """CP932（フォールバックUTF-8）でデコードして dict 行のリストを返す。"""
    for enc in ('cp932', 'utf-8-sig'):
        try:
            text = raw.decode(enc)
            return list(csv.DictReader(io.StringIO(text)))
        except UnicodeDecodeError:
            continue
    raise ValueError('CSVの文字コードを判定できませんでした（CP932/UTF-8）。')


def import_mf_ledger(rows, user, source_ref='', replace=True):
    """補助元帳の行（dictのリスト）を取り込む。統計dictを返す。"""
    if replace:
        # 置換するのは元帳由来データ（請求・入金・引当）のみ。
        # 顧問先・督促アクション・月次確定はユーザー資産/履歴なので保持する。
        Allocation.objects.all().delete()
        Invoice.objects.all().delete()
        Receipt.objects.all().delete()

    def keyf(r):
        return datetime.strptime(r['取引日'], '%Y/%m/%d').date()

    per_client = {}
    skipped = collections.Counter()
    for r in rows:
        sub = (r.get('補助科目') or '').strip()
        m = CODE_PATTERN.match(sub)
        if not m:
            skipped[sub or '(空欄)'] += 1
            continue
        code, name = m.group(1), m.group(2).strip()
        per_client.setdefault(code, {'name': name, 'rows': []})['rows'].append(r)

    invoices, receipts = [], []
    seq = 0
    with transaction.atomic():
        for code, info in per_client.items():
            client, _ = Client.objects.get_or_create(
                code=code, defaults={'name': info['name'], 'created_by': user})
            crows = sorted(info['rows'], key=keyf)

            first = crows[0]
            opening = _yen(first['残高']) - _yen(first['借方金額']) + _yen(first['貸方金額'])
            if opening > 0:
                invoices.append(Invoice(
                    client=client, board_invoice_id=f'MFL-OPEN-{code}',
                    billing_date=OPENING_DATE, due_date=OPENING_DATE,
                    amount=opening, description='繰越残高', source='mf', created_by=user))
            elif opening < 0:
                receipts.append(Receipt(
                    client=client, external_id=f'MFL-OPEN-{code}',
                    receipt_date=OPENING_DATE, amount=-opening,
                    source='manual', description='繰越（前受）', created_by=user))

            for r in crows:
                d = keyf(r)
                deb, cre = _yen(r['借方金額']), _yen(r['貸方金額'])
                desc = r['摘要'] or ''
                contra = f"{r['相手勘定科目']}/{r['相手補助科目']}".strip('/')
                seq += 1
                if deb > 0:
                    invoices.append(Invoice(
                        client=client, board_invoice_id=f'MFL-{seq}',
                        billing_date=d, due_date=_due_date(d, desc, r['相手補助科目']),
                        amount=deb, description=f'{desc}（{contra}）'[:200],
                        source='mf', created_by=user))
                if cre > 0:
                    receipts.append(Receipt(
                        client=client, external_id=f'MFL-{seq}',
                        receipt_date=d, amount=cre,
                        source=_receipt_source(r['相手勘定科目'], r['相手補助科目']),
                        description=f'{desc}（{contra}）'[:200], created_by=user))

        Invoice.objects.bulk_create(invoices, batch_size=1000)
        Receipt.objects.bulk_create(receipts, batch_size=1000)

    # FIFO自動消込
    for client in Client.objects.all():
        if client.receipts.exists():
            services.reconcile_fifo(client)

    # MF残高との照合
    mismatches = []
    for code, info in per_client.items():
        crows = sorted(info['rows'], key=keyf)
        mf_balance = _yen(crows[-1]['残高'])
        calc = services.client_balance(Client.objects.get(code=code))
        if calc != mf_balance:
            mismatches.append((code, info['name'], mf_balance, calc))

    batch = ImportBatch.objects.create(
        source='mf', source_ref=source_ref,
        row_total=len(rows), row_imported=len(invoices) + len(receipts),
        row_skipped=sum(skipped.values()), row_error=len(mismatches),
        status='success' if not mismatches else 'partial', created_by=user,
        log='除外: ' + ', '.join(f'{k}×{v}' for k, v in skipped.items()) + '\n'
            + '\n'.join(f'残高不一致 {c} {n}: MF¥{m:,} ≠ 計算¥{v:,}'
                        for c, n, m, v in mismatches[:50]))

    return {
        'clients': len(per_client), 'invoices': len(invoices), 'receipts': len(receipts),
        'skipped': dict(skipped), 'mismatches': mismatches, 'batch': batch,
    }

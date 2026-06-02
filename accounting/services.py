"""売掛金の算出ロジック（残高・元帳・エイジング）と FIFO 自動消込。"""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import Allocation, Invoice, Receipt

ZERO = Decimal(0)
AGING_BUCKETS = ['0-30', '31-60', '61-90', '90+']


def _bucket(days):
    if days <= 30:
        return '0-30'
    if days <= 60:
        return '31-60'
    if days <= 90:
        return '61-90'
    return '90+'


def client_balance(client, as_of=None):
    """顧問先残高 = Σ請求 − Σ入金（as_of 時点）"""
    inv = client.invoices.all()
    rcp = client.receipts.all()
    if as_of:
        inv = inv.filter(billing_date__lte=as_of)
        rcp = rcp.filter(receipt_date__lte=as_of)
    debit  = inv.aggregate(s=Sum('amount'))['s'] or ZERO
    credit = rcp.aggregate(s=Sum('amount'))['s'] or ZERO
    return debit - credit


def reconcile_fifo(client):
    """未引当の入金を、古い未納請求からFIFOで自動引当する。

    method='manual' の引当は保護（再計算で消さない・上書きしない）。
    冪等：fifo_auto 分のみ作り直す。
    """
    with transaction.atomic():
        Allocation.objects.filter(method='fifo_auto', receipt__client=client).delete()

        invoices = list(client.invoices.order_by('due_date', 'billing_date'))
        receipts = list(client.receipts.order_by('receipt_date'))

        def manual_sum(qs):
            return qs.filter(method='manual').aggregate(s=Sum('amount'))['s'] or ZERO

        inv_remaining = {i.id: i.amount - manual_sum(i.allocations) for i in invoices}
        rcp_remaining = {r.id: r.amount - manual_sum(r.allocations) for r in receipts}

        new_allocs = []
        for r in receipts:
            avail = rcp_remaining[r.id]
            if avail <= 0:
                continue
            for i in invoices:
                if avail <= 0:
                    break
                rem = inv_remaining[i.id]
                if rem <= 0:
                    continue
                applied = min(avail, rem)
                new_allocs.append(Allocation(receipt=r, invoice=i, amount=applied, method='fifo_auto'))
                avail -= applied
                inv_remaining[i.id] -= applied
        Allocation.objects.bulk_create(new_allocs)


def open_invoices(client, as_of=None):
    """未納の請求（残額>0）を期日順に返す。各行に outstanding / days_overdue / bucket を付与。"""
    as_of = as_of or date.today()
    rows = []
    for inv in client.invoices.order_by('due_date', 'billing_date'):
        out = inv.outstanding
        if out <= 0:
            continue
        days = (as_of - inv.due_date).days
        rows.append({
            'invoice': inv,
            'outstanding': out,
            'days_overdue': days,
            'overdue': days > 0,
            'bucket': _bucket(days) if days > 0 else '未到来',
        })
    return rows


def ledger_rows(client, date_from=None, date_to=None):
    """得意先元帳：請求(+)・入金(−)を時系列で並べ、残高を累計。

    期間を絞る場合は date_from 以前の累計を繰越残高として先頭に置く。
    戻り値: (opening_balance, rows)
    """
    movements = []
    for i in client.invoices.all():
        movements.append({'date': i.billing_date, 'kind': '請求', 'plus': i.amount,
                          'minus': ZERO, 'desc': i.description or i.board_invoice_id,
                          'src': i.board_invoice_id, 'by': i.created_by})
    for r in client.receipts.all():
        movements.append({'date': r.receipt_date, 'kind': '入金', 'plus': ZERO,
                          'minus': r.amount, 'desc': r.description or r.get_source_display(),
                          'src': r.get_source_display(), 'by': r.created_by})
    movements.sort(key=lambda m: (m['date'], m['kind']))

    opening = ZERO
    rows = []
    for m in movements:
        if date_from and m['date'] < date_from:
            opening += m['plus'] - m['minus']
            continue
        if date_to and m['date'] > date_to:
            continue
        rows.append(m)

    bal = opening
    for m in rows:
        bal += m['plus'] - m['minus']
        m['balance'] = bal
    return opening, rows


def aging_summary(as_of=None):
    """全社のエイジング集計（未納請求の残額をバケット別に合計）。"""
    as_of = as_of or date.today()
    totals = {b: ZERO for b in AGING_BUCKETS}
    overdue_total = ZERO
    open_count = 0
    invoices = Invoice.objects.prefetch_related('allocations').all()
    for inv in invoices:
        out = inv.outstanding
        if out <= 0:
            continue
        open_count += 1
        days = (as_of - inv.due_date).days
        if days > 0:
            overdue_total += out
            totals[_bucket(days)] += out
        else:
            totals['0-30'] += out
    return totals, overdue_total, open_count


def client_max_overdue_days(client, as_of=None):
    """顧問先の最長延滞日数（未納請求のうち最も古い期日からの経過）。"""
    as_of = as_of or date.today()
    worst = None
    for inv in client.invoices.all():
        if inv.outstanding <= 0:
            continue
        days = (as_of - inv.due_date).days
        if worst is None or days > worst:
            worst = days
    return worst  # None なら未納なし

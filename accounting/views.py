import csv as csvmod
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import services
from .forms import CollectionActionForm
from .importers import import_mf_ledger, parse_csv_bytes
from .models import (Allocation, Client, CollectionAction, ImportBatch,
                     Invoice, MonthlyClose, MonthlyCloseLine, Receipt)

ZERO = Decimal(0)


def _client_summary(client, as_of=None):
    as_of = as_of or date.today()
    balance = services.client_balance(client, as_of)
    over90 = ZERO
    for inv in client.invoices.all():
        out = inv.outstanding
        if out > 0 and (as_of - inv.due_date).days > 90:
            over90 += out
    last_action = client.actions.first()
    next_action = (client.actions.exclude(next_action_date=None)
                   .exclude(status='resolved').order_by('next_action_date').first())
    return {'client': client, 'balance': balance,
            'max_overdue': services.client_max_overdue_days(client, as_of),
            'over90': over90, 'last_action': last_action, 'next_action': next_action}


# ─── S1 ダッシュボード ──────────────────────────────────────
@login_required
def dashboard(request):
    today = date.today()
    clients = Client.objects.filter(is_active=True).prefetch_related('invoices__allocations', 'actions')
    summaries = [_client_summary(c, today) for c in clients]

    total_balance = sum((s['balance'] for s in summaries if s['balance'] > 0), ZERO)
    aging, overdue_total, open_count = services.aging_summary(today)
    aging_max = max(aging.values()) or Decimal(1)

    followups = (CollectionAction.objects.exclude(next_action_date=None)
                 .exclude(status='resolved').filter(next_action_date__lte=today)
                 .select_related('client', 'actor').order_by('next_action_date'))
    top = sorted([s for s in summaries if s['balance'] > 0],
                 key=lambda s: (s['over90'], s['balance']), reverse=True)[:5]
    recent = CollectionAction.objects.select_related('client', 'actor')[:6]
    last_import = ImportBatch.objects.first()

    return render(request, 'accounting/dashboard.html', {
        'total_balance': total_balance, 'open_count': open_count,
        'overdue_total': overdue_total, 'followup_count': followups.count(),
        'aging': [(b, aging[b], int(aging[b] / aging_max * 100)) for b in services.AGING_BUCKETS],
        'followups': followups[:8], 'top': top, 'recent': recent, 'last_import': last_import,
    })


# ─── S2 顧問先一覧 ──────────────────────────────────────────
@login_required
def client_list(request):
    today = date.today()
    assignee = request.GET.get('assignee', '')
    only_balance = request.GET.get('balance', '1') == '1'
    only_followup = request.GET.get('followup', '') == '1'
    q = request.GET.get('q', '').strip()

    clients = Client.objects.filter(is_active=True).select_related('assignee') \
        .prefetch_related('invoices__allocations', 'actions')
    if assignee:
        clients = clients.filter(assignee_id=assignee)
    if q:
        clients = clients.filter(Q(code__icontains=q) | Q(name__icontains=q))

    rows = [_client_summary(c, today) for c in clients]
    if only_balance:
        rows = [r for r in rows if r['balance'] > 0]
    if only_followup:
        rows = [r for r in rows if r['next_action'] and r['next_action'].next_action_date
                and r['next_action'].next_action_date <= today]
    rows.sort(key=lambda r: r['balance'], reverse=True)

    assignees = get_user_model().objects.filter(clients__isnull=False).distinct()
    return render(request, 'accounting/client_list.html', {
        'rows': rows, 'assignees': assignees, 'assignee': assignee,
        'only_balance': only_balance, 'only_followup': only_followup, 'q': q, 'today': today,
    })


# ─── S3 顧問先詳細 ──────────────────────────────────────────
@login_required
def client_detail(request, code):
    client = get_object_or_404(Client, code=code)
    today = date.today()

    if request.method == 'POST':
        form = CollectionActionForm(request.POST)
        if form.is_valid():
            action = form.save(commit=False)
            action.client = client
            action.acted_at = timezone.now()
            action.actor = request.user
            action.created_by = request.user
            action.save()
            messages.success(request, '督促アクションを記録しました。')
            return redirect('accounting:client_detail', code=client.code)
    else:
        form = CollectionActionForm()

    date_from = request.GET.get('date_from') or None
    date_to = request.GET.get('date_to') or None
    opening, ledger = services.ledger_rows(client, date_from, date_to)
    return render(request, 'accounting/client_detail.html', {
        'client': client, 'summary': _client_summary(client, today),
        'open_invoices': services.open_invoices(client, today),
        'opening': opening, 'ledger': ledger,
        'actions': client.actions.select_related('actor').all(), 'form': form,
        'date_from': date_from or '', 'date_to': date_to or '',
    })


# ─── S4 督促ワークリスト ────────────────────────────────────
@login_required
def followups(request):
    today = date.today()
    mine = request.GET.get('mine', '') == '1'
    overdue_only = request.GET.get('overdue', '') == '1'

    qs = (CollectionAction.objects.exclude(next_action_date=None)
          .exclude(status='resolved').select_related('client', 'client__assignee', 'actor')
          .order_by('next_action_date'))
    if mine:
        qs = qs.filter(client__assignee=request.user)
    if overdue_only:
        qs = qs.filter(next_action_date__lte=today)

    rows = []
    for a in qs:
        rows.append({'action': a, 'summary': _client_summary(a.client, today),
                     'overdue': a.next_action_date <= today})
    return render(request, 'accounting/followups.html',
                  {'rows': rows, 'mine': mine, 'overdue_only': overdue_only, 'today': today})


# ─── S5 消込（入金引当） ────────────────────────────────────
@login_required
def allocation(request):
    code = request.GET.get('client', '') or request.POST.get('client', '')
    client = Client.objects.filter(code=code).first() if code else None

    if request.method == 'POST' and client:
        action = request.POST.get('action')
        if action == 'reconcile':
            services.reconcile_fifo(client)
            messages.success(request, 'FIFO自動消込を再実行しました（手動引当は保護）。')
        elif action == 'manual_add':
            try:
                rcp = client.receipts.get(pk=request.POST['receipt'])
                inv = client.invoices.get(pk=request.POST['invoice'])
                amt = Decimal(request.POST['amount'])
                if amt <= 0 or amt > rcp.unapplied or amt > inv.outstanding:
                    raise ValueError
                Allocation.objects.create(receipt=rcp, invoice=inv, amount=amt,
                                          method='manual', created_by=request.user)
                services.reconcile_fifo(client)  # 残りを自動で埋め直す（manualは保護）
                messages.success(request, '手動引当を登録しました。')
            except (KeyError, ValueError, InvalidOperation, Receipt.DoesNotExist, Invoice.DoesNotExist):
                messages.error(request, '引当額が不正です（未引当額・残額を超えていないか確認してください）。')
        elif action == 'delete':
            Allocation.objects.filter(pk=request.POST.get('alloc'), receipt__client=client).delete()
            services.reconcile_fifo(client)
            messages.success(request, '引当を解除しました。')
        return redirect(f"{request.path}?client={client.code}")

    ctx = {'clients': Client.objects.filter(is_active=True).order_by('code'), 'client': client}
    if client:
        ctx.update({
            'receipts': client.receipts.order_by('receipt_date'),
            'open_invoices': services.open_invoices(client),
            'allocations': Allocation.objects.filter(receipt__client=client)
                .select_related('receipt', 'invoice').order_by('invoice__due_date'),
        })
    return render(request, 'accounting/allocation.html', ctx)


# ─── S6 月次残高確定・レポート ──────────────────────────────
def _month_bounds(period):
    y, m = int(period[:4]), int(period[5:7])
    start = date(y, m, 1)
    end = (date(y + (m == 12), (m % 12) + 1, 1)) - timedelta(days=1)
    return start, end


@login_required
def monthly_close(request):
    latest = Invoice.objects.order_by('-billing_date').first()
    default_period = (latest.billing_date if latest else date.today()).strftime('%Y-%m')
    period = request.GET.get('period') or request.POST.get('period') or default_period
    start, end = _month_bounds(period)
    prev_end = start - timedelta(days=1)

    rows = []
    for c in Client.objects.filter(is_active=True).prefetch_related('invoices__allocations', 'receipts'):
        opening = services.client_balance(c, prev_end)
        billed = c.invoices.filter(billing_date__range=(start, end)).aggregate(s=Sum('amount'))['s'] or ZERO
        received = c.receipts.filter(receipt_date__range=(start, end)).aggregate(s=Sum('amount'))['s'] or ZERO
        closing = services.client_balance(c, end)
        if opening or billed or received or closing:
            rows.append({'client': c, 'opening': opening, 'billed': billed,
                         'received': received, 'closing': closing})
    rows.sort(key=lambda r: r['closing'], reverse=True)
    totals = {k: sum((r[k] for r in rows), ZERO) for k in ('opening', 'billed', 'received', 'closing')}

    if request.method == 'POST' and request.POST.get('action') == 'close':
        obj, created = MonthlyClose.objects.get_or_create(period=period, defaults={'created_by': request.user})
        obj.lines.all().delete()
        MonthlyCloseLine.objects.bulk_create(
            [MonthlyCloseLine(close=obj, client=r['client'], balance=r['closing']) for r in rows])
        messages.success(request, f'{period} の残高を確定しました（{len(rows)}社）。')
        return redirect(f"{request.path}?period={period}")

    if request.GET.get('export') == 'csv':
        buf = io.StringIO()
        w = csvmod.writer(buf)
        w.writerow(['顧問先コード', '顧問先名', '期首残高', '当月請求', '当月入金', '期末残高'])
        for r in rows:
            w.writerow([r['client'].code, r['client'].name, r['opening'],
                        r['billed'], r['received'], r['closing']])
        resp = HttpResponse(buf.getvalue().encode('cp932', 'replace'),
                            content_type='text/csv; charset=cp932')
        resp['Content-Disposition'] = f'attachment; filename=ar_{period}.csv'
        return resp

    closed = MonthlyClose.objects.filter(period=period).first()
    return render(request, 'accounting/monthly_close.html', {
        'period': period, 'rows': rows, 'totals': totals, 'closed': closed,
        'periods': _available_periods(),
    })


def _available_periods():
    months = set()
    for d in Invoice.objects.values_list('billing_date', flat=True):
        if d > date(2025, 10, 31):
            months.add(d.strftime('%Y-%m'))
    return sorted(months, reverse=True)


# ─── S7 取込状況 ────────────────────────────────────────────
@login_required
def import_status(request):
    if request.method == 'POST' and request.FILES.get('csv_file'):
        try:
            rows = parse_csv_bytes(request.FILES['csv_file'].read())
            r = import_mf_ledger(rows, request.user, source_ref=request.FILES['csv_file'].name)
            msg = f"取込完了: 顧問先{r['clients']}件 / 請求{r['invoices']}件 / 入金{r['receipts']}件"
            if r['mismatches']:
                messages.warning(request, msg + f"（MF残高と不一致 {len(r['mismatches'])}件）")
            else:
                messages.success(request, msg + "（全社MF残高一致 ✅）")
        except Exception as e:
            messages.error(request, f'取込に失敗しました: {e}')
        return redirect('accounting:import_status')

    return render(request, 'accounting/import_status.html',
                  {'batches': ImportBatch.objects.select_related('created_by')[:50]})

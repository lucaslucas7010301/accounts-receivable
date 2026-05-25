import csv
import io
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import CSVUploadForm, JournalEntryForm, ReceiptForm, SaleForm
from .models import Account, Customer, JournalEntry, Transaction


# ─── 売上登録 ──────────────────────────────────────────────
def sale_create(request):
    if request.method == 'POST':
        form = SaleForm(request.POST)
        if form.is_valid():
            txn = form.save(commit=False)
            txn.transaction_type = 'sale'
            txn.save()
            txn.post()
            messages.success(request, f'売上を登録しました（{txn}）')
            return redirect('accounting:sale_create')
    else:
        form = SaleForm(initial={'date': timezone.localdate()})
    return render(request, 'accounting/sale_create.html', {'form': form})


# ─── 入金消込 ──────────────────────────────────────────────
def receipt_create(request):
    if request.method == 'POST':
        form = ReceiptForm(request.POST)
        if form.is_valid():
            txn = form.save(commit=False)
            txn.transaction_type = 'receipt'
            txn.save()
            txn.post()
            messages.success(request, f'入金を登録しました（{txn}）')
            return redirect('accounting:receipt_create')
    else:
        form = ReceiptForm(initial={'date': timezone.localdate()})

    balances = _ar_balances_by_customer()
    return render(request, 'accounting/receipt_create.html', {
        'form': form,
        'balances': balances,
    })


# ─── 手動仕訳 ──────────────────────────────────────────────
def journal_create(request):
    if request.method == 'POST':
        form = JournalEntryForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            last = JournalEntry.objects.order_by('-group_id').first()
            group_id = (last.group_id or 0) + 1 if last else 1
            with db_transaction.atomic():
                JournalEntry.objects.create(
                    date=d['date'], account=d['debit_account'],
                    customer=d['debit_customer'], side='debit',
                    amount=d['amount'], description=d['description'],
                    group_id=group_id)
                JournalEntry.objects.create(
                    date=d['date'], account=d['credit_account'],
                    customer=d['credit_customer'], side='credit',
                    amount=d['amount'], description=d['description'],
                    group_id=group_id)
            messages.success(request, '仕訳を登録しました。')
            return redirect('accounting:journal_create')
    else:
        form = JournalEntryForm(initial={'date': timezone.localdate()})
    return render(request, 'accounting/journal_create.html', {'form': form})


# ─── 一括登録 ──────────────────────────────────────────────
BULK_ROW_COUNT = 10

def bulk_create(request):
    csv_form = CSVUploadForm()
    errors   = []

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'csv_upload':
            csv_form = CSVUploadForm(request.POST, request.FILES)
            if csv_form.is_valid():
                f    = request.FILES['csv_file']
                text = f.read().decode('utf-8-sig')
                reader = csv.DictReader(io.StringIO(text))
                rows, errors = _parse_bulk_rows(reader)
                if not errors:
                    _save_bulk_rows(rows)
                    messages.success(request, f'CSVから {len(rows)} 件を登録しました。')
                    return redirect('accounting:bulk_create')

        elif action == 'screen_input':
            rows_raw = []
            for i in range(BULK_ROW_COUNT):
                row = {
                    'type':        request.POST.get(f'type_{i}', '').strip(),
                    'date':        request.POST.get(f'date_{i}', '').strip(),
                    'customer':    request.POST.get(f'customer_{i}', '').strip(),
                    'amount':      request.POST.get(f'amount_{i}', '').strip(),
                    'description': request.POST.get(f'description_{i}', '').strip(),
                    'row_num':     i + 1,
                }
                rows_raw.append(row)
            rows_raw = [r for r in rows_raw
                        if r['type'] or r['date'] or r['customer'] or r['amount']]
            rows, errors = _parse_bulk_rows(rows_raw, from_screen=True)
            if not errors and rows:
                _save_bulk_rows(rows)
                messages.success(request, f'{len(rows)} 件を登録しました。')
                return redirect('accounting:bulk_create')
            elif not rows and not errors:
                messages.warning(request, '入力行がありませんでした。')

    customers = Customer.objects.filter(is_active=True)
    return render(request, 'accounting/bulk_create.html', {
        'csv_form':       csv_form,
        'errors':         errors,
        'bulk_row_count': range(BULK_ROW_COUNT),
        'customers':      customers,
    })


def _parse_bulk_rows(rows, from_screen=False):
    valid_rows = []
    errors     = []
    customers  = {c.code: c for c in Customer.objects.all()}

    for i, row in enumerate(rows):
        row_num = row.get('row_num', i + 1)
        t    = (row.get('type') or row.get('取引種別', '')).strip()
        d    = (row.get('date') or row.get('日付', '')).strip()
        ckey = (row.get('customer') or row.get('得意先コード', '')).strip()
        amt  = (row.get('amount') or row.get('金額', '')).strip()
        desc = (row.get('description') or row.get('摘要', '')).strip()

        if not any([t, d, ckey, amt]):
            continue

        if t not in ('sale', 'receipt', '売上', '入金'):
            errors.append(f'{row_num}行目: 取引種別は sale/receipt または 売上/入金 を指定してください（値: "{t}"）')
            continue
        t = 'sale' if t in ('sale', '売上') else 'receipt'

        from datetime import datetime
        try:
            d_obj = datetime.strptime(d, '%Y-%m-%d').date()
        except ValueError:
            errors.append(f'{row_num}行目: 日付の形式が正しくありません（値: "{d}"、例: 2025-01-15）')
            continue

        cust = customers.get(ckey)
        if cust is None:
            errors.append(f'{row_num}行目: 得意先コード "{ckey}" が見つかりません')
            continue

        try:
            amount = Decimal(amt.replace(',', ''))
            if amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            errors.append(f'{row_num}行目: 金額が正しくありません（値: "{amt}"）')
            continue

        valid_rows.append({
            'transaction_type': t,
            'date':             d_obj,
            'customer':         cust,
            'amount':           amount,
            'description':      desc,
        })

    return valid_rows, errors


def _save_bulk_rows(rows):
    with db_transaction.atomic():
        for r in rows:
            txn = Transaction.objects.create(**r)
            txn.post()


# ─── 総勘定元帳 ────────────────────────────────────────────
def general_ledger(request):
    accounts     = Account.objects.all()
    account_code = request.GET.get('account', '1100')
    date_from    = request.GET.get('date_from', '')
    date_to      = request.GET.get('date_to', '')

    try:
        account = Account.objects.get(code=account_code)
    except Account.DoesNotExist:
        account = accounts.first()

    qs = JournalEntry.objects.filter(account=account).select_related('customer')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    qs = qs.order_by('date', 'group_id')

    running = Decimal(0)
    entries = []
    for e in qs:
        if account.account_type == 'asset':
            running += e.amount if e.side == 'debit' else -e.amount
        else:
            running += e.amount if e.side == 'credit' else -e.amount
        entries.append({'entry': e, 'balance': running})

    return render(request, 'accounting/general_ledger.html', {
        'accounts':  accounts,
        'account':   account,
        'entries':   entries,
        'date_from': date_from,
        'date_to':   date_to,
    })


# ─── 補助元帳（得意先別） ──────────────────────────────────
def sub_ledger(request):
    customers   = Customer.objects.filter(is_active=True)
    customer_id = request.GET.get('customer', '')
    date_from   = request.GET.get('date_from', '')
    date_to     = request.GET.get('date_to', '')

    customer = None
    entries  = []

    if customer_id:
        try:
            customer = Customer.objects.get(pk=customer_id)
        except Customer.DoesNotExist:
            pass

    if customer:
        ar = Account.objects.get(code='1100')
        qs = JournalEntry.objects.filter(
            account=ar, customer=customer
        ).order_by('date', 'group_id')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        running = Decimal(0)
        for e in qs:
            running += e.amount if e.side == 'debit' else -e.amount
            entries.append({'entry': e, 'balance': running})

    return render(request, 'accounting/sub_ledger.html', {
        'customers':   customers,
        'customer':    customer,
        'entries':     entries,
        'customer_id': customer_id,
        'date_from':   date_from,
        'date_to':     date_to,
    })


# ─── 残高照合 ─────────────────────────────────────────────
def balance_check(request):
    result = None
    if request.method == 'POST':
        ar = Account.objects.get(code='1100')

        gl_debit  = JournalEntry.objects.filter(account=ar, side='debit' ).aggregate(s=Sum('amount'))['s'] or Decimal(0)
        gl_credit = JournalEntry.objects.filter(account=ar, side='credit').aggregate(s=Sum('amount'))['s'] or Decimal(0)
        gl_balance = gl_debit - gl_credit

        sub_balances = _ar_balances_by_customer()
        sub_total    = sum(sub_balances.values()) if sub_balances else Decimal(0)

        result = {
            'gl_balance':   gl_balance,
            'sub_total':    sub_total,
            'diff':         gl_balance - sub_total,
            'ok':           gl_balance == sub_total,
            'sub_balances': sub_balances,
        }

    return render(request, 'accounting/balance_check.html', {'result': result})


# ─── 共通ヘルパー ──────────────────────────────────────────
def _ar_balances_by_customer():
    ar        = Account.objects.get(code='1100')
    customers = Customer.objects.filter(is_active=True)
    balances  = {}
    for cust in customers:
        debit  = JournalEntry.objects.filter(account=ar, customer=cust, side='debit' ).aggregate(s=Sum('amount'))['s'] or Decimal(0)
        credit = JournalEntry.objects.filter(account=ar, customer=cust, side='credit').aggregate(s=Sum('amount'))['s'] or Decimal(0)
        bal = debit - credit
        if bal != 0:
            balances[cust] = bal
    return balances

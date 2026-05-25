from django.db import models
from django.db import transaction as db_transaction


class Account(models.Model):
    """勘定科目マスタ（固定3科目）"""
    TYPE_CHOICES = [
        ('asset',   '資産'),
        ('revenue', '収益'),
    ]
    code         = models.CharField('科目コード', max_length=10, unique=True)
    name         = models.CharField('科目名',    max_length=50)
    account_type = models.CharField('科目区分',  max_length=10, choices=TYPE_CHOICES)

    class Meta:
        verbose_name        = '勘定科目'
        verbose_name_plural = '勘定科目'
        ordering            = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Customer(models.Model):
    """得意先マスタ"""
    code       = models.CharField('得意先コード', max_length=20, unique=True)
    name       = models.CharField('得意先名',    max_length=100)
    is_active  = models.BooleanField('有効',     default=True)
    created_at = models.DateTimeField('登録日時', auto_now_add=True)

    class Meta:
        verbose_name        = '得意先'
        verbose_name_plural = '得意先'
        ordering            = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class JournalEntry(models.Model):
    """仕訳明細（複式簿記の1行 = 借方または貸方）"""
    SIDE_CHOICES = [('debit', '借方'), ('credit', '貸方')]

    date        = models.DateField('仕訳日付')
    account     = models.ForeignKey(Account,  on_delete=models.PROTECT,
                                    verbose_name='勘定科目')
    customer    = models.ForeignKey(Customer, on_delete=models.PROTECT,
                                    null=True, blank=True,
                                    verbose_name='得意先',
                                    related_name='journal_entries')
    side        = models.CharField('借貸区分', max_length=6, choices=SIDE_CHOICES)
    amount      = models.DecimalField('金額', max_digits=12, decimal_places=0)
    description = models.CharField('摘要', max_length=200, blank=True)
    # 同一取引の借方・貸方2行を結ぶID（Transaction.pk、または手動仕訳連番）
    group_id    = models.IntegerField('仕訳グループID', null=True, blank=True)
    created_at  = models.DateTimeField('登録日時', auto_now_add=True)

    class Meta:
        verbose_name        = '仕訳'
        verbose_name_plural = '仕訳'
        ordering            = ['date', 'group_id', 'side']

    def __str__(self):
        return (f'{self.date} [{self.get_side_display()}] '
                f'{self.account.name} {self.amount:,.0f}円')


class Transaction(models.Model):
    """取引（売上発生・入金の業務記録）"""
    TYPE_CHOICES = [
        ('sale',    '売上'),
        ('receipt', '入金'),
    ]
    transaction_type = models.CharField('取引種別', max_length=10, choices=TYPE_CHOICES)
    date             = models.DateField('取引日付')
    customer         = models.ForeignKey(Customer, on_delete=models.PROTECT,
                                         verbose_name='得意先')
    amount           = models.DecimalField('金額', max_digits=12, decimal_places=0)
    description      = models.CharField('摘要', max_length=200, blank=True)
    is_posted        = models.BooleanField('起票済', default=False)
    created_at       = models.DateTimeField('登録日時', auto_now_add=True)

    class Meta:
        verbose_name        = '取引'
        verbose_name_plural = '取引'
        ordering            = ['-date', '-created_at']

    def __str__(self):
        return (f'{self.date} {self.get_transaction_type_display()} '
                f'{self.customer.name} {self.amount:,.0f}円')

    def post(self):
        """取引から仕訳を自動起票する。起票済の場合はスキップ（冪等）。"""
        if self.is_posted:
            return
        ar    = Account.objects.get(code='1100')  # 売掛金
        sales = Account.objects.get(code='4000')  # 売上高
        cash  = Account.objects.get(code='1000')  # 現預金
        desc  = self.description or f'{self.get_transaction_type_display()} {self.customer.name}'

        with db_transaction.atomic():
            if self.transaction_type == 'sale':
                # 借方:売掛金 / 貸方:売上高
                JournalEntry.objects.create(
                    date=self.date, account=ar, customer=self.customer,
                    side='debit',  amount=self.amount,
                    description=desc, group_id=self.pk)
                JournalEntry.objects.create(
                    date=self.date, account=sales, customer=self.customer,
                    side='credit', amount=self.amount,
                    description=desc, group_id=self.pk)
            elif self.transaction_type == 'receipt':
                # 借方:現預金 / 貸方:売掛金
                JournalEntry.objects.create(
                    date=self.date, account=cash, customer=self.customer,
                    side='debit',  amount=self.amount,
                    description=desc, group_id=self.pk)
                JournalEntry.objects.create(
                    date=self.date, account=ar, customer=self.customer,
                    side='credit', amount=self.amount,
                    description=desc, group_id=self.pk)
            self.is_posted = True
            self.save()

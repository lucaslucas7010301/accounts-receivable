from django.conf import settings
from django.db import models
from django.db.models import Sum
from simple_history.models import HistoricalRecords


class Audited(models.Model):
    """監査フィールド共通基底（登録者・日時・更新日時）。

    created_by は本来必須だが、MVP段階では admin/シード作成を容易にするため null 許容。
    本番では認証ミドルウェアで自動補完し非null化する想定（docs/db_design.md §7）。
    """
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   null=True, blank=True, related_name='+',
                                   verbose_name='登録者')
    created_at = models.DateTimeField('登録日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        abstract = True


class Client(Audited):
    """顧問先（得意先）"""
    code               = models.CharField('顧問先コード', max_length=20, unique=True)
    name               = models.CharField('顧問先名', max_length=100)
    is_active          = models.BooleanField('有効', default=True)
    assignee           = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                           null=True, blank=True, related_name='clients',
                                           verbose_name='担当者')
    closing_day        = models.PositiveSmallIntegerField('締め日', null=True, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField('入金サイト(日)', null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = verbose_name_plural = '顧問先'
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Invoice(Audited):
    """請求/売上（board由来）"""
    client           = models.ForeignKey(Client, on_delete=models.PROTECT,
                                          related_name='invoices', verbose_name='顧問先')
    board_invoice_id = models.CharField('board請求ID', max_length=50, unique=True)
    billing_date     = models.DateField('請求日')
    due_date         = models.DateField('入金期日')
    amount           = models.DecimalField('請求額', max_digits=12, decimal_places=0)
    description      = models.CharField('摘要', max_length=200, blank=True)
    source           = models.CharField('取込元', max_length=10, default='board')
    history = HistoricalRecords()

    class Meta:
        verbose_name = verbose_name_plural = '請求'
        ordering = ['billing_date']
        indexes = [
            models.Index(fields=['client', 'billing_date']),
            models.Index(fields=['client', 'due_date']),
        ]

    @property
    def allocated(self):
        return self.allocations.aggregate(s=Sum('amount'))['s'] or 0

    @property
    def outstanding(self):
        return self.amount - self.allocated

    def __str__(self):
        return f'{self.billing_date} {self.client.name} {self.amount:,.0f}円'


class Receipt(Audited):
    """入金（MF/銀行/NSS由来）"""
    SOURCE_CHOICES = [('mf_bank', 'MF/銀行'), ('nss', 'NSS引落'), ('manual', '手動')]

    client       = models.ForeignKey(Client, on_delete=models.PROTECT,
                                      related_name='receipts', verbose_name='顧問先')
    external_id  = models.CharField('連携明細ID', max_length=50, unique=True, null=True, blank=True)
    receipt_date = models.DateField('入金日')
    amount       = models.DecimalField('入金額', max_digits=12, decimal_places=0)
    source       = models.CharField('入金元', max_length=10, choices=SOURCE_CHOICES, default='mf_bank')
    description  = models.CharField('摘要', max_length=200, blank=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = verbose_name_plural = '入金'
        ordering = ['receipt_date']
        indexes = [models.Index(fields=['client', 'receipt_date'])]

    @property
    def allocated(self):
        return self.allocations.aggregate(s=Sum('amount'))['s'] or 0

    @property
    def unapplied(self):
        return self.amount - self.allocated

    def __str__(self):
        return f'{self.receipt_date} {self.client.name} {self.amount:,.0f}円'


class Allocation(Audited):
    """消込（入金↔請求の引当）"""
    METHOD_CHOICES = [('fifo_auto', 'FIFO自動'), ('manual', '手動')]

    receipt = models.ForeignKey(Receipt, on_delete=models.PROTECT,
                                related_name='allocations', verbose_name='入金')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT,
                                related_name='allocations', verbose_name='請求')
    amount  = models.DecimalField('引当額', max_digits=12, decimal_places=0)
    method  = models.CharField('引当方式', max_length=10, choices=METHOD_CHOICES, default='fifo_auto')
    history = HistoricalRecords()

    class Meta:
        verbose_name = verbose_name_plural = '消込'

    def __str__(self):
        return f'{self.invoice} ← {self.amount:,.0f}円 ({self.get_method_display()})'


class CollectionAction(Audited):
    """督促アクション"""
    METHOD_CHOICES = [('call', '電話'), ('email', 'メール'), ('visit', '訪問'),
                      ('letter', '文書'), ('other', 'その他')]
    STATUS_CHOICES = [('open', '未対応'), ('in_progress', '督促中'), ('resolved', '解決')]

    client           = models.ForeignKey(Client, on_delete=models.PROTECT,
                                          related_name='actions', verbose_name='顧問先')
    acted_at         = models.DateTimeField('実施日時')
    actor            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='+',
                                         verbose_name='担当者')
    method           = models.CharField('手段', max_length=10, choices=METHOD_CHOICES)
    content          = models.TextField('内容', blank=True)
    result           = models.TextField('結果', blank=True)
    status           = models.CharField('状態', max_length=12, choices=STATUS_CHOICES, default='in_progress')
    next_action_date = models.DateField('次回フォロー日', null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = verbose_name_plural = '督促アクション'
        ordering = ['-acted_at']
        indexes = [
            models.Index(fields=['client', 'acted_at']),
            models.Index(fields=['status', 'next_action_date']),
        ]

    def __str__(self):
        return f'{self.acted_at:%Y-%m-%d} {self.client.name} {self.get_method_display()}'


class ImportBatch(Audited):
    """取込バッチ（連携の監査）"""
    SOURCE_CHOICES = [('board', 'board'), ('mf', 'MF入金'), ('nss', 'NSS')]
    STATUS_CHOICES = [('success', '成功'), ('partial', '一部'), ('failed', '失敗')]

    source       = models.CharField('取込元', max_length=10, choices=SOURCE_CHOICES)
    source_ref   = models.CharField('取込対象', max_length=200, blank=True)
    row_total    = models.IntegerField('対象件数', default=0)
    row_imported = models.IntegerField('取込件数', default=0)
    row_skipped  = models.IntegerField('スキップ', default=0)
    row_error    = models.IntegerField('エラー', default=0)
    status       = models.CharField('状態', max_length=10, choices=STATUS_CHOICES, default='success')
    log          = models.TextField('ログ', blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '取込バッチ'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.get_source_display()}'


class MonthlyClose(Audited):
    """月次残高確定"""
    period     = models.CharField('対象月(YYYY-MM)', max_length=7, unique=True)
    note       = models.TextField('備考', blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '月次残高確定'
        ordering = ['-period']

    def __str__(self):
        return self.period


class MonthlyCloseLine(models.Model):
    """月次残高確定の顧問先別スナップショット"""
    close   = models.ForeignKey(MonthlyClose, on_delete=models.CASCADE, related_name='lines')
    client  = models.ForeignKey(Client, on_delete=models.PROTECT, verbose_name='顧問先')
    balance = models.DecimalField('期末残高', max_digits=12, decimal_places=0)

    class Meta:
        verbose_name = verbose_name_plural = '月次残高明細'

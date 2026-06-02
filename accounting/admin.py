from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (Allocation, Client, CollectionAction, ImportBatch,
                     Invoice, MonthlyClose, MonthlyCloseLine, Receipt)


@admin.register(Client)
class ClientAdmin(SimpleHistoryAdmin):
    list_display  = ('code', 'name', 'assignee', 'is_active')
    list_filter   = ('is_active', 'assignee')
    search_fields = ('code', 'name')


@admin.register(Invoice)
class InvoiceAdmin(SimpleHistoryAdmin):
    list_display  = ('board_invoice_id', 'client', 'billing_date', 'due_date', 'amount')
    list_filter   = ('source',)
    search_fields = ('board_invoice_id', 'client__name')
    date_hierarchy = 'billing_date'


@admin.register(Receipt)
class ReceiptAdmin(SimpleHistoryAdmin):
    list_display  = ('receipt_date', 'client', 'amount', 'source', 'external_id')
    list_filter   = ('source',)
    search_fields = ('external_id', 'client__name')
    date_hierarchy = 'receipt_date'


@admin.register(Allocation)
class AllocationAdmin(SimpleHistoryAdmin):
    list_display = ('invoice', 'receipt', 'amount', 'method')
    list_filter  = ('method',)


@admin.register(CollectionAction)
class CollectionActionAdmin(SimpleHistoryAdmin):
    list_display  = ('acted_at', 'client', 'method', 'status', 'actor', 'next_action_date')
    list_filter   = ('status', 'method', 'actor')
    search_fields = ('client__name', 'content', 'result')


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'source', 'row_imported', 'row_skipped', 'row_error', 'status')
    list_filter  = ('source', 'status')


class MonthlyCloseLineInline(admin.TabularInline):
    model = MonthlyCloseLine
    extra = 0


@admin.register(MonthlyClose)
class MonthlyCloseAdmin(admin.ModelAdmin):
    list_display = ('period', 'created_by', 'created_at')
    inlines = [MonthlyCloseLineInline]

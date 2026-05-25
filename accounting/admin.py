from django.contrib import admin
from .models import Account, Customer, JournalEntry, Transaction


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'account_type']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display  = ['code', 'name', 'is_active', 'created_at']
    list_filter   = ['is_active']
    search_fields = ['code', 'name']


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display  = ['date', 'account', 'side', 'amount', 'customer', 'description', 'group_id']
    list_filter   = ['account', 'side']
    search_fields = ['description']
    date_hierarchy = 'date'


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display  = ['date', 'transaction_type', 'customer', 'amount', 'is_posted', 'description']
    list_filter   = ['transaction_type', 'is_posted']
    search_fields = ['customer__name', 'description']
    date_hierarchy = 'date'
    actions       = ['post_transactions']

    @admin.action(description='選択した取引を仕訳起票する')
    def post_transactions(self, request, queryset):
        count = 0
        for t in queryset.filter(is_posted=False):
            t.post()
            count += 1
        self.message_user(request, f'{count}件を起票しました。')


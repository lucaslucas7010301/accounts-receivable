from django.urls import path
from . import views

app_name = 'accounting'

urlpatterns = [
    path('sale/',         views.sale_create,    name='sale_create'),
    path('receipt/',      views.receipt_create, name='receipt_create'),
    path('journal/',      views.journal_create, name='journal_create'),
    path('bulk/',         views.bulk_create,    name='bulk_create'),
    path('ledger/',       views.general_ledger, name='general_ledger'),
    path('sub-ledger/',   views.sub_ledger,     name='sub_ledger'),
    path('balance-check/',views.balance_check,  name='balance_check'),
]

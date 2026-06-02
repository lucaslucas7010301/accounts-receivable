from django.urls import path

from . import views

app_name = 'accounting'

urlpatterns = [
    path('',                  views.dashboard,      name='dashboard'),
    path('clients/',          views.client_list,    name='client_list'),
    path('followups/',        views.followups,      name='followups'),
    path('allocation/',       views.allocation,     name='allocation'),
    path('monthly/',          views.monthly_close,  name='monthly_close'),
    path('imports/',          views.import_status,  name='import_status'),
    path('clients/<str:code>/', views.client_detail, name='client_detail'),
]

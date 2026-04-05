from django.urls import path
from . import views

app_name = 'configs'

urlpatterns = [
    path('global/', views.GlobalConfigView.as_view(), name='global-config'),
    path('admin/list/', views.AdminConfigListView.as_view(), name='admin-config-list'),
    path('admin/update/', views.AdminConfigUpdateView.as_view(), name='admin-config-update'),
]

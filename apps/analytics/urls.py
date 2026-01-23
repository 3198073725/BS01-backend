"""Analytics 应用路由模块。

用于组织埋点/统计相关 API 路由，例如事件上报、指标查询等。
"""

from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('events/', views.EventsIngestView.as_view(), name='events'),
]

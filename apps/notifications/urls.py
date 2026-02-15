"""Notifications 应用路由模块。

用于组织通知相关 API 路由，例如系统通知、消息列表、已读状态更新等。
后续可在此导入 views 并定义 path 列表。
"""

from django.urls import path
from apps.interactions import views as iviews
from . import views

app_name = 'notifications'

urlpatterns = [
    # 对接 interactions.views 中的通知视图，提供 /api/notifications/* 路由
    path('', iviews.NotificationsListView.as_view(), name='notifications-list'),
    path('mark-read/', iviews.NotificationsMarkReadView.as_view(), name='notifications-mark-read'),
    path('mark-all-read/', iviews.NotificationsMarkAllReadView.as_view(), name='notifications-mark-all-read'),
    path('clear/', iviews.NotificationsClearAllView.as_view(), name='notifications-clear'),
    path('unread-count/', iviews.NotificationsUnreadCountView.as_view(), name='notifications-unread-count'),

    # 系统公告（全站系统消息）
    path('announcements/', views.AnnouncementsListView.as_view(), name='announcements-list'),
    path('announcements/unread-count/', views.AnnouncementsUnreadCountView.as_view(), name='announcements-unread-count'),
    path('announcements/latest-unread/', views.AnnouncementsLatestUnreadView.as_view(), name='announcements-latest-unread'),
    path('announcements/<uuid:pk>/', views.AnnouncementDetailView.as_view(), name='announcements-detail'),
    path('announcements/<uuid:pk>/read/', views.AnnouncementMarkReadView.as_view(), name='announcements-mark-read'),
]

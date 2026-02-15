"""Interactions 应用路由模块。

用于组织互动相关 API 路由，例如点赞、评论、收藏、关注等。
后续可在此导入 views 并定义 path 列表。
"""

from django.urls import path
from . import views

app_name = 'interactions'

urlpatterns = [
    # 关注/取关
    path('follow/', views.FollowCreateView.as_view(), name='follow'),
    path('unfollow/', views.UnfollowView.as_view(), name='unfollow'),
    # 列表
    path('followers/', views.FollowersListView.as_view(), name='followers'),
    path('following/', views.FollowingListView.as_view(), name='following'),
    path('likes/', views.LikesListView.as_view(), name='likes-list'),
    path('favorites/', views.FavoritesListView.as_view(), name='favorites-list'),
    path('watch-later/', views.WatchLaterListView.as_view(), name='watch-later-list'),
    path('watch-later/toggle/', views.WatchLaterToggleView.as_view(), name='watch-later-toggle'),
    path('history/', views.HistoryListView.as_view(), name='history-list'),
    path('history/record/', views.HistoryRecordView.as_view(), name='history-record'),
    path('relationship/', views.RelationshipView.as_view(), name='relationship'),
    # 批量操作
    path('likes/bulk-unlike/', views.LikesBulkUnlikeView.as_view(), name='likes-bulk-unlike'),
    path('favorites/bulk-remove/', views.FavoritesBulkRemoveView.as_view(), name='favorites-bulk-remove'),
    path('watch-later/bulk-remove/', views.WatchLaterBulkRemoveView.as_view(), name='watch-later-bulk-remove'),
    path('history/bulk-remove/', views.HistoryBulkRemoveView.as_view(), name='history-bulk-remove'),
    # 点赞/收藏切换
    path('like/toggle/', views.LikeToggleView.as_view(), name='like-toggle'),
    path('favorite/toggle/', views.FavoriteToggleView.as_view(), name='favorite-toggle'),
    # 评论
    path('comments/', views.CommentsListCreateView.as_view(), name='comments-list-create'),
    path('comments/replies/', views.CommentRepliesListView.as_view(), name='comments-replies'),
    path('comments/<uuid:pk>/', views.CommentDetailView.as_view(), name='comment-detail'),
    path('comments/<uuid:pk>/like/', views.CommentLikeToggleView.as_view(), name='comment-like-toggle'),
    # 通知
    path('notifications/', views.NotificationsListView.as_view(), name='notifications-list'),
    path('notifications/mark-read/', views.NotificationsMarkReadView.as_view(), name='notifications-mark-read'),
    path('notifications/mark-all-read/', views.NotificationsMarkAllReadView.as_view(), name='notifications-mark-all-read'),
    path('notifications/clear/', views.NotificationsClearAllView.as_view(), name='notifications-clear'),
    path('notifications/unread-count/', views.NotificationsUnreadCountView.as_view(), name='notifications-unread-count'),
]

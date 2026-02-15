from django.urls import path
from . import views

app_name = 'adminapi'

urlpatterns = [
    path('users/', views.AdminUsersListView.as_view(), name='users-list'),
    path('users/<uuid:pk>/', views.AdminUserDetailView.as_view(), name='users-detail'),
    path('users/<uuid:pk>/force-logout/', views.AdminUserForceLogoutView.as_view(), name='users-force-logout'),

    path('videos/', views.AdminVideosListView.as_view(), name='videos-list'),
    path('videos/<uuid:pk>/', views.AdminVideoDetailView.as_view(), name='videos-detail'),
    path('videos/bulk-update/', views.AdminVideosBulkUpdateView.as_view(), name='videos-bulk-update'),
    path('videos/bulk-delete/', views.AdminVideosBulkDeleteView.as_view(), name='videos-bulk-delete'),
    path('videos/batch-approve/', views.AdminVideosBatchApproveView.as_view(), name='videos-batch-approve'),
    path('videos/transcode-failures/', views.AdminVideosTranscodeFailuresView.as_view(), name='videos-transcode-failures'),
    path('videos/metrics-trend/', views.AdminVideosMetricsTrendView.as_view(), name='videos-metrics-trend'),

    path('comments/', views.AdminCommentsListView.as_view(), name='comments-list'),
    path('comments/<uuid:pk>/', views.AdminCommentDetailView.as_view(), name='comments-detail'),

    path('me/', views.AdminMeView.as_view(), name='me'),

    # Audit logs
    path('audit-logs/', views.AdminAuditLogsListView.as_view(), name='audit-logs-list'),

    # Categories
    path('categories/', views.AdminCategoriesListView.as_view(), name='categories-list'),
    path('categories/<uuid:pk>/', views.AdminCategoryDetailView.as_view(), name='categories-detail'),

    # Tags
    path('tags/', views.AdminTagsListView.as_view(), name='tags-list'),
    path('tags/<uuid:pk>/', views.AdminTagDetailView.as_view(), name='tags-detail'),
    path('tags/bulk-delete/', views.AdminTagsBulkDeleteView.as_view(), name='tags-bulk-delete'),
    path('tags/merge/', views.AdminTagsMergeView.as_view(), name='tags-merge'),

    # Analytics
    path('analytics/overview/', views.AdminAnalyticsOverviewView.as_view(), name='analytics-overview'),

    # System announcements
    path('announcements/', views.AdminAnnouncementsListCreateView.as_view(), name='announcements-list-create'),
    path('announcements/<uuid:pk>/', views.AdminAnnouncementDetailView.as_view(), name='announcements-detail'),
]

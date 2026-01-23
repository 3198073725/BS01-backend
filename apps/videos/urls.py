"""Videos 应用路由模块。

用于组织视频相关 API 路由，例如视频上传、列表、详情、播放记录等。
"""

from django.urls import path
from . import views

app_name = 'videos'

urlpatterns = [
    path('upload/', views.VideoUploadView.as_view(), name='upload'),
    path('upload/init/', views.UploadInitView.as_view(), name='upload-init'),
    path('upload/chunk/', views.UploadChunkView.as_view(), name='upload-chunk'),
    path('upload/status/', views.UploadStatusView.as_view(), name='upload-status'),
    path('upload/complete/', views.UploadCompleteView.as_view(), name='upload-complete'),
    path('list/', views.VideoListView.as_view(), name='list'),
    path('<uuid:pk>/', views.VideoDetailView.as_view(), name='detail'),
    path('bulk-delete/', views.VideoBulkDeleteView.as_view(), name='bulk-delete'),
]

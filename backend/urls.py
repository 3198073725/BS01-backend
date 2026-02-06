"""
URL configuration for backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.views.static import serve as static_serve
from rest_framework_simplejwt.views import (
    TokenVerifyView,
)
from apps.users.views import TokenObtainPairViewWithCooldown, TokenRefreshViewWithRevoke
import os


def health(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [
    # Django 管理后台
    path('admin/', admin.site.urls),

    # 各业务子应用 API 前缀
    path('api/health/', health, name='health'),
    path('api/users/', include('apps.users.urls')),
    path('api/admin/', include('apps.adminapi.urls')),
    path('api/videos/', include('apps.videos.urls')),
    path('api/interactions/', include('apps.interactions.urls')),
    path('api/content/', include('apps.content.urls')),
    path('api/recommendation/', include('apps.recommendation.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
    path('api/analytics/', include('apps.analytics.urls')),

    # DRF 浏览器可视化登录（可用于开发调试 Session 登录）
    path('api-auth/', include('rest_framework.urls')),

    # JWT 鉴权端点（Obtain/Refresh/Verify）
    path('api/token/', TokenObtainPairViewWithCooldown.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshViewWithRevoke.as_view(), name='token_refresh'),
    path('api/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
]

if settings.DEBUG or str(os.getenv('SERVE_MEDIA', 'false')).lower() in ('true','1','yes'):
    # 使用 Django 提供的静态文件视图服务媒体文件（仅开发/内网调试场景）
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', static_serve, {
            'document_root': settings.MEDIA_ROOT,
            'show_indexes': False,
        }),
    ]

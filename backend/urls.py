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
from django.http import JsonResponse, FileResponse, Http404, HttpResponse
from django.utils._os import safe_join
from django.utils.http import http_date
from django.views.static import serve as static_serve
from rest_framework_simplejwt.views import (
    TokenVerifyView,
)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from apps.users.views import TokenObtainPairViewWithCooldown, TokenRefreshViewWithRevoke
import os
import mimetypes
import re


def _is_owner_or_admin(video, viewer) -> bool:
    try:
        if not viewer or not getattr(viewer, 'id', None):
            return False
        if getattr(viewer, 'is_staff', False):
            return True
        return str(viewer.id) == str(getattr(video, 'user_id', None))
    except Exception:
        return False


def _can_view_video(video, viewer) -> bool:
    try:
        status = getattr(video, 'status', 'draft')
        if status == 'banned':
            return bool(viewer and getattr(viewer, 'is_staff', False))
        if status != 'published' and not _is_owner_or_admin(video, viewer):
            return False
        if getattr(video, 'visibility', 'public') == 'private' and not _is_owner_or_admin(video, viewer):
            return False
        return True
    except Exception:
        return False


def _extract_video_key_from_media_path(path: str):
    parts = [p for p in str(path or '').split('/') if p]
    if len(parts) < 2 or parts[0] != 'videos':
        return None
    if parts[1] == 'hls' and len(parts) >= 3:
        return parts[2]
    if parts[1] == 'low' and len(parts) >= 3:
        return os.path.splitext(parts[2])[0]
    if parts[1] == 'thumbs' and len(parts) >= 3:
        if len(parts) >= 4 and parts[2].endswith('_vtt'):
            return parts[2][:-4]
        stem = os.path.splitext(parts[2])[0]
        if stem.endswith('_custom'):
            stem = stem[:-7]
        return stem
    return os.path.splitext(parts[-1])[0]


def _video_from_media_path(path: str):
    key = _extract_video_key_from_media_path(path)
    if not key:
        return None
    from django.db.models import Q
    from apps.videos.models import Video

    candidates = Video.objects.filter(
        Q(video_file__icontains=key) | Q(video_file_f__icontains=key)
    ).only('id', 'user_id', 'status', 'visibility', 'video_file', 'video_file_f')
    for video in candidates:
        rel = getattr(getattr(video, 'video_file_f', None), 'name', None) or getattr(video, 'video_file', '')
        if os.path.splitext(os.path.basename(str(rel or '')))[0] == key:
            return video
    return None


def health(request):
    return JsonResponse({"status": "ok"})


def _media_content_type(fullpath: str) -> tuple[str, str | None]:
    ext = os.path.splitext(fullpath)[1].lower()
    if ext == '.m3u8':
        return 'application/vnd.apple.mpegurl', None
    if ext == '.ts':
        return 'video/mp2t', None
    if ext == '.mpd':
        return 'application/dash+xml', None
    if ext == '.vtt':
        return 'text/vtt', None
    ctype, encoding = mimetypes.guess_type(fullpath)
    return ctype or 'application/octet-stream', encoding


def media_serve_with_range(request, path, document_root=None):
    """Serve media files with HTTP Range support.

    django.views.static.serve does not implement Range requests.
    Mobile video players often require Range (206) for MP4 playback.
    """
    document_root = document_root or settings.MEDIA_ROOT
    try:
        fullpath = safe_join(document_root, path)
    except Exception:
        raise Http404("Not Found")

    video = _video_from_media_path(path)
    if video is not None:
        viewer = getattr(request, 'user', None)
        if not _can_view_video(video, viewer):
            raise Http404("Not Found")

    if not os.path.exists(fullpath) or not os.path.isfile(fullpath):
        raise Http404("Not Found")

    file_size = os.path.getsize(fullpath)
    content_type, encoding = _media_content_type(fullpath)
    range_header = request.META.get('HTTP_RANGE', '')

    # Always advertise Range support.
    def _set_common_headers(resp: HttpResponse) -> HttpResponse:
        resp['Accept-Ranges'] = 'bytes'
        try:
            resp['Last-Modified'] = http_date(os.path.getmtime(fullpath))
        except Exception:
            pass
        if encoding:
            resp['Content-Encoding'] = encoding
        return resp

    if not range_header:
        resp = FileResponse(open(fullpath, 'rb'), content_type=content_type)
        resp['Content-Length'] = str(file_size)
        return _set_common_headers(resp)

    m = re.match(r'^bytes=(\d*)-(\d*)$', range_header.strip())
    if not m:
        resp = HttpResponse(status=416)
        resp['Content-Range'] = f'bytes */{file_size}'
        return _set_common_headers(resp)

    start_str, end_str = m.group(1), m.group(2)
    if start_str == '' and end_str == '':
        resp = HttpResponse(status=416)
        resp['Content-Range'] = f'bytes */{file_size}'
        return _set_common_headers(resp)

    if start_str == '':
        # suffix range: last N bytes
        length = int(end_str)
        if length <= 0:
            resp = HttpResponse(status=416)
            resp['Content-Range'] = f'bytes */{file_size}'
            return _set_common_headers(resp)
        start = max(0, file_size - length)
        end = file_size - 1
    else:
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1

    if start >= file_size or start < 0 or end < start:
        resp = HttpResponse(status=416)
        resp['Content-Range'] = f'bytes */{file_size}'
        return _set_common_headers(resp)

    end = min(end, file_size - 1)
    length = (end - start) + 1

    def file_iter(fp, offset: int, count: int, chunk_size: int = 8192):
        fp.seek(offset)
        remaining = count
        while remaining > 0:
            data = fp.read(min(chunk_size, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data

    fp = open(fullpath, 'rb')
    resp = HttpResponse(file_iter(fp, start, length), status=206, content_type=content_type)
    resp['Content-Length'] = str(length)
    resp['Content-Range'] = f'bytes {start}-{end}/{file_size}'
    return _set_common_headers(resp)

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
    path('api/configs/', include('apps.configs.urls')),

    # DRF 浏览器可视化登录（可用于开发调试 Session 登录）
    path('api-auth/', include('rest_framework.urls')),

    # JWT 鉴权端点（Obtain/Refresh/Verify）
    path('api/token/', TokenObtainPairViewWithCooldown.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshViewWithRevoke.as_view(), name='token_refresh'),
    path('api/token/verify/', TokenVerifyView.as_view(), name='token_verify'),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG or str(os.getenv('SERVE_MEDIA', 'false')).lower() in ('true','1','yes'):
    # 使用 Django 提供的静态文件视图服务媒体文件（仅开发/内网调试场景）
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', media_serve_with_range, {
            'document_root': settings.MEDIA_ROOT,
        }),
    ]

# Serve static files in production (for API docs, etc.)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

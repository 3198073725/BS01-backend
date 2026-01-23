"""Analytics 视图模块。

事件上报/统计相关 API。
"""

from __future__ import annotations
from typing import Any, Iterable
from django.core.cache import cache
from django.utils import timezone
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError

from apps.videos.models import Video
from django.db.models import F


class EventsIngestView(APIView):
    """埋点事件上报

    - 方法：POST /api/analytics/events/
    - 接受：单个事件对象或事件数组
      事件示例：{"type":"video_play","video_id":"<uuid>","session_id":"<sid>","ts": 1730000000}
    - 权限：允许匿名
    - 节流：analytics（settings.DEFAULT_THROTTLE_RATES）
    - 行为：
      * 对 `video_play|video_view|play` 事件，按 (video_id, session_id/ip) 做去重后累计 view_count
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'analytics'

    def post(self, request):
        payload = request.data
        events: list[dict[str, Any]]
        if isinstance(payload, dict):
            events = [payload]
        elif isinstance(payload, list):
            events = [e for e in payload if isinstance(e, dict)]
        else:
            raise ValidationError({'detail': '请求体应为对象或对象数组'})

        # dedupe 视图的时间窗口（秒）
        dedupe_ttl = 6 * 3600
        ip = (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR') or 'unknown').lower()

        updated = 0
        for ev in events:
            etype = str(ev.get('type') or '').lower()
            if etype in ('video_play', 'video_view', 'play'):
                vid = ev.get('video_id') or ev.get('video') or ev.get('target_id')
                if not vid:
                    continue
                sid = ev.get('session_id') or request.headers.get('X-Session-Id') or ip
                key = f"view_once:{vid}:{sid}"
                try:
                    created = cache.add(key, 1, timeout=dedupe_ttl)
                    if not created:
                        continue
                    Video.objects.filter(pk=vid).update(view_count=F('view_count') + 1)
                    updated += 1
                except Exception:
                    continue

        # 返回 204 更简单；为便于诊断，这里返回处理条数
        return Response({'updated': updated}, status=status.HTTP_200_OK)

"""Analytics 视图模块。

用于实现埋点/统计相关的 API 视图，例如事件上报、指标查询等。
后续可结合 DRF 的 APIView/ViewSet 来定义接口，并在 urls 中进行路由绑定。
"""

from django.shortcuts import render

# 在此编写视图，例如：
# from rest_framework.views import APIView
# from rest_framework.response import Response
#
# class HealthView(APIView):
#     def get(self, request):
#         return Response({"status": "ok"})

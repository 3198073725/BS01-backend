"""Notifications 视图模块。"""

from __future__ import annotations

from django.db.models import Exists, OuterRef

from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from backend.common.pagination import StandardResultsSetPagination

from .models import SystemAnnouncement, SystemAnnouncementRead


def _bool(v) -> bool:
    try:
        s = str(v).strip().lower()
    except Exception:
        return False
    return s in ('1', 'true', 'yes', 'y', 'on')


class AnnouncementsListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        include_inactive = _bool(request.query_params.get('include_inactive'))
        p = StandardResultsSetPagination()
        qs = SystemAnnouncement.objects.all()
        if not include_inactive:
            qs = qs.filter(is_active=True)

        read_exists = SystemAnnouncementRead.objects.filter(
            user=request.user,
            announcement_id=OuterRef('pk'),
        )
        qs = qs.annotate(is_read=Exists(read_exists)).order_by('-pinned', '-published_at', '-created_at')

        rows = list(p.paginate_queryset(qs, request, view=self))
        out = []
        for a in rows:
            out.append({
                'id': str(a.id),
                'title': a.title,
                'content': a.content,
                'is_active': bool(a.is_active),
                'pinned': bool(a.pinned),
                'published_at': a.published_at,
                'created_at': a.created_at,
                'updated_at': a.updated_at,
                'is_read': bool(getattr(a, 'is_read', False)),
            })

        total = getattr(p.page.paginator, 'count', None)
        return Response(p.format(out, total))


class AnnouncementDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        a = SystemAnnouncement.objects.filter(pk=pk).first()
        if not a:
            raise ValidationError({'detail': '公告不存在'})
        is_read = SystemAnnouncementRead.objects.filter(user=request.user, announcement=a).exists()
        return Response({
            'id': str(a.id),
            'title': a.title,
            'content': a.content,
            'is_active': bool(a.is_active),
            'pinned': bool(a.pinned),
            'published_at': a.published_at,
            'created_at': a.created_at,
            'updated_at': a.updated_at,
            'is_read': bool(is_read),
        })


class AnnouncementMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        a = SystemAnnouncement.objects.filter(pk=pk).first()
        if not a:
            raise ValidationError({'detail': '公告不存在'})
        SystemAnnouncementRead.objects.get_or_create(announcement=a, user=request.user)
        return Response({'ok': True})


class AnnouncementsUnreadCountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = SystemAnnouncement.objects.filter(is_active=True)
        read_exists = SystemAnnouncementRead.objects.filter(user=request.user, announcement_id=OuterRef('pk'))
        qs = qs.annotate(is_read=Exists(read_exists)).filter(is_read=False)
        return Response({'unread': int(qs.count())})


class AnnouncementsLatestUnreadView(APIView):
    """获取最新一条未读公告（用于 Web 启动弹窗）。"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = SystemAnnouncement.objects.filter(is_active=True)
        read_exists = SystemAnnouncementRead.objects.filter(user=request.user, announcement_id=OuterRef('pk'))
        qs = qs.annotate(is_read=Exists(read_exists)).filter(is_read=False)
        a = qs.order_by('-pinned', '-published_at', '-created_at').first()
        if not a:
            return Response({'announcement': None})
        return Response({
            'announcement': {
                'id': str(a.id),
                'title': a.title,
                'content': a.content,
                'pinned': bool(a.pinned),
                'published_at': a.published_at,
            }
        })

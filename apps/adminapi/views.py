from __future__ import annotations
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Count, Sum
from django.db.models.functions import TruncDate

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.core.cache import cache
from django.conf import settings
from django.core.files.storage import default_storage
import time
import uuid

from backend.common.pagination import StandardResultsSetPagination
from apps.users.models import User
from apps.videos.models import Video, VideoTag
from apps.interactions.models import Comment, History, Like
from apps.content.models import AuditLog, Category, Tag
from apps.content.moderation import check_zhipu_moderation
from apps.content.comment_moderation_rules import (
    COMMENT_PATTERN_RULES,
    COMMENT_TEXT_CANONICAL_RULES,
    DEFAULT_COMMENT_BLOCKED_KEYWORDS,
)
from apps.notifications.models import SystemAnnouncement
from apps.configs.utils import get_system_setting, set_config
from backend.system_events import publish_config_updated
from .permissions import IsReviewer, IsModerator, IsAdmin, IsSuperAdmin, CanHandleReport


def _refresh_lifetime_seconds() -> int:
    try:
        days = int(get_system_setting('REFRESH_TOKEN_LIFETIME_DAYS', 60) or 60)
        return max(1, days) * 24 * 3600
    except Exception:
        td = settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME')
        return int(getattr(td, 'total_seconds')()) if td else 3600


def _apply_refresh_lifetime(refresh):
    try:
        days = int(get_system_setting('REFRESH_TOKEN_LIFETIME_DAYS', 60) or 60)
        refresh.set_exp(lifetime=timezone.timedelta(days=max(1, days)))
    except Exception:
        pass
    return refresh


def _parse_bool(v) -> bool | None:
    if v is None:
        return None
    try:
        s = str(v).strip().lower()
    except Exception:
        return None
    if s in ("1", "true", "yes", "y", "on"):  # noqa: E712
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return None


def _audit(request, verb: str, target_type: str | None = None, target_id: str | None = None, meta: dict | None = None):
    try:
        AuditLog.objects.create(
            actor=getattr(request, 'user', None),
            verb=verb,
            target_type=target_type,
            target_id=target_id,
            meta=meta or {},
        )
    except Exception:
        pass


def _report_target_allowed_actions(target_type: str) -> set[str]:
    mapping = {
        'video': {'dismiss', 'warn', 'delete_content', 'escalate'},
        'comment': {'dismiss', 'warn', 'delete_content', 'escalate'},
        'user': {'dismiss', 'warn', 'ban_user', 'escalate'},
    }
    return mapping.get(str(target_type or '').strip().lower(), set())


def _can_handle_escalated_report(user, report) -> bool:
    role = getattr(user, 'admin_role', 'none')
    if report.target_type == 'user':
        return role in {'admin', 'super_admin'}
    return role in {'moderator', 'admin', 'super_admin'}


def _can_escalate_report(user, report) -> bool:
    role = getattr(user, 'admin_role', 'none')
    if role == 'reviewer':
        return True
    if role == 'moderator':
        return report.target_type == 'user'
    return False

from drf_spectacular.utils import extend_schema, extend_schema_view
from apps.users.serializers import UserMeSerializer, UserFollowListSerializer

@extend_schema_view(
    get=extend_schema(responses={200: UserFollowListSerializer(many=True)}),
)
class AdminUsersListView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = User.objects.all()
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(nickname__icontains=q) | Q(email__icontains=q))
        for field in ('is_active', 'is_verified', 'is_creator'):
            v = _parse_bool(request.query_params.get(field))
            if v is not None:
                qs = qs.filter(**{field: v})
        # admin_role filter
        admin_role = (request.query_params.get('admin_role') or '').strip()
        if admin_role:
            qs = qs.filter(admin_role=admin_role)
        order = (request.query_params.get('order') or '').strip().lower()
        if order == 'oldest':
            qs = qs.order_by('date_joined')
        elif order == 'popular':
            qs = qs.order_by('-followers_count', '-date_joined')
        else:
            qs = qs.order_by('-date_joined')

        # Annotate with accurate video count to ensure correctness in admin stats
        try:
            qs = qs.annotate(video_count_calc=Count('videos'))
        except Exception:
            pass

        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = []
        for u in rows:
            data.append({
                'id': str(u.id),
                'username': u.username,
                'email': u.email,
                'nickname': u.nickname,
                'is_active': bool(u.is_active),
                'is_verified': bool(u.is_verified),
                'is_creator': bool(u.is_creator),
                'is_staff': bool(u.is_staff),
                'admin_role': u.admin_role or 'none',
                'followers_count': u.followers_count,
                'following_count': u.following_count,
                'video_count': getattr(u, 'video_count_calc', u.video_count),
                'date_joined': u.date_joined,
                'last_active': u.last_active,
            })
        return Response(p.format(data, total))


@extend_schema_view(
    get=extend_schema(responses={200: UserMeSerializer}),
)
class AdminUserDetailView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [IsAdmin()]
        return [IsReviewer()]

    def get(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        data = {
            'id': str(u.id),
            'username': u.username,
            'email': u.email,
            'nickname': u.nickname,
            'is_active': bool(u.is_active),
            'is_verified': bool(u.is_verified),
            'is_creator': bool(u.is_creator),
            'is_staff': bool(u.is_staff),
            'admin_role': u.admin_role or 'none',
            'followers_count': u.followers_count,
            'following_count': u.following_count,
            'video_count': u.video_count,
            'date_joined': u.date_joined,
            'last_active': u.last_active,
        }
        return Response(data)

    def patch(self, request, pk):
        u = get_object_or_404(User, pk=pk)
        body = request.data or {}
        updates: dict[str, object] = {}
        if 'is_active' in body:
            v = body.get('is_active')
            if not isinstance(v, bool):
                vv = _parse_bool(v)
                if vv is None:
                    raise ValidationError({'is_active': '必须为布尔值'})
                v = vv
            updates['is_active'] = bool(v)
        if 'is_verified' in body:
            v = body.get('is_verified')
            vv = v if isinstance(v, bool) else _parse_bool(v)
            if vv is None:
                raise ValidationError({'is_verified': '必须为布尔值'})
            updates['is_verified'] = bool(vv)
        if 'is_creator' in body:
            v = body.get('is_creator')
            vv = v if isinstance(v, bool) else _parse_bool(v)
            if vv is None:
                raise ValidationError({'is_creator': '必须为布尔值'})
            updates['is_creator'] = bool(vv)
        if 'is_staff' in body:
            # 仅超级管理员可调整 is_staff
            if getattr(request.user, 'admin_role', 'none') != 'super_admin':
                raise PermissionDenied('仅超级管理员可修改 is_staff')
            v = body.get('is_staff')
            vv = v if isinstance(v, bool) else _parse_bool(v)
            if vv is None:
                raise ValidationError({'is_staff': '必须为布尔值'})
            updates['is_staff'] = bool(vv)
        if 'admin_role' in body:
            # 仅超级管理员可调整 admin_role
            if getattr(request.user, 'admin_role', 'none') != 'super_admin':
                raise PermissionDenied('仅超级管理员可修改管理员角色')
            role = str(body.get('admin_role') or '').strip()
            valid_roles = ['none', 'reviewer', 'moderator', 'admin', 'super_admin']
            if role not in valid_roles:
                raise ValidationError({'admin_role': f'非法值，可选: {", ".join(valid_roles)}'})
            updates['admin_role'] = role

        if not updates:
            return Response({'updated': 0})

        for k, v in updates.items():
            setattr(u, k, v)
        # 总是更新 updated_at（模型上已有 auto_now）；这里只列出具体变更字段
        fields = list(updates.keys()) + ['updated_at']
        u.save(update_fields=list(dict.fromkeys(fields)))
        try:
            _audit(request, 'user.update', 'user', str(u.id), {'fields': list(updates.keys())})
        except Exception:
            pass
        return Response({'updated': 1})


class AdminVideosListView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = Video.objects.all().select_related('user', 'category').prefetch_related('video_tags__tag')
        # base/media for URL building
        base = (get_system_setting('SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        def to_url(rel: str):
            if not rel:
                return None
            try:
                u = default_storage.url(rel)
            except Exception:
                u = None
            if u and (u.startswith('http://') or u.startswith('https://')):
                return u
            return url_of(rel)
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(user__username__icontains=q))
        user_id = request.query_params.get('user_id')
        if user_id:
            try:
                _ = uuid.UUID(str(user_id))
                qs = qs.filter(user_id=user_id)
            except Exception:
                pass
        # owner verification filter
        owner_v = _parse_bool(request.query_params.get('owner_verified'))
        if owner_v is not None:
            qs = qs.filter(user__is_verified=bool(owner_v))
        status_q = (request.query_params.get('status') or '').strip()
        if status_q:
            qs = qs.filter(status=status_q)
        vis = (request.query_params.get('visibility') or '').strip()
        if vis:
            qs = qs.filter(visibility=vis)
        is_feat = _parse_bool(request.query_params.get('is_featured'))
        if is_feat is not None:
            qs = qs.filter(is_featured=bool(is_feat))
        order = (request.query_params.get('order') or 'latest').strip().lower()
        if order == 'hot':
            qs = qs.order_by('-like_count', '-view_count', '-published_at', '-created_at')
        elif order == 'earliest':
            qs = qs.order_by('created_at')
        elif order == 'published':
            qs = qs.order_by('-published_at', '-created_at')
        else:
            qs = qs.order_by('-created_at')

        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count

        def user_obj(u: User):
            pp_name = getattr(getattr(u, 'profile_picture_f', None), 'name', None)
            pp = pp_name or getattr(u, 'profile_picture', None)
            return {
                'id': str(u.id),
                'username': getattr(u, 'username', ''),
                'is_verified': bool(getattr(u, 'is_verified', False)),
                'avatar_url': (to_url(pp) if pp else None),
            }

        data = []
        for v in rows:
            tags = []
            try:
                for vt in list(getattr(v, 'video_tags').all()):
                    t = getattr(vt, 'tag', None)
                    if t:
                        tags.append({'id': str(t.id), 'name': t.name})
            except Exception:
                tags = []
            # thumbnail absolute url (if available)
            thumb_rel = (getattr(v.thumbnail_f, 'name', None) or v.thumbnail)
            thumb_url = to_url(thumb_rel) if thumb_rel else None
            data.append({
                'id': str(v.id),
                'title': v.title,
                'status': v.status,
                'transcode_error': getattr(v, 'transcode_error', None),
                'visibility': v.visibility,
                'is_featured': bool(getattr(v, 'is_featured', False)),
                'allow_comments': bool(v.allow_comments),
                'allow_download': bool(v.allow_download),
                'category': ({'id': str(v.category.id), 'name': v.category.name} if getattr(v, 'category', None) else None),
                'tags': tags,
                'owner': user_obj(v.user) if getattr(v, 'user', None) else None,
                'thumbnail_url': thumb_url,
                'view_count': v.view_count,
                'like_count': v.like_count,
                'comment_count': v.comment_count,
                'created_at': v.created_at,
                'published_at': v.published_at,
            })
        return Response(p.format(data, total))


class AdminVideoDetailView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method == 'DELETE':
            return [IsModerator()]
        return [IsReviewer()]

    def _ensure_video_patch_role(self, request, data):
        status_only_fields = {'status'}
        admin_fields = {
            'title', 'description', 'allow_comments', 'allow_download',
            'is_featured', 'visibility', 'category_id', 'tag_ids',
        }
        provided = {k for k in admin_fields | status_only_fields if k in data}
        if not provided:
            return
        if provided - status_only_fields:
            if not IsAdmin().has_permission(request, self):
                raise PermissionDenied('仅管理员可修改视频运营信息')

    def get(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        u = getattr(v, 'user', None)
        # tags
        tags = []
        try:
            for vt in list(getattr(v, 'video_tags').select_related('tag').all()):
                t = getattr(vt, 'tag', None)
                if t:
                    tags.append({'id': str(t.id), 'name': t.name})
        except Exception:
            tags = []
        return Response({
            'id': str(v.id),
            'title': v.title,
            'description': v.description,
            'status': v.status,
            'visibility': v.visibility,
            'allow_comments': bool(v.allow_comments),
            'allow_download': bool(v.allow_download),
            'category': ({'id': str(v.category.id), 'name': v.category.name} if getattr(v, 'category', None) else None),
            'tags': tags,
            'owner': ({'id': str(u.id), 'username': getattr(u, 'username', ''), 'is_verified': bool(getattr(u, 'is_verified', False))} if u else None),
            'created_at': v.created_at,
            'published_at': v.published_at,
            'view_count': v.view_count,
            'like_count': v.like_count,
            'comment_count': v.comment_count,
        })

    def patch(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        data = request.data or {}
        self._ensure_video_patch_role(request, data)
        updates: dict[str, object] = {}
        tags_changed = False
        if 'title' in data and isinstance(data.get('title'), str):
            updates['title'] = (data.get('title') or '').strip()[:200]
        if 'description' in data and isinstance(data.get('description'), str):
            updates['description'] = (data.get('description') or '').strip()[:500]
        if 'allow_comments' in data:
            bv = data.get('allow_comments') if isinstance(data.get('allow_comments'), bool) else _parse_bool(data.get('allow_comments'))
            if bv is None:
                raise ValidationError({'allow_comments': '必须为布尔值'})
            updates['allow_comments'] = bool(bv)
        if 'allow_download' in data:
            bv = data.get('allow_download') if isinstance(data.get('allow_download'), bool) else _parse_bool(data.get('allow_download'))
            if bv is None:
                raise ValidationError({'allow_download': '必须为布尔值'})
            updates['allow_download'] = bool(bv)
        if 'is_featured' in data:
            bv = data.get('is_featured') if isinstance(data.get('is_featured'), bool) else _parse_bool(data.get('is_featured'))
            if bv is None:
                raise ValidationError({'is_featured': '必须为布尔值'})
            updates['is_featured'] = bool(bv)
        if 'visibility' in data:
            vis = str(data.get('visibility') or '').strip()
            if vis not in {'public', 'unlisted', 'private'}:
                raise ValidationError({'visibility': '取值无效'})
            updates['visibility'] = vis
        if 'status' in data:
            st = str(data.get('status') or '').strip()
            if st not in {'draft', 'processing', 'published', 'banned'}:
                raise ValidationError({'status': '取值无效'})
            # 发布前校验：作者邮箱需已验证（这里复用 is_verified 字段）
            if st == 'published':
                try:
                    owner = getattr(v, 'user', None)
                    if not owner:
                        owner = User.objects.only('id','is_verified').get(pk=v.user_id)
                    if not bool(getattr(owner, 'is_verified', False)):
                        raise ValidationError({'status': '作者邮箱未验证，不能发布'})
                except ValidationError:
                    raise
                except Exception:
                    raise ValidationError({'status': '发布校验失败'})
            updates['status'] = st
            if st == 'published':
                # 发布则设置发布时间
                if not v.published_at:
                    updates['published_at'] = timezone.now()
            else:
                # 非发布状态则清空发布时间
                updates['published_at'] = None
        if 'category_id' in data:
            raw = data.get('category_id')
            cid = (str(raw).strip() if raw is not None else '')
            if cid in ('', 'null'):
                updates['category'] = None
            else:
                try:
                    c = Category.objects.get(pk=cid)
                except Category.DoesNotExist:
                    raise ValidationError({'category_id': '分类不存在'})
                updates['category'] = c
        if 'tag_ids' in data:
            tag_ids = data.get('tag_ids')
            if tag_ids is None:
                pass
            elif not isinstance(tag_ids, list):
                raise ValidationError({'tag_ids': '必须为数组'})
            else:
                ids = [str(i) for i in tag_ids if str(i)]
                if len(ids) > 100:
                    raise ValidationError({'tag_ids': '数量过多'})
                exist_ids = set(str(tid) for tid in Tag.objects.filter(id__in=ids).values_list('id', flat=True))
                cur_ids = set(str(tid) for tid in v.video_tags.values_list('tag_id', flat=True))
                add_ids = list(exist_ids - cur_ids)
                del_ids = list(cur_ids - exist_ids)
                if add_ids:
                    VideoTag.objects.bulk_create([VideoTag(video=v, tag_id=tid) for tid in add_ids], ignore_conflicts=True)
                if del_ids:
                    VideoTag.objects.filter(video=v, tag_id__in=del_ids).delete()
                tags_changed = True

        if not updates:
            # 仅标签变化也属于更新
            if tags_changed:
                try:
                    _audit(request, 'video.update', 'video', str(v.id), {'fields': ['tags']})
                except Exception:
                    pass
                return Response({'updated': 1})
            return Response({'updated': 0})

        for k, val in updates.items():
            setattr(v, k, val)
        fields = list(updates.keys()) + ['updated_at']
        v.save(update_fields=list(dict.fromkeys(fields)))
        try:
            _audit(request, 'video.update', 'video', str(v.id), {'fields': list(updates.keys()) + (['tags'] if tags_changed else [])})
        except Exception:
            pass
        return Response({'updated': 1})

    def delete(self, request, pk):
        v = get_object_or_404(Video, pk=pk)
        vid = str(v.id)
        v.delete()
        try:
            _audit(request, 'video.delete', 'video', vid, None)
        except Exception:
            pass
        return Response({'removed': 1})


class AdminAnalyticsOverviewView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        rng = (request.query_params.get('range') or '7d').lower().strip()
        days_map = {'7d': 7, '30d': 30, '90d': 90}
        days = days_map.get(rng, 7)
        now = timezone.now()
        since = now - timezone.timedelta(days=days)
        # 用本地日期做日粒度序列边界
        today = timezone.localdate()
        start_date = today - timezone.timedelta(days=days - 1)

        # Totals
        users_total = User.objects.all().count()
        videos_qs = Video.objects.all()
        videos_total = videos_qs.count()
        comments_total = Comment.objects.all().count()
        views_total = videos_qs.aggregate(s=Sum('view_count'))['s'] or 0

        # Deltas within range
        users_delta = User.objects.filter(date_joined__gte=since).count()
        videos_delta = videos_qs.filter(created_at__gte=since).count()
        comments_delta = Comment.objects.filter(created_at__gte=since).count()

        # Visibility distribution
        vis_rows = videos_qs.values('visibility').annotate(c=Count('id'))
        visibility = {'public': 0, 'unlisted': 0, 'private': 0}
        for it in vis_rows:
            v = (it.get('visibility') or '').strip()
            if v in visibility:
                visibility[v] = int(it.get('c') or 0)

        # Top categories
        cat_rows = (
            videos_qs.values('category__name')
            .annotate(c=Count('id'))
            .order_by('-c')[:5]
        )
        top_categories = []
        for it in cat_rows:
            name = it.get('category__name') or '未分类'
            top_categories.append({'name': name, 'count': int(it.get('c') or 0)})

        # 日趋势（用户/视频/评论/观看）
        # Users per day
        try:
            u_rows = (
                User.objects.filter(date_joined__date__gte=start_date, date_joined__date__lte=today)
                .annotate(d=TruncDate('date_joined'))
                .values('d')
                .annotate(c=Count('id'))
            )
            u_map = {str(it['d']): int(it['c'] or 0) for it in u_rows}
        except Exception:
            u_map = {}
        # Videos per day
        try:
            v_rows = (
                videos_qs.filter(created_at__date__gte=start_date, created_at__date__lte=today)
                .annotate(d=TruncDate('created_at'))
                .values('d')
                .annotate(c=Count('id'))
            )
            v_map = {str(it['d']): int(it['c'] or 0) for it in v_rows}
        except Exception:
            v_map = {}
        # Comments per day
        try:
            c_rows = (
                Comment.objects.filter(created_at__date__gte=start_date, created_at__date__lte=today)
                .annotate(d=TruncDate('created_at'))
                .values('d')
                .annotate(c=Count('id'))
            )
            c_map = {str(it['d']): int(it['c'] or 0) for it in c_rows}
        except Exception:
            c_map = {}

        # Views per day（使用 interactions_history 的新增记录数近似）
        try:
            vw_rows = (
                History.objects.filter(created_at__date__gte=start_date, created_at__date__lte=today)
                .annotate(d=TruncDate('created_at'))
                .values('d')
                .annotate(c=Count('id'))
            )
            vw_map = {str(it['d']): int(it['c'] or 0) for it in vw_rows}
        except Exception:
            vw_map = {}

        trend = []
        for i in range(days):
            d = start_date + timezone.timedelta(days=i)
            ds = d.isoformat()
            trend.append({
                'date': ds,
                'users': int(u_map.get(ds, 0)),
                'videos': int(v_map.get(ds, 0)),
                'comments': int(c_map.get(ds, 0)),
                'views': int(vw_map.get(ds, 0)),
            })

        # Top videos by views
        top_videos_qs = (
            videos_qs.only('id','title','view_count')
            .order_by('-view_count','-published_at','-created_at')[:5]
        )
        top_videos = [
            {'id': str(v.id), 'title': v.title, 'view_count': int(getattr(v, 'view_count', 0) or 0)}
            for v in top_videos_qs
        ]

        # Top users by video count
        try:
            top_users_qs = (
                User.objects.annotate(video_count_calc=Count('videos'))
                .values('id','username','video_count_calc')
                .order_by('-video_count_calc')[:5]
            )
            top_users = [
                {'id': str(it['id']), 'username': it['username'], 'video_count': int(it['video_count_calc'] or 0)}
                for it in top_users_qs
            ]
        except Exception:
            top_users = []

        data = {
            'range': f'{days}d',
            'totals': {
                'users': int(users_total),
                'videos': int(videos_total),
                'comments': int(comments_total),
                'views': int(views_total),
            },
            'deltas': {
                'users': int(users_delta),
                'videos': int(videos_delta),
                'comments': int(comments_delta),
            },
            'visibility': visibility,
            'top_categories': top_categories,
            'trend': trend,
            'top_videos': top_videos,
            'top_users': top_users,
        }
        return Response(data)


class AdminVideosBulkUpdateView(APIView):
    permission_classes = [IsReviewer]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 500:
            raise ValidationError({'video_ids': '一次最多处理 500 个'})
        updates = {}
        # boolean toggles
        if 'allow_comments' in request.data:
            v = request.data.get('allow_comments')
            bv = v if isinstance(v, bool) else _parse_bool(v)
            if bv is None:
                raise ValidationError({'allow_comments': '必须为布尔值'})
            updates['allow_comments'] = bool(bv)
        if 'allow_download' in request.data:
            v = request.data.get('allow_download')
            bv = v if isinstance(v, bool) else _parse_bool(v)
            if bv is None:
                raise ValidationError({'allow_download': '必须为布尔值'})
            updates['allow_download'] = bool(bv)
        # category
        if 'category_id' in request.data:
            raw = request.data.get('category_id')
            cid = (str(raw).strip() if raw is not None else '')
            if cid in ('', 'null'):
                updates['category_id'] = None
            else:
                try:
                    c = Category.objects.only('id').get(pk=cid)
                except Category.DoesNotExist:
                    raise ValidationError({'category_id': '分类不存在'})
                updates['category_id'] = c.id
        # visibility
        pub_fields = {}
        if 'visibility' in request.data:
            vis = str(request.data.get('visibility') or '').strip()
            if vis not in {'', 'public', 'unlisted', 'private'}:
                raise ValidationError({'visibility': '取值无效'})
            if vis:
                updates['visibility'] = vis
        # status & published_at handling
        set_status = None
        if 'status' in request.data:
            st = str(request.data.get('status') or '').strip()
            if st not in {'', 'draft', 'processing', 'published', 'banned'}:
                raise ValidationError({'status': '取值无效'})
            if st:
                set_status = st
        if updates and not IsAdmin().has_permission(request, self):
            raise PermissionDenied('仅管理员可批量修改视频运营信息')
        qs = Video.objects.filter(id__in=ids)
        affected = qs.count()
        # 若批量设置为发布，做作者邮箱验证校验
        if set_status == 'published':
            try:
                bad = qs.filter(user__is_verified=False).values_list('id', flat=True)
                bad_list = list(str(x) for x in bad)
                if bad_list:
                    raise ValidationError({'status': f'存在作者邮箱未验证的视频，禁止发布', 'video_ids': bad_list[:10]})
            except ValidationError:
                raise
            except Exception:
                raise ValidationError({'status': '发布校验失败'})
        if updates:
            qs.update(**updates)
        if set_status:
            if set_status == 'published':
                now = timezone.now()
                qs.filter(status='published').update(status='published')
                qs.exclude(status='published').update(status='published', published_at=now)
            else:
                qs.update(status=set_status, published_at=None)
        try:
            _audit(request, 'video.bulk_update', 'video', None, {'count': len(ids), 'affected': affected, 'fields': list(updates.keys()) + ((['status'] if set_status else []))})
        except Exception:
            pass
        return Response({'updated': int(affected)})


class AdminVideosBulkDeleteView(APIView):
    permission_classes = [IsModerator]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 500:
            raise ValidationError({'video_ids': '一次最多处理 500 个'})
        qs = Video.objects.filter(id__in=ids)
        
        # 统计各作者被删除的视频数，用于更新 user.video_count
        user_counts = qs.values('user_id').annotate(count=Count('id'))
        affected = qs.count()
        
        with transaction.atomic():
            # 批量删除
            qs.delete()
            # 补偿更新用户的视频计数
            for item in user_counts:
                User.objects.filter(id=item['user_id']).update(
                    video_count=Case(
                        When(video_count__gte=item['count'], then=F('video_count') - item['count']),
                        default=0,
                        output_field=IntegerField()
                    )
                )
        
        try:
            _audit(request, 'video.bulk_delete', 'video', None, {'count': len(ids), 'affected': affected})
        except Exception:
            pass
        return Response({'removed': int(affected)})


class AdminVideosBatchApproveView(APIView):
    permission_classes = [IsReviewer]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        action = str(request.data.get('action') or '').strip().lower()
        reason = (request.data.get('reason') or '').strip()
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        if action not in {'approve', 'reject'}:
            raise ValidationError({'action': '必须为 approve 或 reject'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 500:
            raise ValidationError({'video_ids': '一次最多处理 500 个'})
        qs = Video.objects.filter(id__in=ids)
        affected = qs.count()
        if action == 'approve':
            try:
                bad = qs.filter(user__is_verified=False).values_list('id', flat=True)
                bad_list = [str(x) for x in bad]
                if bad_list:
                    raise ValidationError({'action': '存在作者邮箱未验证的视频，禁止通过', 'video_ids': bad_list[:10]})
            except ValidationError:
                raise
            except Exception:
                raise ValidationError({'action': '审核通过校验失败'})
            now = timezone.now()
            qs.filter(status='published').update(status='published', transcode_error=None)
            qs.exclude(status='published').update(status='published', published_at=now, transcode_error=None)
            try:
                _audit(request, 'video.batch_approve', 'video', None, {'count': len(ids), 'affected': affected})
            except Exception:
                pass
            return Response({'approved': int(affected)})
        else:
            qs.update(status='banned', transcode_error=reason or 'rejected_by_admin')
            try:
                _audit(request, 'video.batch_reject', 'video', None, {'count': len(ids), 'affected': affected, 'reason': reason})
            except Exception:
                pass
            return Response({'rejected': int(affected), 'reason': reason})


class AdminVideosTranscodeFailuresView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = Video.objects.filter(transcode_error__isnull=False).order_by('-updated_at')
        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = []
        for v in rows:
            data.append({
                'id': str(v.id),
                'title': v.title,
                'status': v.status,
                'transcode_error': v.transcode_error,
                'updated_at': v.updated_at,
                'owner_id': str(v.user_id),
            })
        return Response(p.format(data, total))


class AdminVideosMetricsTrendView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        metric = (request.query_params.get('metric') or 'upload').strip().lower()
        rng = (request.query_params.get('range') or '7d').lower().strip()
        days_map = {'7d': 7, '30d': 30}
        days = days_map.get(rng, 7)
        today = timezone.localdate()
        start_date = today - timezone.timedelta(days=days - 1)
        def date_range_list():
            return [start_date + timezone.timedelta(days=i) for i in range(days)]
        result = []
        if metric == 'view':
            qs = History.objects.filter(created_at__date__gte=start_date, created_at__date__lte=today)
            rows = qs.annotate(d=TruncDate('created_at')).values('d').annotate(c=Count('id'))
        elif metric == 'like':
            qs = Like.objects.filter(created_at__date__gte=start_date, created_at__date__lte=today)
            rows = qs.annotate(d=TruncDate('created_at')).values('d').annotate(c=Count('id'))
        elif metric == 'transcode_fail':
            qs = Video.objects.filter(updated_at__date__gte=start_date, updated_at__date__lte=today, transcode_error__isnull=False)
            rows = qs.annotate(d=TruncDate('updated_at')).values('d').annotate(c=Count('id'))
        else:  # upload
            metric = 'upload'
            qs = Video.objects.filter(created_at__date__gte=start_date, created_at__date__lte=today)
            rows = qs.annotate(d=TruncDate('created_at')).values('d').annotate(c=Count('id'))
        m = {str(it['d']): int(it['c'] or 0) for it in rows}
        for d in date_range_list():
            ds = d.isoformat()
            result.append({'date': ds, 'value': int(m.get(ds, 0))})
        return Response({'metric': metric, 'range': f'{days}d', 'trend': result})


class AdminCommentsListView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = Comment.objects.select_related('user', 'video').all()
        base = (get_system_setting('SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
        media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
        def url_of(rel: str) -> str:
            if media.startswith('http://') or media.startswith('https://'):
                return f"{media}/{rel}"
            return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"
        def to_url(rel: str):
            if not rel:
                return None
            try:
                u = default_storage.url(rel)
            except Exception:
                u = None
            if u and (u.startswith('http://') or u.startswith('https://')):
                return u
            return url_of(rel)
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(content__icontains=q)
        vid = request.query_params.get('video_id')
        if vid:
            try:
                _ = uuid.UUID(str(vid))
                qs = qs.filter(video_id=vid)
            except Exception:
                pass
        uid = request.query_params.get('user_id')
        if uid:
            try:
                _ = uuid.UUID(str(uid))
                qs = qs.filter(user_id=uid)
            except Exception:
                pass
        # 时间范围过滤（ISO 字符串或日期）
        dr_from = request.query_params.get('from')
        dr_to = request.query_params.get('to')
        # 宽松解析：让数据库去解析字符串为时间
        if dr_from:
            try:
                qs = qs.filter(created_at__gte=dr_from)
            except Exception:
                pass
        if dr_to:
            try:
                qs = qs.filter(created_at__lte=dr_to)
            except Exception:
                pass
        order = (request.query_params.get('order') or '').strip().lower()
        if order == 'earliest':
            qs = qs.order_by('created_at')
        else:
            qs = qs.order_by('-created_at')

        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = []
        for c in rows:
            u = getattr(c, 'user', None)
            v = getattr(c, 'video', None)
            # user avatar
            avatar_url = None
            if u is not None:
                pp_name = getattr(getattr(u, 'profile_picture_f', None), 'name', None)
                pp = pp_name or getattr(u, 'profile_picture', None)
                avatar_url = to_url(pp) if pp else None
            data.append({
                'id': str(c.id),
                'content': c.content,
                'user': ({'id': str(u.id), 'username': getattr(u, 'username', ''), 'avatar_url': avatar_url} if u else None),
                'video': ({'id': str(v.id), 'title': getattr(v, 'title', '')} if v else None),
                'parent_id': str(getattr(c.parent, 'id', '') or '') if getattr(c, 'parent', None) else None,
                'created_at': c.created_at,
                'updated_at': c.updated_at,
            })
        return Response(p.format(data, total))


class AdminCommentDetailView(APIView):
    permission_classes = [IsModerator]

    def delete(self, request, pk):
        c = get_object_or_404(Comment, pk=pk)
        cid = str(c.id)
        c.delete()
        try:
            _audit(request, 'comment.delete', 'comment', cid, None)
        except Exception:
            pass
        return Response({'removed': 1})


class AdminMeView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        u = request.user
        return Response({
            'id': str(getattr(u, 'id', '')),
            'username': getattr(u, 'username', ''),
            'is_staff': bool(getattr(u, 'is_staff', False)),
            'is_superuser': bool(getattr(u, 'is_superuser', False)),
            'admin_role': getattr(u, 'admin_role', 'none'),
        })


class AdminModerationHealthCheckView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        allowed_keys = {
            'ZHIPU_API_KEY',
            'ZHIPU_BASE_URL',
            'ZHIPU_MODERATION_MODEL',
            'ZHIPU_MODERATION_TIMEOUT_SECONDS',
            'ZHIPU_MODERATION_FAIL_CLOSED',
            'ZHIPU_MODERATION_BLOCKED_CATEGORIES',
            'MODERATION_MEDIA_PUBLIC_BASE_URL',
            'SITE_URL',
        }
        overrides = {k: body.get(k) for k in allowed_keys if k in body}
        result = check_zhipu_moderation(overrides=overrides)
        try:
            _audit(request, 'moderation.check', 'system', None, {
                'ok': bool(result.get('ok')),
                'model': result.get('model'),
                'base_url': result.get('base_url'),
            })
        except Exception:
            pass
        return Response(result, status=200 if result.get('ok') else 400)


class AdminUserForceLogoutView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, pk):
        cutoff = int(time.time())
        key = f"logout_after:{pk}"
        ttl = _refresh_lifetime_seconds()
        cache.set(key, cutoff, timeout=ttl)
        try:
            _audit(request, 'user.force_logout', 'user', str(pk), {'cutoff': cutoff})
        except Exception:
            pass
        return Response({'success': True, 'cutoff': cutoff})


class AdminAuditLogsListView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = AuditLog.objects.select_related('actor').all()
        verb = (request.query_params.get('verb') or '').strip()
        if verb:
            qs = qs.filter(verb__iexact=verb)
        actor_id = request.query_params.get('actor_id')
        if actor_id:
            try:
                _ = uuid.UUID(str(actor_id))
                qs = qs.filter(actor_id=actor_id)
            except Exception:
                pass
        tt = (request.query_params.get('target_type') or '').strip()
        if tt:
            qs = qs.filter(target_type__iexact=tt)
        meta_source = (request.query_params.get('source') or '').strip()
        if meta_source:
            qs = qs.filter(meta__source=meta_source)
        meta_scenario = (request.query_params.get('scenario') or '').strip()
        if meta_scenario:
            qs = qs.filter(meta__scenario=meta_scenario)
        tid = request.query_params.get('target_id')
        if tid:
            try:
                _ = uuid.UUID(str(tid))
                qs = qs.filter(target_id=tid)
            except Exception:
                pass
        qs = qs.order_by('-created_at')
        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = []
        for a in rows:
            data.append({
                'id': str(a.id),
                'verb': a.verb,
                'target_type': a.target_type,
                'target_id': str(a.target_id) if a.target_id else None,
                'actor': ({'id': str(a.actor.id), 'username': getattr(a.actor, 'username', '')} if a.actor else None),
                'meta': a.meta,
                'created_at': a.created_at,
            })
        return Response(p.format(data, total))


class AdminAuditLogsAutomodSummaryView(APIView):
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = AuditLog.objects.filter(verb='content.automod.blocked').order_by('-created_at')
        meta_source = (request.query_params.get('source') or '').strip()
        if meta_source:
            qs = qs.filter(meta__source=meta_source)
        meta_scenario = (request.query_params.get('scenario') or '').strip()
        if meta_scenario:
            qs = qs.filter(meta__scenario=meta_scenario)
        days = 7
        try:
            days = max(1, min(90, int(request.query_params.get('days') or 7)))
        except Exception:
            days = 7
        since = timezone.now() - timezone.timedelta(days=days)
        qs = qs.filter(created_at__gte=since)

        summary: dict[str, dict] = {}
        total_hits = 0
        for row in qs.only('meta', 'created_at'):
            meta = row.meta or {}
            details = meta.get('matched_details') or []
            if not isinstance(details, list):
                details = []
            if details:
                for detail in details:
                    if not isinstance(detail, dict):
                        continue
                    label = str(detail.get('label') or '').strip()
                    if not label:
                        continue
                    detail_type = str(detail.get('type') or '').strip() or 'rule'
                    matched_text = str(detail.get('matched_text') or '').strip()
                    key = f'{detail_type}::{label}::{matched_text}'
                    item = summary.setdefault(key, {
                        'type': detail_type,
                        'label': label,
                        'matched_text': matched_text,
                        'count': 0,
                        'latest_at': None,
                        'scenarios': {},
                        'sources': {},
                    })
                    item['count'] += 1
                    total_hits += 1
                    created_at = row.created_at
                    if item['latest_at'] is None or created_at > item['latest_at']:
                        item['latest_at'] = created_at
                    scenario = str(meta.get('scenario') or '').strip() or 'unknown'
                    source = str(meta.get('source') or '').strip() or 'unknown'
                    item['scenarios'][scenario] = int(item['scenarios'].get(scenario) or 0) + 1
                    item['sources'][source] = int(item['sources'].get(source) or 0) + 1
            else:
                for keyword in meta.get('matched_keywords') or []:
                    label = str(keyword or '').strip()
                    if not label:
                        continue
                    key = f'keyword::{label}::{label}'
                    item = summary.setdefault(key, {
                        'type': 'keyword',
                        'label': label,
                        'matched_text': label,
                        'count': 0,
                        'latest_at': None,
                        'scenarios': {},
                        'sources': {},
                    })
                    item['count'] += 1
                    total_hits += 1
                    created_at = row.created_at
                    if item['latest_at'] is None or created_at > item['latest_at']:
                        item['latest_at'] = created_at
                    scenario = str(meta.get('scenario') or '').strip() or 'unknown'
                    source = str(meta.get('source') or '').strip() or 'unknown'
                    item['scenarios'][scenario] = int(item['scenarios'].get(scenario) or 0) + 1
                    item['sources'][source] = int(item['sources'].get(source) or 0) + 1

        top_n = 20
        try:
            top_n = max(1, min(100, int(request.query_params.get('limit') or 20)))
        except Exception:
            top_n = 20
        rows = sorted(summary.values(), key=lambda item: (-item['count'], -(item['latest_at'].timestamp() if item['latest_at'] else 0)))[:top_n]
        data = [{
            'type': item['type'],
            'label': item['label'],
            'matched_text': item['matched_text'],
            'count': item['count'],
            'latest_at': item['latest_at'],
            'scenarios': item['scenarios'],
            'sources': item['sources'],
        } for item in rows]
        return Response({
            'days': days,
            'total_rules': len(summary),
            'total_hits': total_hits,
            'results': data,
        })


class AdminAutomodRuleApplyView(APIView):
    permission_classes = [IsAdmin]

    @staticmethod
    def _append_line(raw_value: str, line: str) -> tuple[str, bool]:
        lines = [str(item or '').strip() for item in str(raw_value or '').splitlines() if str(item or '').strip()]
        if line in lines:
            return '\n'.join(lines), False
        lines.append(line)
        return '\n'.join(lines), True

    @staticmethod
    def _append_keyword(raw_value: str, keyword: str) -> tuple[str, bool]:
        items: list[str] = []
        seen: set[str] = set()
        for chunk in str(raw_value or '').replace(',', '\n').splitlines():
            item = str(chunk or '').strip()
            if not item:
                continue
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            items.append(item)
        lowered_keyword = keyword.lower()
        if lowered_keyword in seen:
            return '\n'.join(items), False
        items.append(keyword)
        return '\n'.join(items), True

    @staticmethod
    def _remove_line(raw_value: str, line: str) -> tuple[str, bool]:
        lines = [str(item or '').strip() for item in str(raw_value or '').splitlines() if str(item or '').strip()]
        kept = [item for item in lines if item != line]
        return '\n'.join(kept), len(kept) != len(lines)

    @staticmethod
    def _remove_keyword(raw_value: str, keyword: str) -> tuple[str, bool]:
        items = [str(item or '').strip() for item in str(raw_value or '').replace(',', '\n').splitlines() if str(item or '').strip()]
        lowered_keyword = keyword.lower()
        kept = [item for item in items if item.lower() != lowered_keyword]
        return '\n'.join(kept), len(kept) != len(items)

    @staticmethod
    def _resolve_rule_target(rule_kind: str, content_type: str) -> tuple[str, str]:
        mapping = {
            ('keyword', 'comment'): ('COMMENT_BLOCKED_KEYWORDS', 'keyword'),
            ('canonical', 'comment'): ('COMMENT_CANONICAL_RULES', 'canonical'),
            ('pattern', 'comment'): ('COMMENT_PATTERN_RULES', 'pattern'),
            ('keyword', 'video'): ('VIDEO_BLOCKED_KEYWORDS', 'keyword'),
        }
        result = mapping.get((rule_kind, content_type))
        if not result:
            raise ValidationError({'rule_type': '当前仅支持评论关键词/归一化/正则，以及视频关键词'})
        return result

    @staticmethod
    def _builtin_rule_lines(rule_kind: str, content_type: str) -> set[str]:
        if content_type != 'comment':
            return set()
        if rule_kind == 'keyword':
            return {str(item).strip() for item in DEFAULT_COMMENT_BLOCKED_KEYWORDS if str(item).strip()}
        if rule_kind == 'canonical':
            return {f'{src}={dst}' for src, dst in COMMENT_TEXT_CANONICAL_RULES if str(src).strip() and str(dst).strip()}
        if rule_kind == 'pattern':
            return {f'{pattern.pattern}={label}' for pattern, label in COMMENT_PATTERN_RULES if str(pattern.pattern).strip() and str(label).strip()}
        return set()

    def post(self, request):
        rule_type = str(request.data.get('rule_type') or '').strip().lower()
        content_type = str(request.data.get('content_type') or 'comment').strip().lower()
        label = str(request.data.get('label') or '').strip()
        matched_text = str(request.data.get('matched_text') or '').strip()
        if rule_type not in {'keyword', 'canonical', 'pattern'}:
            raise ValidationError({'rule_type': '仅支持 keyword、canonical 或 pattern'})
        if content_type not in {'comment', 'video'}:
            raise ValidationError({'content_type': '仅支持 comment 或 video'})
        setting_key, normalized_rule_type = self._resolve_rule_target(rule_type, content_type)
        if rule_type == 'keyword':
            if not label:
                raise ValidationError({'label': '缺少关键词'})
            rule_line = label
            builtin_lines = self._builtin_rule_lines(rule_type, content_type)
            if rule_line in builtin_lines:
                return Response({
                    'status': 'ok',
                    'added': False,
                    'content_type': content_type,
                    'rule_type': normalized_rule_type,
                    'setting_key': setting_key,
                    'rule_line': rule_line,
                    'version': int(time.time()),
                    'source': 'default',
                })
            current_value = str(get_system_setting(setting_key, '') or '')
            updated_value, added = self._append_keyword(current_value, rule_line)
        else:
            if not label or not matched_text:
                raise ValidationError({'detail': '缺少规则内容'})
            if rule_type == 'canonical' and label == matched_text:
                raise ValidationError({'matched_text': '归一化规则需要变体和值不同'})
            rule_line = f'{matched_text}={label}'
            builtin_lines = self._builtin_rule_lines(rule_type, content_type)
            if rule_line in builtin_lines:
                return Response({
                    'status': 'ok',
                    'added': False,
                    'content_type': content_type,
                    'rule_type': normalized_rule_type,
                    'setting_key': setting_key,
                    'rule_line': rule_line,
                    'version': int(time.time()),
                    'source': 'default',
                })
            current_value = str(get_system_setting(setting_key, '') or '')
            updated_value, added = self._append_line(current_value, rule_line)

        if not setting_key:
            raise ValidationError({'detail': '缺少规则内容'})
        set_config('system', setting_key, updated_value, value_type='string')

        version = int(time.time())
        set_config('system', 'config_version', version, value_type='int')
        publish_config_updated(
            version=version,
            changed_keys=[setting_key],
            reload_required=False,
        )
        try:
            _audit(
                request,
                'content.automod.rule_apply',
                'config',
                None,
                {
                    'rule_type': normalized_rule_type,
                    'content_type': content_type,
                    'setting_key': setting_key,
                    'rule_line': rule_line,
                    'added': added,
                },
            )
        except Exception:
            pass
        return Response({
            'status': 'ok',
            'added': added,
            'content_type': content_type,
            'rule_type': normalized_rule_type,
            'setting_key': setting_key,
            'rule_line': rule_line,
            'version': version,
        })

    def delete(self, request):
        rule_type = str(request.data.get('rule_type') or '').strip().lower()
        content_type = str(request.data.get('content_type') or 'comment').strip().lower()
        label = str(request.data.get('label') or '').strip()
        matched_text = str(request.data.get('matched_text') or '').strip()
        if rule_type not in {'keyword', 'canonical', 'pattern'}:
            raise ValidationError({'rule_type': '仅支持 keyword、canonical 或 pattern'})
        if content_type not in {'comment', 'video'}:
            raise ValidationError({'content_type': '仅支持 comment 或 video'})
        setting_key, normalized_rule_type = self._resolve_rule_target(rule_type, content_type)
        if rule_type == 'keyword':
            if not label:
                raise ValidationError({'label': '缺少关键词'})
            rule_line = label
            if rule_line in self._builtin_rule_lines(rule_type, content_type):
                raise ValidationError({'detail': '内置规则不能删除'})
            current_value = str(get_system_setting(setting_key, '') or '')
            updated_value, removed = self._remove_keyword(current_value, rule_line)
        else:
            if not label or not matched_text:
                raise ValidationError({'detail': '缺少规则内容'})
            rule_line = f'{matched_text}={label}'
            if rule_line in self._builtin_rule_lines(rule_type, content_type):
                raise ValidationError({'detail': '内置规则不能删除'})
            current_value = str(get_system_setting(setting_key, '') or '')
            updated_value, removed = self._remove_line(current_value, rule_line)

        if removed:
            set_config('system', setting_key, updated_value, value_type='string')

        version = int(time.time())
        set_config('system', 'config_version', version, value_type='int')
        publish_config_updated(
            version=version,
            changed_keys=[setting_key],
            reload_required=False,
        )
        try:
            _audit(
                request,
                'content.automod.rule_remove',
                'config',
                None,
                {
                    'rule_type': normalized_rule_type,
                    'content_type': content_type,
                    'setting_key': setting_key,
                    'rule_line': rule_line,
                    'removed': removed,
                },
            )
        except Exception:
            pass
        return Response({
            'status': 'ok',
            'removed': removed,
            'content_type': content_type,
            'rule_type': normalized_rule_type,
            'setting_key': setting_key,
            'rule_line': rule_line,
            'version': version,
        })


class AdminCategoriesListView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAdmin()]
        return [IsReviewer()]

    def get(self, request):
        qs = Category.objects.all()
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.order_by('name')
        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = [{'id': str(c.id), 'name': c.name, 'description': c.description, 'created_at': c.created_at} for c in rows]
        return Response(p.format(data, total))

    def post(self, request):
        name = str(request.data.get('name') or '').strip()
        desc = request.data.get('description')
        if not name:
            raise ValidationError({'name': '必填'})
        if len(name) > 100:
            raise ValidationError({'name': '长度不能超过100'})
        if Category.objects.filter(name__iexact=name).exists():
            raise ValidationError({'name': '已存在同名分类'})
        c = Category.objects.create(name=name, description=desc)
        try:
            _audit(request, 'category.create', 'category', str(c.id), {'name': name})
        except Exception:
            pass
        return Response({'id': str(c.id), 'name': c.name, 'description': c.description}, status=status.HTTP_201_CREATED)


class AdminCategoryDetailView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method in {'PATCH', 'DELETE'}:
            return [IsAdmin()]
        return [IsReviewer()]

    def patch(self, request, pk):
        c = get_object_or_404(Category, pk=pk)
        updates = {}
        if 'name' in request.data:
            n = str(request.data.get('name') or '').strip()
            if not n:
                raise ValidationError({'name': '必填'})
            if len(n) > 100:
                raise ValidationError({'name': '长度不能超过100'})
            if Category.objects.filter(name__iexact=n).exclude(pk=c.pk).exists():
                raise ValidationError({'name': '已存在同名分类'})
            c.name = n
            updates['name'] = True
        if 'description' in request.data:
            c.description = request.data.get('description')
            updates['description'] = True
        if updates:
            fields = []
            if updates.get('name'): fields.append('name')
            if updates.get('description'): fields.append('description')
            c.save(update_fields=fields)
            try:
                _audit(request, 'category.update', 'category', str(c.id), {'fields': list(updates.keys())})
            except Exception:
                pass
        return Response({'updated': 1})

    def delete(self, request, pk):
        c = get_object_or_404(Category, pk=pk)
        cid = str(c.id)
        c.delete()
        try:
            _audit(request, 'category.delete', 'category', cid, None)
        except Exception:
            pass
        return Response({'removed': 1})


class AdminTagsListView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAdmin()]
        return [IsReviewer()]

    def get(self, request):
        qs = Tag.objects.all().annotate(usage_count=Count('tag_videos__id', distinct=True))
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.order_by('name')
        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count
        data = [{'id': str(t.id), 'name': t.name, 'created_at': t.created_at, 'usage_count': getattr(t, 'usage_count', 0)} for t in rows]
        return Response(p.format(data, total))

    def post(self, request):
        name = str(request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': '必填'})
        if len(name) > 50:
            raise ValidationError({'name': '长度不能超过50'})
        if Tag.objects.filter(name__iexact=name).exists():
            raise ValidationError({'name': '已存在同名标签'})
        t = Tag.objects.create(name=name)
        try:
            _audit(request, 'tag.create', 'tag', str(t.id), {'name': name})
        except Exception:
            pass
        return Response({'id': str(t.id), 'name': t.name}, status=status.HTTP_201_CREATED)


class AdminTagDetailView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method in {'PATCH', 'DELETE'}:
            return [IsAdmin()]
        return [IsReviewer()]

    def patch(self, request, pk):
        t = get_object_or_404(Tag, pk=pk)
        if 'name' in request.data:
            n = str(request.data.get('name') or '').strip()
            if not n:
                raise ValidationError({'name': '必填'})
            if len(n) > 50:
                raise ValidationError({'name': '长度不能超过50'})
            if Tag.objects.filter(name__iexact=n).exclude(pk=t.pk).exists():
                raise ValidationError({'name': '已存在同名标签'})
            t.name = n
            t.save(update_fields=['name'])
            try:
                _audit(request, 'tag.update', 'tag', str(t.id), {'fields': ['name']})
            except Exception:
                pass
        return Response({'updated': 1})

    def delete(self, request, pk):
        t = get_object_or_404(Tag, pk=pk)
        tid = str(t.id)
        used = VideoTag.objects.filter(tag=t).exists()
        if used:
            raise ValidationError({'detail': '该标签已被视频使用，无法删除'})
        t.delete()
        try:
            _audit(request, 'tag.delete', 'tag', tid, None)
        except Exception:
            pass
        return Response({'removed': 1})


class AdminTagsBulkDeleteView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request):
        ids = request.data.get('ids') or []
        if not isinstance(ids, (list, tuple)) or not ids:
            raise ValidationError({'ids': '必填，需为列表'})
        removed = 0
        blocked = []
        for tid in ids:
            try:
                t = Tag.objects.get(pk=tid)
            except Tag.DoesNotExist:
                continue
            if VideoTag.objects.filter(tag=t).exists():
                blocked.append(str(t.id))
                continue
            t.delete()
            removed += 1
            try:
                _audit(request, 'tag.delete', 'tag', str(t.id), None)
            except Exception:
                pass
        if blocked:
            return Response({'removed': removed, 'blocked': blocked, 'detail': '部分标签已被使用，未删除'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'removed': removed})


class AdminTagsMergeView(APIView):
    permission_classes = [IsAdmin]

    @transaction.atomic
    def post(self, request):
        source_id = request.data.get('source')
        target_id = request.data.get('target')
        if not source_id or not target_id:
            raise ValidationError({'detail': 'source 与 target 均为必填'})
        if str(source_id) == str(target_id):
            raise ValidationError({'detail': 'source 与 target 不能相同'})
        source = get_object_or_404(Tag, pk=source_id)
        target = get_object_or_404(Tag, pk=target_id)

        # Move usages
        existing_pairs = set(VideoTag.objects.filter(tag=target).values_list('video_id', flat=True))
        move_qs = VideoTag.objects.filter(tag=source)
        to_create = []
        for vt in move_qs:
            if vt.video_id in existing_pairs:
                continue
            to_create.append(VideoTag(video_id=vt.video_id, tag=target))
        if to_create:
            VideoTag.objects.bulk_create(to_create, ignore_conflicts=True)
        moved = move_qs.count()
        move_qs.delete()

        sid = str(source.id)
        source.delete()
        try:
            _audit(request, 'tag.merge', 'tag', sid, {'source': sid, 'target': str(target.id), 'moved': moved})
        except Exception:
            pass
        return Response({'merged': 1, 'moved': moved, 'target': str(target.id)})


class AdminAnnouncementsListCreateView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAdmin()]
        return [IsReviewer()]

    def get(self, request):
        p = StandardResultsSetPagination()
        qs = SystemAnnouncement.objects.all()

        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))

        v_active = _parse_bool(request.query_params.get('is_active'))
        if v_active is not None:
            qs = qs.filter(is_active=v_active)

        order = (request.query_params.get('order') or '').strip().lower()
        if order == 'oldest':
            qs = qs.order_by('created_at')
        else:
            qs = qs.order_by('-pinned', '-published_at', '-created_at')

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
            })
        total = getattr(p.page.paginator, 'count', None)
        return Response(p.format(out, total))

    def post(self, request):
        title = (request.data.get('title') or '').strip()
        content = (request.data.get('content') or '').strip()
        if not title:
            raise ValidationError({'title': '标题不能为空'})
        is_active = bool(_parse_bool(request.data.get('is_active')) if request.data.get('is_active') is not None else True)
        pinned = bool(_parse_bool(request.data.get('pinned')) if request.data.get('pinned') is not None else False)

        published_at = timezone.now() if is_active else None
        a = SystemAnnouncement.objects.create(
            title=title,
            content=content,
            is_active=is_active,
            pinned=pinned,
            published_at=published_at,
        )
        try:
            _audit(request, 'announcement.create', 'system_announcement', str(a.id), {'is_active': is_active, 'pinned': pinned})
        except Exception:
            pass
        return Response({'id': str(a.id)})


class AdminAnnouncementDetailView(APIView):
    permission_classes = [IsReviewer]

    def get_permissions(self):
        if self.request.method in {'PATCH', 'DELETE'}:
            return [IsAdmin()]
        return [IsReviewer()]

    def get(self, request, pk):
        a = get_object_or_404(SystemAnnouncement, pk=pk)
        return Response({
            'id': str(a.id),
            'title': a.title,
            'content': a.content,
            'is_active': bool(a.is_active),
            'pinned': bool(a.pinned),
            'published_at': a.published_at,
            'created_at': a.created_at,
            'updated_at': a.updated_at,
        })

    def patch(self, request, pk):
        a = get_object_or_404(SystemAnnouncement, pk=pk)
        changed = {}

        if 'title' in request.data:
            title = (request.data.get('title') or '').strip()
            if not title:
                raise ValidationError({'title': '标题不能为空'})
            a.title = title
            changed['title'] = True

        if 'content' in request.data:
            a.content = (request.data.get('content') or '')
            changed['content'] = True

        if 'pinned' in request.data:
            pinned = _parse_bool(request.data.get('pinned'))
            if pinned is None:
                raise ValidationError({'pinned': 'pinned 必须为布尔值'})
            a.pinned = bool(pinned)
            changed['pinned'] = bool(pinned)

        if 'is_active' in request.data:
            is_active = _parse_bool(request.data.get('is_active'))
            if is_active is None:
                raise ValidationError({'is_active': 'is_active 必须为布尔值'})
            is_active = bool(is_active)
            if is_active and not a.is_active:
                a.published_at = timezone.now()
            if not is_active:
                a.published_at = None
            a.is_active = is_active
            changed['is_active'] = is_active

        a.save(update_fields=None)
        try:
            _audit(request, 'announcement.update', 'system_announcement', str(a.id), changed)
        except Exception:
            pass
        return Response({'ok': True})

    def delete(self, request, pk):
        a = get_object_or_404(SystemAnnouncement, pk=pk)
        aid = str(a.id)
        a.delete()
        try:
            _audit(request, 'announcement.delete', 'system_announcement', aid, None)
        except Exception:
            pass
        return Response({'deleted': 1})


from apps.content.models import Report, ModerationAction


class AdminReportsListView(APIView):
    """举报列表接口（管理端）

    - 方法：GET /api/admin/reports/
    - 权限：审核员及以上
    - 支持过滤：status、target_type、reporter_id
    - 支持排序：created_at（默认倒序）
    """
    permission_classes = [IsReviewer]

    def get(self, request):
        qs = Report.objects.all().select_related('reporter', 'handled_by')

        # 过滤条件
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        target_type = request.query_params.get('target_type')
        if target_type:
            qs = qs.filter(target_type=target_type)

        reporter_id = request.query_params.get('reporter_id')
        if reporter_id:
            qs = qs.filter(reporter_id=reporter_id)

        target_id = request.query_params.get('target_id')
        if target_id:
            qs = qs.filter(target_id=target_id)

        # 排序
        order = (request.query_params.get('order') or '').strip().lower()
        if order == 'oldest':
            qs = qs.order_by('created_at')
        else:
            qs = qs.order_by('-created_at')

        p = StandardResultsSetPagination()
        rows = list(p.paginate_queryset(qs, request, view=self))
        total = p.page.paginator.count

        data = []
        for r in rows:
            reporter = r.reporter
            handled_by = r.handled_by
            latest_action = r.actions.select_related('moderator').order_by('-created_at').first()

            # 获取目标对象信息
            target_info = None
            try:
                if r.target_type == 'video':
                    from apps.videos.models import Video
                    v = Video.objects.filter(id=r.target_id).first()
                    if v:
                        target_info = {
                            'id': str(v.id),
                            'title': v.title,
                            'author': v.user.username if v.user else None,
                        }
                elif r.target_type == 'comment':
                    from apps.interactions.models import Comment
                    c = Comment.objects.filter(id=r.target_id).select_related('user', 'video').first()
                    if c:
                        target_info = {
                            'id': str(c.id),
                            'content': c.content[:100] if c.content else '',
                            'author': c.user.username if c.user else None,
                            'video_title': c.video.title if c.video else None,
                        }
                elif r.target_type == 'user':
                    from apps.users.models import User
                    u = User.objects.filter(id=r.target_id).first()
                    if u:
                        target_info = {
                            'id': str(u.id),
                            'username': u.username,
                            'nickname': u.nickname,
                        }
            except Exception:
                pass

            data.append({
                'id': str(r.id),
                'reporter': {
                    'id': str(reporter.id) if reporter else None,
                    'username': reporter.username if reporter else None,
                    'nickname': reporter.nickname if reporter else None,
                },
                'target_type': r.target_type,
                'target_id': str(r.target_id),
                'target_info': target_info,
                'reason_code': r.reason_code,
                'description': r.description,
                'status': r.status,
                'handled_by': {
                    'id': str(handled_by.id) if handled_by else None,
                    'username': handled_by.username if handled_by else None,
                } if handled_by else None,
                'handled_at': r.handled_at,
                'moderator_notes': r.moderator_notes,
                'created_at': r.created_at,
                'updated_at': r.updated_at,
                'latest_action': {
                    'action': latest_action.action,
                    'reason': latest_action.reason,
                    'created_at': latest_action.created_at,
                    'moderator': {
                        'id': str(latest_action.moderator.id) if latest_action.moderator else None,
                        'username': latest_action.moderator.username if latest_action.moderator else None,
                    } if latest_action.moderator else None,
                } if latest_action else None,
            })

        return Response(p.format(data, total))


class AdminReportDetailView(APIView):
    """举报详情接口（管理端）

    - 方法：GET /api/admin/reports/<pk>/
    - 权限：审核员及以上
    """
    permission_classes = [IsReviewer]

    def get(self, request, pk):
        report = get_object_or_404(Report.objects.select_related('reporter', 'handled_by'), pk=pk)

        # 获取目标对象详情
        target_detail = None
        try:
            if report.target_type == 'video':
                from apps.videos.models import Video
                v = Video.objects.filter(id=report.target_id).select_related('user').first()
                if v:
                    target_detail = {
                        'id': str(v.id),
                        'title': v.title,
                        'description': v.description,
                        'author': {
                            'id': str(v.user.id) if v.user else None,
                            'username': v.user.username if v.user else None,
                            'nickname': v.user.nickname if v.user else None,
                        },
                        'status': v.status,
                        'visibility': v.visibility,
                        'created_at': v.created_at,
                    }
            elif report.target_type == 'comment':
                from apps.interactions.models import Comment
                c = Comment.objects.filter(id=report.target_id).select_related('user', 'video').first()
                if c:
                    target_detail = {
                        'id': str(c.id),
                        'content': c.content,
                        'author': {
                            'id': str(c.user.id) if c.user else None,
                            'username': c.user.username if c.user else None,
                            'nickname': c.user.nickname if c.user else None,
                        },
                        'video': {
                            'id': str(c.video.id) if c.video else None,
                            'title': c.video.title if c.video else None,
                        },
                        'created_at': c.created_at,
                    }
            elif report.target_type == 'user':
                from apps.users.models import User
                u = User.objects.filter(id=report.target_id).first()
                if u:
                    target_detail = {
                        'id': str(u.id),
                        'username': u.username,
                        'nickname': u.nickname,
                        'is_active': u.is_active,
                        'is_verified': u.is_verified,
                        'date_joined': u.date_joined,
                    }
                    role = getattr(request.user, 'admin_role', 'none')
                    if role in ('admin', 'super_admin'):
                        target_detail['email'] = u.email
        except Exception:
            pass

        # 获取处理记录
        actions = []
        for a in report.actions.select_related('moderator').order_by('-created_at'):
            actions.append({
                'id': str(a.id),
                'action': a.action,
                'reason': a.reason,
                'moderator': {
                    'id': str(a.moderator.id) if a.moderator else None,
                    'username': a.moderator.username if a.moderator else None,
                },
                'created_at': a.created_at,
            })

        data = {
            'id': str(report.id),
            'reporter': {
                'id': str(report.reporter.id) if report.reporter else None,
                'username': report.reporter.username if report.reporter else None,
                'nickname': report.reporter.nickname if report.reporter else None,
            },
            'target_type': report.target_type,
            'target_id': str(report.target_id),
            'target_detail': target_detail,
            'reason_code': report.reason_code,
            'description': report.description,
            'status': report.status,
            'handled_by': {
                'id': str(report.handled_by.id) if report.handled_by else None,
                'username': report.handled_by.username if report.handled_by else None,
            } if report.handled_by else None,
            'handled_at': report.handled_at,
            'moderator_notes': report.moderator_notes,
            'created_at': report.created_at,
            'updated_at': report.updated_at,
            'actions': actions,
        }
        return Response(data)


class AdminReportHandleView(APIView):
    """举报处理接口（管理端）

    - 方法：POST /api/admin/reports/<pk>/handle/
    - 权限：根据动作类型动态判断
    - 参数：action（动作类型）、notes（备注）
    """
    permission_classes = [CanHandleReport]

    def post(self, request, pk):
        report = get_object_or_404(Report, pk=pk)
        if report.status not in {'pending', 'escalated'}:
            raise ValidationError({'status': f'该举报当前状态为 {report.status}，不能继续处理'})

        action = (request.data.get('action') or '').strip()
        notes = (request.data.get('notes') or '').strip()

        if not action:
            raise ValidationError({'action': '必填，例如: dismiss, warn, delete_content, ban_user'})

        valid_actions = ['dismiss', 'warn', 'delete_content', 'ban_user', 'escalate']
        if action not in valid_actions:
            raise ValidationError({'action': f'非法值，可选: {", ".join(valid_actions)}'})
        allowed_actions = _report_target_allowed_actions(report.target_type)
        if action not in allowed_actions:
            raise ValidationError({'action': f'{report.target_type} 举报不支持动作 {action}'})
        if action == 'escalate' and not notes:
            raise ValidationError({'notes': '升级处理必须填写交接备注'})
        if action == 'escalate' and not _can_escalate_report(request.user, report):
            raise PermissionDenied('当前角色不需要升级该举报，请直接处理')
        if report.status == 'escalated':
            if action == 'escalate':
                raise ValidationError({'action': '该举报已升级，不能重复升级'})
            if not _can_handle_escalated_report(request.user, report):
                raise PermissionDenied('该升级举报需由更高权限角色继续处理')

        target_user_id = None
        video_id = None
        comment_id = None
        try:
            if report.target_type == 'video':
                v = Video.objects.filter(id=report.target_id).only('id', 'user_id').first()
                if v:
                    target_user_id = v.user_id
                    video_id = v.id
            elif report.target_type == 'comment':
                c = Comment.objects.filter(id=report.target_id).only('id', 'user_id', 'video_id').first()
                if c:
                    target_user_id = c.user_id
                    video_id = c.video_id
                    comment_id = c.id
            elif report.target_type == 'user':
                target_user_id = report.target_id
        except Exception:
            pass

        handled_at = timezone.now()
        sibling_notes = (notes or '').strip() or '同目标举报已随主处理单一并关闭'
        settle_related = action in {'delete_content', 'ban_user'}
        action_result = None

        with transaction.atomic():
            if action == 'delete_content':
                if report.target_type == 'video':
                    from apps.videos.models import Video
                    v = Video.objects.filter(id=report.target_id).first()
                    if not v:
                        raise ValidationError({'target_id': '举报目标视频不存在或已删除，不能执行删除内容'})
                    try:
                        if v.user_id:
                            from apps.interactions.models import Notification
                            Notification.objects.create(
                                user_id=v.user_id,
                                actor_id=request.user.id,
                                verb='content_removed',
                                video_id=None,
                                comment_id=None,
                            )
                    except Exception:
                        pass
                    v.delete()
                    action_result = '视频已删除'
                elif report.target_type == 'comment':
                    from apps.interactions.models import Comment
                    c = Comment.objects.filter(id=report.target_id).first()
                    if not c:
                        raise ValidationError({'target_id': '举报目标评论不存在或已删除，不能执行删除内容'})
                    try:
                        if c.user_id:
                            from apps.interactions.models import Notification
                            Notification.objects.create(
                                user_id=c.user_id,
                                actor_id=request.user.id,
                                verb='content_removed',
                                video_id=None,
                                comment_id=None,
                            )
                    except Exception:
                        pass
                    c.delete()
                    action_result = '评论已删除'
            elif action == 'ban_user':
                if report.target_type != 'user':
                    raise ValidationError({'action': f'{report.target_type} 举报不支持动作 {action}'})
                from apps.users.models import User
                u = User.objects.filter(id=report.target_id).first()
                if not u:
                    raise ValidationError({'target_id': '举报目标用户不存在，不能执行封禁'})
                u.is_active = False
                u.save(update_fields=['is_active'])
                action_result = '用户已封禁'
            elif action == 'warn':
                action_result = '已记录警告'
            elif action == 'dismiss':
                action_result = '举报已驳回'
            elif action == 'escalate':
                action_result = '举报已升级'

            report.status = 'resolved' if action != 'escalate' else 'escalated'
            report.handled_by = request.user
            report.handled_at = handled_at
            report.moderator_notes = notes or report.moderator_notes
            report.save(update_fields=['status', 'handled_by', 'handled_at', 'moderator_notes', 'updated_at'])

            ModerationAction.objects.create(
                report=report,
                moderator=request.user,
                action=action,
                reason=notes,
            )

            if settle_related:
                Report.objects.filter(
                    target_type=report.target_type,
                    target_id=report.target_id,
                    status='pending',
                ).exclude(id=report.id).update(
                    status='resolved',
                    handled_by=request.user,
                    handled_at=handled_at,
                    moderator_notes=sibling_notes,
                )

        _audit(request, 'report.handle', 'report', str(report.id), {
            'action': action,
            'notes': notes,
            'result': action_result,
        })

        # 发送站内通知给被处理用户
        try:
            from apps.interactions.models import Notification
            # 发送通知
            if target_user_id and action not in {'delete_content', 'escalate'}:
                notify_video_id = video_id
                notify_comment_id = comment_id
                verb_map = {
                    'dismiss': 'report_dismissed',
                    'warn': 'report_warned',
                    'delete_content': 'content_removed',
                    'ban_user': 'account_banned',
                }
                verb = verb_map.get(action, 'report_handled')
                Notification.objects.create(
                    user_id=target_user_id,
                    actor_id=request.user.id,
                    verb=verb,
                    video_id=notify_video_id,
                    comment_id=notify_comment_id
                )
        except Exception:
            pass  # 通知失败不影响主流程

        return Response({
            'report_id': str(report.id),
            'status': report.status,
            'action': action,
            'action_result': action_result,
            'handled_at': report.handled_at,
        })


class AdminSwitchUserView(APIView):
    """切换管理员账号（直接登录到另一个管理员账号）

    - 方法：POST /api/admin/switch-user/
    - 权限：当前用户必须为超级管理员，且需验证目标管理员凭据
    - 参数：target_username（目标管理员用户名）, target_password（目标管理员密码）
    - 返回：登录后的 token
    """
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        target_username = (request.data.get('target_username') or '').strip()
        target_password = request.data.get('target_password', '')
        
        if not target_username or not target_password:
            raise ValidationError({'code': 'missing_credentials', 'detail': '请输入目标管理员的用户名和密码'})

        # 验证目标管理员凭据
        from django.contrib.auth import authenticate
        target_user = authenticate(username=target_username, password=target_password)
        
        if not target_user or not getattr(target_user, 'is_staff', False):
            raise PermissionDenied('管理员凭据无效')
        
        # 检查目标用户是否是管理员角色
        target_role = getattr(target_user, 'admin_role', 'none')
        if target_role not in ['reviewer', 'moderator', 'admin', 'super_admin']:
            raise PermissionDenied('目标用户不是管理员')

        # 生成目标用户的 token
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = _apply_refresh_lifetime(RefreshToken.for_user(target_user))
        
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': str(target_user.id),
                'username': target_user.username,
                'nickname': target_user.nickname,
                'is_staff': target_user.is_staff,
                'is_superuser': target_user.is_superuser,
                'admin_role': target_role,
            },
            'switched': True,
        })


class AdminImpersonateExitView(APIView):
    """退出模拟登录，返回原始管理员token

    - 方法：POST /api/admin/impersonate-exit/
    - 权限：需登录
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # 前端保存原始管理员token，退出时恢复
        return Response({'ok': True})


class AdminVideoRetryTranscodeView(APIView):
    """管理员转码重试（使用 IsReviewer 权限）

    - 方法：POST /api/admin/videos/<pk>/retry-transcode/
    - 权限：审核员及以上
    """
    permission_classes = [IsModerator]

    def post(self, request, pk):
        from apps.videos.models import Video
        from apps.tasks.tasks import generate_vtt_and_thumbnail, transcode_video_to_hls

        v = get_object_or_404(Video, pk=pk)

        # 仅允许处理已上传完成的视频
        if not v.video_file:
            raise ValidationError({'detail': '视频文件缺失，无法重试'})

        # 清理状态与错误信息
        v.status = 'processing'
        v.transcode_error = None
        try:
            v.save(update_fields=['status', 'transcode_error', 'updated_at'])
        except Exception:
            v.save()

        # 触发转码与缩略图任务
        t1 = generate_vtt_and_thumbnail.delay(str(v.id))
        t2 = transcode_video_to_hls.delay(str(v.id))

        # 记录审计日志
        try:
            _audit(request, 'video.retry_transcode', 'video', str(v.id), None)
        except Exception:
            pass

        return Response({'status': 'processing', 'id': str(v.id), 'task_ids': [t1.id, t2.id]})

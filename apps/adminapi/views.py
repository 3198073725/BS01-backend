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

from drf_spectacular.utils import extend_schema, extend_schema_view
from apps.users.serializers import UserMeSerializer, UserFollowListSerializer

@extend_schema_view(
    get=extend_schema(responses={200: UserFollowListSerializer(many=True)}),
)
class AdminUsersListView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = User.objects.all()
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(nickname__icontains=q) | Q(email__icontains=q))
        for field in ('is_active', 'is_verified', 'is_creator'):
            v = _parse_bool(request.query_params.get(field))
            if v is not None:
                qs = qs.filter(**{field: v})
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
    permission_classes = [permissions.IsAdminUser]

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
            if not getattr(request.user, 'is_superuser', False):
                raise PermissionDenied('仅超级管理员可修改 is_staff')
            v = body.get('is_staff')
            vv = v if isinstance(v, bool) else _parse_bool(v)
            if vv is None:
                raise ValidationError({'is_staff': '必须为布尔值'})
            updates['is_staff'] = bool(vv)

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
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = Video.objects.all().select_related('user', 'category').prefetch_related('video_tags__tag')
        # base/media for URL building
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
                qs.update(status='published', published_at=timezone.now())
            else:
                qs.update(status=set_status, published_at=None)
        try:
            _audit(request, 'video.bulk_update', 'video', None, {'count': len(ids), 'affected': affected, 'fields': list(updates.keys()) + ((['status'] if set_status else []))})
        except Exception:
            pass
        return Response({'updated': int(affected)})


class AdminVideosBulkDeleteView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 500:
            raise ValidationError({'video_ids': '一次最多处理 500 个'})
        qs = Video.objects.filter(id__in=ids)
        affected = qs.count()
        qs.delete()
        try:
            _audit(request, 'video.bulk_delete', 'video', None, {'count': len(ids), 'affected': affected})
        except Exception:
            pass
        return Response({'removed': int(affected)})


class AdminVideosBatchApproveView(APIView):
    permission_classes = [permissions.IsAdminUser]

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
            qs.update(status='published', published_at=timezone.now(), transcode_error=None)
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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = Comment.objects.select_related('user', 'video').all()
        base = (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        u = request.user
        return Response({
            'id': str(getattr(u, 'id', '')),
            'username': getattr(u, 'username', ''),
            'is_staff': bool(getattr(u, 'is_staff', False)),
            'is_superuser': bool(getattr(u, 'is_superuser', False)),
        })


class AdminUserForceLogoutView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        cutoff = int(time.time())
        key = f"logout_after:{pk}"
        td = settings.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME')
        ttl = int(getattr(td, 'total_seconds')()) if td else 3600
        cache.set(key, cutoff, timeout=ttl)
        try:
            _audit(request, 'user.force_logout', 'user', str(pk), {'cutoff': cutoff})
        except Exception:
            pass
        return Response({'success': True, 'cutoff': cutoff})


class AdminAuditLogsListView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = AuditLog.objects.select_related('actor').all()
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


class AdminCategoriesListView(APIView):
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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
    permission_classes = [permissions.IsAdminUser]

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

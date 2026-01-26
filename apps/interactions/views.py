"""Interactions 视图模块。

用于实现互动相关的 API 视图，例如点赞、评论、收藏、关注关系等。
可结合 DRF 的 APIView/ViewSet 来定义接口，并在 urls 中进行路由绑定。
"""

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status, generics, serializers
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from django.db import IntegrityError
from django.db.models import Q, Exists, OuterRef, Value, IntegerField, Case, When, Count

from apps.interactions.models import Follow, Like, Favorite, History, Comment, Notification
from django.db import IntegrityError, transaction
from django.db.models import F, FloatField
import math
from uuid import UUID
from apps.users.models import User
from apps.users.serializers import UserPublicSerializer, UserFollowListSerializer
from apps.videos.models import Video, WatchLater
from django.conf import settings
from backend.common.pagination import StandardResultsSetPagination


class FollowCreateView(APIView):
    """关注接口

    - 方法：POST /api/interactions/follow/
    - 参数：user_id（被关注者）
    - 权限：需登录
    - 节流：follow（见 settings DEFAULT_THROTTLE_RATES）
    - 结果：如果已关注则幂等返回 200，否则创建关注返回 201
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'follow'

    def post(self, request):
        target_id = request.data.get('user_id')
        if not target_id:
            raise ValidationError({'user_id': '必填'})
        if str(target_id) == str(request.user.id):
            raise ValidationError({'user_id': '不能关注自己'})
        target = get_object_or_404(User, pk=target_id)
        # 抗并发：如发生唯一约束竞争，视为已关注（幂等）
        try:
            obj, created = Follow.objects.get_or_create(follower=request.user, followed=target)
        except IntegrityError:
            created = False
        # 计数维护已由 signals 处理（apps.interactions.signals）
        data = {'following': True, 'user_id': str(target.id)}
        return Response(data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnfollowView(APIView):
    """取关接口

    - 方法：POST /api/interactions/unfollow/
    - 参数：user_id（被取关者）
    - 权限：需登录
    - 节流：follow（与关注同额度）
    - 结果：总是返回 204（幂等）
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'follow'

    def post(self, request):
        target_id = request.data.get('user_id')
        if not target_id:
            raise ValidationError({'user_id': '必填'})
        if str(target_id) == str(request.user.id):
            raise ValidationError({'user_id': '不能取关自己'})
        Follow.objects.filter(follower=request.user, followed_id=target_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FollowersListView(generics.ListAPIView):
    """粉丝列表（关注我的人）

    - 方法：GET /api/interactions/followers/?user_id=<可选>
    - 若未提供 user_id 且已登录，则默认为当前用户；未登录又未提供则 400
    - 返回：分页用户公开资料
    - 权限：允许匿名（查看他人粉丝列表）
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = UserFollowListSerializer

    def get_queryset(self):
        uid = self.request.query_params.get('user_id')
        q = (self.request.query_params.get('q') or '').strip()
        order = (self.request.query_params.get('order') or 'latest').lower()
        user = None
        if uid:
            user = get_object_or_404(User, pk=uid)
        elif self.request.user and self.request.user.is_authenticated:
            user = self.request.user
        else:
            # 这里抛出校验错误，统一异常处理后会返回标准错误结构
            raise ValidationError({'user_id': '缺少 user_id 且未登录'})
        # 隐私访问控制
        viewer = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        if user != viewer:
            mode = getattr(user, 'privacy_mode', 'public')
            if mode == 'private':
                raise PermissionDenied('该用户的关注/粉丝列表不对外公开')
            if mode == 'friends_only':
                if not viewer:
                    raise PermissionDenied('仅对互相关注用户可见')
                is_mutual = (
                    Follow.objects.filter(follower=viewer, followed=user).exists() and
                    Follow.objects.filter(follower=user, followed=viewer).exists()
                )
                if not is_mutual:
                    raise PermissionDenied('仅对互相关注用户可见')
        # 取“我的粉丝”：谁关注了我 -> follower 集合
        qs = User.objects.filter(following__followed=user)
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(nickname__icontains=q) | Q(display_name__icontains=q))
        if order == 'earliest':
            return qs.order_by('following__created_at').distinct()
        if order == 'comprehensive' and self.request.user and self.request.user.is_authenticated:
            me = self.request.user
            qs = qs.annotate(
                m1=Exists(Follow.objects.filter(follower=me, followed=OuterRef('pk'))),
                m2=Exists(Follow.objects.filter(follower=OuterRef('pk'), followed=me)),
            ).annotate(
                mutual=Case(When(Q(m1=True) & Q(m2=True), then=Value(1)), default=Value(0), output_field=IntegerField())
            )
            return qs.order_by('-mutual', '-following__created_at').distinct()
        return qs.order_by('-following__created_at').distinct()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.request.user and self.request.user.is_authenticated:
            ids = Follow.objects.filter(follower=self.request.user).values_list('followed_id', flat=True)
            ctx['following_id_set'] = set(str(i) for i in ids)
            ids2 = Follow.objects.filter(followed=self.request.user).values_list('follower_id', flat=True)
            ctx['followers_of_me_id_set'] = set(str(i) for i in ids2)
        else:
            ctx['following_id_set'] = set()
            ctx['followers_of_me_id_set'] = set()
        return ctx


class FollowingListView(generics.ListAPIView):
    """关注列表（我关注的人）

    - 方法：GET /api/interactions/following/?user_id=<可选>
    - 若未提供 user_id 且已登录，则默认为当前用户；未登录又未提供则 400
    - 返回：分页用户公开资料
    - 权限：允许匿名（查看他人关注列表）
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = UserFollowListSerializer

    def get_queryset(self):
        uid = self.request.query_params.get('user_id')
        q = (self.request.query_params.get('q') or '').strip()
        order = (self.request.query_params.get('order') or 'latest').lower()
        user = None
        if uid:
            user = get_object_or_404(User, pk=uid)
        elif self.request.user and self.request.user.is_authenticated:
            user = self.request.user
        else:
            raise ValidationError({'user_id': '缺少 user_id 且未登录'})
        # 隐私访问控制
        viewer = self.request.user if (self.request.user and self.request.user.is_authenticated) else None
        if user != viewer:
            mode = getattr(user, 'privacy_mode', 'public')
            if mode == 'private':
                raise PermissionDenied('该用户的关注/粉丝列表不对外公开')
            if mode == 'friends_only':
                if not viewer:
                    raise PermissionDenied('仅对互相关注用户可见')
                is_mutual = (
                    Follow.objects.filter(follower=viewer, followed=user).exists() and
                    Follow.objects.filter(follower=user, followed=viewer).exists()
                )
                if not is_mutual:
                    raise PermissionDenied('仅对互相关注用户可见')
        # 取“我关注的人”：被我关注的用户 -> followed 集合
        qs = User.objects.filter(followers__follower=user)
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(nickname__icontains=q) | Q(display_name__icontains=q))
        if order == 'earliest':
            return qs.order_by('followers__created_at').distinct()
        if order == 'comprehensive' and self.request.user and self.request.user.is_authenticated:
            me = self.request.user
            qs = qs.annotate(
                m1=Exists(Follow.objects.filter(follower=me, followed=OuterRef('pk'))),
                m2=Exists(Follow.objects.filter(follower=OuterRef('pk'), followed=me)),
            ).annotate(
                mutual=Case(When(Q(m1=True) & Q(m2=True), then=Value(1)), default=Value(0), output_field=IntegerField())
            )
            return qs.order_by('-mutual', '-followers__created_at').distinct()
        return qs.order_by('-followers__created_at').distinct()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.request.user and self.request.user.is_authenticated:
            ids = Follow.objects.filter(follower=self.request.user).values_list('followed_id', flat=True)
            ctx['following_id_set'] = set(str(i) for i in ids)
            ids2 = Follow.objects.filter(followed=self.request.user).values_list('follower_id', flat=True)
            ctx['followers_of_me_id_set'] = set(str(i) for i in ids2)
        else:
            ctx['following_id_set'] = set()
            ctx['followers_of_me_id_set'] = set()
        return ctx

def _media_url(request, rel: str) -> str:
    # 优先使用请求 Host，避免 SITE_URL 与前端 Host 不一致导致跨域或 127.0.0.1 无法访问
    try:
        base = (request.build_absolute_uri('/') if request else (getattr(settings, 'SITE_URL', '') or '')).rstrip('/')
    except Exception:
        base = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    media = getattr(settings, 'MEDIA_URL', '/media').rstrip('/')
    if media.startswith('http://') or media.startswith('https://'):
        return f"{media}/{rel}"
    return f"{base}{media}/{rel}" if media.startswith('/') else f"{base}/{media}/{rel}"


def _ensure_privacy_access(owner: User, viewer: User | None):
    """根据用户的 privacy_mode 进行访问控制。
    - public: 任何人可见
    - private: 仅本人可见
    - friends_only: 仅互相关注可见
    """
    if owner == viewer:
        return
    mode = getattr(owner, 'privacy_mode', 'public')
    if mode == 'public':
        return
    if mode == 'private':
        raise PermissionDenied('该用户的内容仅自己可见')
    if mode == 'friends_only':
        if not viewer:
            raise PermissionDenied('仅对互相关注用户可见')
        is_mutual = (
            Follow.objects.filter(follower=viewer, followed=owner).exists() and
            Follow.objects.filter(follower=owner, followed=viewer).exists()
        )
        if not is_mutual:
            raise PermissionDenied('仅对互相关注用户可见')


class _BaseUserList(APIView):
    permission_classes = [permissions.AllowAny]

    def _resolve_user(self, request):
        uid = request.query_params.get('user_id')
        if uid:
            return get_object_or_404(User, pk=uid)
        if request.user and request.user.is_authenticated:
            return request.user
        raise ValidationError({'user_id': '缺少 user_id 且未登录'})

    def _paginate(self, request):
        try:
            page = int(request.query_params.get('page', '1'))
        except Exception:
            page = 1
        page = max(1, page)
        try:
            size = int(request.query_params.get('page_size', '12'))
        except Exception:
            size = 12
        size = min(50, max(1, size))
        return page, size


class LikesListView(_BaseUserList):
    def get(self, request):
        user = self._resolve_user(request)
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        _ensure_privacy_access(user, viewer)
        p = StandardResultsSetPagination()
        qs = Like.objects.filter(user=user).order_by('-created_at')
        rows = list(p.paginate_queryset(qs, request, view=self))
        # 批量取视频（仅必要字段）
        vid_ids = [r.video_id for r in rows]
        allow_all = bool(viewer and (str(viewer.id) == str(user.id) or getattr(viewer, 'is_staff', False)))
        base_qs = Video.objects.filter(id__in=vid_ids).only('id','title','view_count','like_count','thumbnail','thumbnail_f')
        if not allow_all:
            base_qs = base_qs.filter(visibility='public', status='published')
        vmap = {str(v.id): v for v in base_qs}
        items = []
        for r in rows:
            v = vmap.get(str(r.video_id))
            if not v:
                continue
            thumb = getattr(v.thumbnail_f, 'name', None) or v.thumbnail
            items.append({
                'id': str(v.id),
                'title': v.title,
                'cover': _media_url(request, thumb) if thumb else None,
                'views': v.view_count,
                'likes': v.like_count,
                'liked_at': r.created_at,
            })
        return Response({'results': items,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})


class RelationshipView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        uid = request.query_params.get('user_id')
        if not uid:
            raise ValidationError({'user_id': '必填'})
        target = get_object_or_404(User, pk=uid)
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        following = False  # 我是否关注对方
        followed_by = False  # 对方是否关注我
        if viewer:
            try:
                following = Follow.objects.filter(follower=viewer, followed=target).exists()
                followed_by = Follow.objects.filter(follower=target, followed=viewer).exists()
            except Exception:
                following = False; followed_by = False
        return Response({'user_id': str(target.id), 'following': bool(following), 'followed_by': bool(followed_by), 'mutual': bool(following and followed_by)})


class FavoritesListView(_BaseUserList):
    def get(self, request):
        user = self._resolve_user(request)
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        _ensure_privacy_access(user, viewer)
        p = StandardResultsSetPagination()
        qs = Favorite.objects.filter(user=user).order_by('-created_at')
        rows = list(p.paginate_queryset(qs, request, view=self))
        vid_ids = [r.video_id for r in rows]
        allow_all = bool(viewer and (str(viewer.id) == str(user.id) or getattr(viewer, 'is_staff', False)))
        base_qs = Video.objects.filter(id__in=vid_ids).only('id','title','view_count','like_count','thumbnail','thumbnail_f')
        if not allow_all:
            base_qs = base_qs.filter(visibility='public', status='published')
        vmap = {str(v.id): v for v in base_qs}
        items = []
        for r in rows:
            v = vmap.get(str(r.video_id))
            if not v:
                continue
            thumb = getattr(v.thumbnail_f, 'name', None) or v.thumbnail
            items.append({
                'id': str(v.id),
                'title': v.title,
                'cover': _media_url(request, thumb) if thumb else None,
                'views': v.view_count,
                'likes': v.like_count,
                'favorited_at': r.created_at,
            })
        return Response({'results': items,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})


class WatchLaterListView(_BaseUserList):
    def get(self, request):
        user = self._resolve_user(request)
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        _ensure_privacy_access(user, viewer)
        p = StandardResultsSetPagination()
        qs = WatchLater.objects.filter(user=user).order_by('-created_at')
        rows = list(p.paginate_queryset(qs, request, view=self))
        vid_ids = [r.video_id for r in rows]
        allow_all = bool(viewer and (str(viewer.id) == str(user.id) or getattr(viewer, 'is_staff', False)))
        base_qs = Video.objects.filter(id__in=vid_ids).only('id','title','view_count','like_count','thumbnail','thumbnail_f')
        if not allow_all:
            base_qs = base_qs.filter(visibility='public', status='published')
        vmap = {str(v.id): v for v in base_qs}
        items = []
        for r in rows:
            v = vmap.get(str(r.video_id))
            if not v:
                continue
            thumb = getattr(v.thumbnail_f, 'name', None) or v.thumbnail
            items.append({
                'id': str(v.id),
                'title': v.title,
                'cover': _media_url(request, thumb) if thumb else None,
                'views': v.view_count,
                'likes': v.like_count,
                'saved_at': r.created_at,
            })
        return Response({'results': items,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})


class HistoryListView(_BaseUserList):
    def get(self, request):
        user = self._resolve_user(request)
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        _ensure_privacy_access(user, viewer)
        p = StandardResultsSetPagination()
        qs = History.objects.filter(user=user).order_by('-created_at')
        rows = list(p.paginate_queryset(qs, request, view=self))
        vid_ids = [r.video_id for r in rows]
        allow_all = bool(viewer and (str(viewer.id) == str(user.id) or getattr(viewer, 'is_staff', False)))
        base_qs = Video.objects.filter(id__in=vid_ids).only('id','title','view_count','like_count','thumbnail','thumbnail_f')
        if not allow_all:
            base_qs = base_qs.filter(visibility='public', status='published')
        vmap = {str(v.id): v for v in base_qs}
        items = []
        for r in rows:
            v = vmap.get(str(r.video_id))
            if not v:
                continue
            thumb = getattr(v.thumbnail_f, 'name', None) or v.thumbnail
            items.append({
                'id': str(v.id),
                'title': v.title,
                'cover': _media_url(request, thumb) if thumb else None,
                'views': v.view_count,
                'likes': v.like_count,
                'watched_at': r.created_at,
                'progress': r.progress,
                'watch_duration': r.watch_duration,
            })
        return Response({'results': items,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})


class LikesBulkUnlikeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 200:
            raise ValidationError({'video_ids': '一次最多处理 200 个'})
        qs = Like.objects.filter(user=request.user, video_id__in=ids)
        removed, _ = qs.delete()
        return Response({'removed': int(removed)})


class FavoritesBulkRemoveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 200:
            raise ValidationError({'video_ids': '一次最多处理 200 个'})
        qs = Favorite.objects.filter(user=request.user, video_id__in=ids)
        removed, _ = qs.delete()
        return Response({'removed': int(removed)})


class WatchLaterBulkRemoveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 200:
            raise ValidationError({'video_ids': '一次最多处理 200 个'})
        qs = WatchLater.objects.filter(user=request.user, video_id__in=ids)
        removed, _ = qs.delete()
        return Response({'removed': int(removed)})


class HistoryBulkRemoveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('video_ids') or request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'video_ids': '必须为非空数组'})
        ids = [str(i) for i in ids if str(i)]
        if len(ids) > 200:
            raise ValidationError({'video_ids': '一次最多处理 200 个'})
        qs = History.objects.filter(user=request.user, video_id__in=ids)
        removed, _ = qs.delete()
        return Response({'removed': int(removed)})


class LikeToggleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        vid = request.data.get('video_id') or request.data.get('id')
        if not vid:
            raise ValidationError({'video_id': '必填'})
        # 校验 UUID 格式，避免 ValueError 导致 500
        try:
            UUID(str(vid))
        except Exception:
            raise ValidationError({'video_id': '格式不正确'})
        v = get_object_or_404(Video, pk=vid)
        # 未发布视频不允许此操作
        if getattr(v, 'status', '') != 'published':
            raise NotFound('资源不存在')
        # 私密视频仅作者/管理员可操作
        if getattr(v, 'visibility', 'public') == 'private':
            viewer = request.user
            if (str(viewer.id) != str(v.user_id)) and (not getattr(viewer, 'is_staff', False)):
                raise NotFound('资源不存在')
        obj = Like.objects.filter(user=request.user, video=v).first()
        liked = False
        if obj:
            obj.delete()
            liked = False
        else:
            try:
                Like.objects.create(user=request.user, video=v)
                liked = True
            except IntegrityError:
                liked = True
        # 读取最新点赞数
        fresh = Video.objects.filter(id=v.id).values_list('like_count', flat=True).first() or 0
        return Response({'liked': liked, 'like_count': int(fresh)})


class FavoriteToggleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        vid = request.data.get('video_id') or request.data.get('id')
        if not vid:
            raise ValidationError({'video_id': '必填'})
        v = get_object_or_404(Video, pk=vid)
        # 未发布视频不允许此操作
        if getattr(v, 'status', '') != 'published':
            raise NotFound('资源不存在')
        # 私密视频仅作者/管理员可操作
        if getattr(v, 'visibility', 'public') == 'private':
            viewer = request.user
            if (str(viewer.id) != str(v.user_id)) and (not getattr(viewer, 'is_staff', False)):
                raise NotFound('资源不存在')
        obj = Favorite.objects.filter(user=request.user, video=v).first()
        favorited = False
        if obj:
            obj.delete()
            favorited = False
        else:
            try:
                Favorite.objects.create(user=request.user, video=v)
                favorited = True
            except IntegrityError:
                favorited = True
        # 读取最新收藏数
        fresh = Favorite.objects.filter(video=v).count()
        return Response({'favorited': favorited, 'favorite_count': int(fresh)})


class HistoryRecordView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = 'history'

    def post(self, request):
        vid = request.data.get('video_id') or request.data.get('id')
        if not vid:
            raise ValidationError({'video_id': '必填'})
        v = get_object_or_404(Video, pk=vid)
        # 未发布视频不允许此操作
        if getattr(v, 'status', '') != 'published':
            raise NotFound('资源不存在')
        # 私密视频仅作者/管理员可写入观看记录
        if getattr(v, 'visibility', 'public') == 'private':
            viewer = request.user
            if (str(viewer.id) != str(v.user_id)) and (not getattr(viewer, 'is_staff', False)):
                raise NotFound('资源不存在')
        prog = request.data.get('progress')
        cur = request.data.get('current')
        dur = request.data.get('duration')
        wd = request.data.get('watch_duration')
        p = 0.0
        try:
            if prog is not None:
                p = float(prog)
            elif cur is not None and dur:
                p = float(cur) / float(dur)
        except Exception:
            p = 0.0
        if not (p >= 0):
            p = 0.0
        if p > 1:
            p = 1.0
        w = 0
        try:
            if wd is not None:
                w = int(wd)
            elif cur is not None:
                w = int(float(cur))
        except Exception:
            w = 0
        # 规整数值，避免 NaN/Inf 导致数据库约束异常
        try:
            if not math.isfinite(p):
                p = 0.0
        except Exception:
            p = 0.0
        try:
            if w is None or not isinstance(w, int):
                w = int(w or 0)
        except Exception:
            w = 0
        p = max(0.0, min(1.0, float(p)))
        w = max(0, int(w))

        try:
            # 先尝试创建，如已存在则走 UPDATE（取最大值）
            obj, created = History.objects.get_or_create(
                user=request.user,
                video=v,
                defaults={'progress': p, 'watch_duration': w},
            )
            if not created:
                History.objects.filter(user=request.user, video=v).update(
                    progress=Case(
                        When(progress__gte=p, then=F('progress')),
                        default=Value(p),
                        output_field=FloatField(),
                    ),
                    watch_duration=Case(
                        When(watch_duration__gte=w, then=F('watch_duration')),
                        default=Value(w),
                        output_field=IntegerField(),
                    ),
                )
            row = History.objects.filter(user=request.user, video=v).values('progress', 'watch_duration').first()
            prog_out = float(row.get('progress') or 0.0) if row else float(p)
            wd_out = int(row.get('watch_duration') or 0) if row else int(w)
            return Response({'ok': True, 'progress': prog_out, 'watch_duration': wd_out})
        except IntegrityError:
            # 并发极端情况：再次回退到 UPDATE，然后读取
            History.objects.filter(user=request.user, video=v).update(
                progress=Case(
                    When(progress__gte=p, then=F('progress')),
                    default=Value(p),
                    output_field=FloatField(),
                ),
                watch_duration=Case(
                    When(watch_duration__gte=w, then=F('watch_duration')),
                    default=Value(w),
                    output_field=IntegerField(),
                ),
            )
            row = History.objects.filter(user=request.user, video=v).values('progress', 'watch_duration').first()
            prog_out = float(row.get('progress') or 0.0) if row else float(p)
            wd_out = int(row.get('watch_duration') or 0) if row else int(w)
            return Response({'ok': True, 'progress': prog_out, 'watch_duration': wd_out})


class CommentSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    replies_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Comment
        fields = ['id', 'content', 'user', 'video', 'parent', 'created_at', 'updated_at', 'replies_count']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at', 'replies_count']

    def get_user(self, obj):
        u = getattr(obj, 'user', None)
        if not u:
            return None
        try:
            # 构造绝对头像 URL（优先文件字段，再退回缩略或原图），并对以 / 开头的路径进行基址拼接
            try:
                req = self.context.get('request') if hasattr(self, 'context') else None
            except Exception:
                req = None
            rel = (
                getattr(getattr(u, 'profile_picture_f', None), 'name', None)
                or getattr(u, 'profile_picture_thumb', None)
                or getattr(u, 'profile_picture', None)
            )
            avatar = None
            if rel:
                try:
                    s = str(rel)
                except Exception:
                    s = ''
                if s.startswith('http://') or s.startswith('https://'):
                    avatar = s
                elif s.startswith('/'):
                    try:
                        base = (getattr(settings, 'SITE_URL', '') or (req.build_absolute_uri('/') if req else '')).rstrip('/')
                    except Exception:
                        base = ''
                    avatar = f"{base}{s}" if base else s
                else:
                    try:
                        avatar = _media_url(req, s)
                    except Exception:
                        avatar = s
            return {
                'id': str(u.id),
                'username': getattr(u, 'username', '') or '',
                'display_name': getattr(u, 'display_name', '') or getattr(u, 'nickname', '') or '',
                'avatar_url': avatar,
            }
        except Exception:
            return None


class CommentCreateSerializer(serializers.Serializer):
    video_id = serializers.CharField()
    content = serializers.CharField(allow_blank=False, allow_null=False, max_length=2000)
    parent_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CommentsListCreateView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        vid = request.query_params.get('video_id')
        if not vid:
            raise ValidationError({'video_id': '缺少视频ID'})
        # 私密视频仅作者/管理员可查看评论列表
        v = get_object_or_404(Video, id=vid)
        if getattr(v, 'status', '') != 'published':
            raise NotFound('资源不存在')
        viewer = request.user if (request.user and request.user.is_authenticated) else None
        if getattr(v, 'visibility', 'public') == 'private':
            if (not viewer) or (str(viewer.id) != str(v.user_id) and not getattr(viewer, 'is_staff', False)):
                raise NotFound('资源不存在')
        p = StandardResultsSetPagination()
        qs = Comment.objects.filter(video_id=vid, parent__isnull=True).select_related('user').order_by('-created_at')
        qs = qs.annotate(replies_count=Count('replies'))
        page = p.paginate_queryset(qs, request, view=self)
        ser = CommentSerializer(page, many=True, context={'request': request})
        return Response({'results': ser.data,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            raise PermissionDenied('未登录')
        data = CommentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        video_id = data.validated_data['video_id']
        content = (data.validated_data['content'] or '').strip()
        parent_id = data.validated_data.get('parent_id') or None
        if not content:
            raise ValidationError({'content': '内容不能为空'})
        video = get_object_or_404(Video, id=video_id)
        if getattr(video, 'status', '') != 'published':
            raise NotFound('资源不存在')
        # 私密视频仅作者/管理员可评论
        if getattr(video, 'visibility', 'public') == 'private':
            viewer = request.user
            if (str(viewer.id) != str(video.user_id)) and (not getattr(viewer, 'is_staff', False)):
                raise NotFound('资源不存在')
        parent = None
        if parent_id:
            parent = get_object_or_404(Comment, id=parent_id)
            if str(parent.video_id) != str(video.id):
                raise ValidationError({'parent_id': '父评论不属于该视频'})
        # 若作者关闭评论，任何用户均不可评论（完全关闭）
        if not bool(getattr(video, 'allow_comments', True)):
            raise PermissionDenied('评论已关闭')
        c = Comment.objects.create(content=content, user=request.user, video=video, parent=parent)
        setattr(c, 'replies_count', 0)
        ser = CommentSerializer(c, context={'request': request})
        return Response(ser.data, status=status.HTTP_201_CREATED)


class CommentRepliesListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        pid = request.query_params.get('parent_id')
        if not pid:
            raise ValidationError({'parent_id': '缺少父评论ID'})
        parent = get_object_or_404(Comment, id=pid)
        # 校验所属视频可见性（私密仅作者/管理员）
        video = None
        try:
            video = parent.video
        except Exception:
            video = None
        if not video:
            vid = getattr(parent, 'video_id', None)
            if vid:
                video = get_object_or_404(Video, id=vid)
        if video and getattr(video, 'status', '') != 'published':
            raise NotFound('资源不存在')
        if video and getattr(video, 'visibility', 'public') == 'private':
            viewer = request.user if (request.user and request.user.is_authenticated) else None
            if (not viewer) or (str(viewer.id) != str(video.user_id) and not getattr(viewer, 'is_staff', False)):
                raise NotFound('资源不存在')
        p = StandardResultsSetPagination()
        qs = Comment.objects.filter(Q(parent_id=pid) | Q(parent__parent_id=pid)).select_related('user').order_by('created_at').annotate(replies_count=Value(0, output_field=IntegerField()))
        page = p.paginate_queryset(qs, request, view=self)
        ser = CommentSerializer(page, many=True, context={'request': request})
        return Response({'results': ser.data,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})


class CommentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, pk):
        c = get_object_or_404(Comment, id=pk)
        if (not request.user.is_staff) and (str(c.user_id) != str(request.user.id)):
            raise PermissionDenied('无权删除该评论')
        c.delete()
        return Response({'success': True})


class NotificationActorSerializer(serializers.Serializer):
    id = serializers.CharField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    avatar_url = serializers.CharField()


class NotificationItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    verb = serializers.CharField()
    read = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    actor = NotificationActorSerializer()
    video = serializers.DictField(required=False)
    comment = serializers.DictField(required=False)


class NotificationsListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        only_unread = str(request.query_params.get('unread') or '').lower() in ('1','true','yes')
        p = StandardResultsSetPagination()
        qs = Notification.objects.filter(user=request.user, hidden=False)
        if only_unread:
            qs = qs.filter(read=False)
        qs = qs.select_related('actor', 'video', 'comment').order_by('-created_at')
        rows = list(p.paginate_queryset(qs, request, view=self))
        out = []
        for n in rows:
            try:
                actor = getattr(n, 'actor', None)
                a = None
                if actor:
                    a = {
                        'id': str(actor.id),
                        'username': getattr(actor, 'username', '') or '',
                        'display_name': getattr(actor, 'display_name', '') or getattr(actor, 'nickname', '') or '',
                        'avatar_url': getattr(actor, 'profile_picture_thumb', '') or getattr(actor, 'profile_picture', '') or '',
                    }
                v = getattr(n, 'video', None)
                vobj = None
                if v:
                    vobj = {
                        'id': str(v.id),
                        'title': v.title,
                    }
                c = getattr(n, 'comment', None)
                cobj = None
                if c:
                    cobj = {
                        'id': str(c.id),
                        'content': (c.content or '')[:120],
                    }
                out.append({
                    'id': str(n.id),
                    'verb': n.verb,
                    'read': bool(n.read),
                    'created_at': n.created_at,
                    'actor': a,
                    'video': vobj,
                    'comment': cobj,
                })
            except Exception:
                continue
        return Response({'results': out,
                         'page': p.page.number,
                         'page_size': p.get_page_size(request),
                         'total': p.page.paginator.count,
                         'has_next': p.page.has_next()})


class NotificationsMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            raise ValidationError({'ids': '必须为非空数组'})
        qs = Notification.objects.filter(user=request.user, id__in=ids, read=False)
        updated = qs.update(read=True)
        return Response({'updated': int(updated)})


class NotificationsMarkAllReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        qs = Notification.objects.filter(user=request.user, read=False)
        updated = qs.update(read=True)
        return Response({'updated': int(updated)})


class NotificationsUnreadCountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        cnt = Notification.objects.filter(user=request.user, hidden=False, read=False).count()
        return Response({'unread': int(cnt)})


class NotificationsClearAllView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        qs = Notification.objects.filter(user=request.user, hidden=False)
        updated = qs.update(hidden=True, read=True)
        return Response({'updated': int(updated)})
